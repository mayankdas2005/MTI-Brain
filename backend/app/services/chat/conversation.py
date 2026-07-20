"""Service layer for conversation thread and project CRUD operations."""

import re
import uuid
from datetime import datetime, timezone

from app.core.logger import logger
from app.models.conversation import MTIBrainDashboard, MTIBrainMessage, MTIBrainProject, MTIBrainThread
from sqlalchemy import delete, exists, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession


# Filter predicates injected into raw ``text()`` queries are expected to be
# hardcoded literals like ``"t.user_id = :user_id"``. This regex permits
# only characters that appear in such literals (identifiers, dots, whitespace,
# parens for casts, comparison operators, commas, and ``:`` for bound params)
# and forbids quotes, dashes, semicolons, braces, etc. ``_safe_filter`` runs
# every predicate through it so a future commit can't slip an interpolated
# value into the WHERE clause.
_SAFE_FILTER_RE = re.compile(r"^[\w\s\.\(\)=<>!,:]+$")


def _safe_filter(predicate: str) -> str:
    """Validate that a SQL filter predicate contains only safe characters.

    Raises:
        ValueError: if the predicate contains anything that could carry
            a SQL injection (string literals, f-string remnants, etc.).
    """
    if not _SAFE_FILTER_RE.fullmatch(predicate):
        raise ValueError(f"Unsafe SQL filter predicate: {predicate!r}")
    return predicate


# ─── Threads ───


async def create_thread(
    db: AsyncSession,
    thread_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    title: str | None = None,
    user_id: uuid.UUID | None = None,
) -> MTIBrainThread:
    """Create a new conversation thread. 1 round-trip (INSERT).

    Args:
        db: Async database session.
        thread_id: Optional explicit thread ID; a random UUID is generated if None.
        project_id: Optional project to associate the thread with.
        title: Optional initial title for the thread.
        user_id: Owner user UUID.

    Returns:
        The newly created MTIBrainThread instance.
    """
    thread = MTIBrainThread(
        id=thread_id or uuid.uuid4(),
        project_id=project_id,
        title=title,
        user_id=user_id,
    )
    db.add(thread)
    await db.flush()
    logger.info(f"Thread created: {thread.id}")
    return thread


async def thread_exists(
    db: AsyncSession,
    thread_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> bool:
    """Check if a thread exists (optionally scoped to a user). 1 round-trip.

    Args:
        db: Async database session.
        thread_id: The thread identifier to check.
        user_id: Optional user filter.

    Returns:
        True if a thread with the given ID exists, False otherwise.
    """
    stmt = select(MTIBrainThread.id).where(MTIBrainThread.id == thread_id).limit(1)
    if user_id:
        stmt = stmt.where(MTIBrainThread.user_id == user_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None


async def get_thread(
    db: AsyncSession,
    thread_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> tuple[MTIBrainThread, list[MTIBrainMessage]] | tuple[None, None]:
    """Get thread with messages, excluding conversations with empty assistant responses.

    Uses a single query with a subquery filter. 1 round-trip.

    Args:
        db: Async database session.
        thread_id: The thread identifier to retrieve.
        user_id: Optional owner filter; when provided, threads owned by other
            users return as ``(None, None)``.

    Returns:
        A tuple of (MTIBrainThread, list[MTIBrainMessage]) if found, or (None, None)
        if the thread does not exist or the caller does not own it.
    """
    user_filter = _safe_filter("AND t.user_id = :uid") if user_id else ""
    params: dict = {"tid": str(thread_id)}
    if user_id:
        params["uid"] = str(user_id)

    # Single query: thread + filtered messages + per-message feedback.
    #
    # Performance notes:
    #   - valid_convos uses ix_mti_brain_message_thread_role (thread_id, role)
    #   - message join uses ix_mti_brain_message_conversation (conversation_id)
    #   - LATERAL feedback uses ix_mti_brain_feedback_message_created
    #     (message_id, created_at) for an indexed point-lookup per message.
    #     The old DISTINCT ON pattern had NO WHERE clause on mti_brain_feedback
    #     and therefore scanned the entire feedback table — catastrophic at scale.
    result = await db.execute(
        text(f"""
            WITH valid_convos AS (
                SELECT DISTINCT conversation_id
                FROM mti_brain_message
                WHERE thread_id = :tid
                  AND role = 'assistant'
                  AND (
                      (content IS NOT NULL AND content != '')
                      OR (metadata->>'stopped')::boolean = true
                  )
            )
            SELECT
                t.id, t.project_id, t.title, t.starred, t.created_at, t.updated_at,
                m.id AS msg_id, m.conversation_id, m.parent_conversation_id,
                m.role, m.content, m.reasoning, m.metadata, m.created_at AS msg_created_at,
                f.liked AS feedback_liked, f.comment AS feedback_comment
            FROM mti_brain_thread t
            LEFT JOIN mti_brain_message m
                ON m.thread_id = t.id
                AND m.conversation_id IN (SELECT conversation_id FROM valid_convos)
            LEFT JOIN LATERAL (
                SELECT liked, comment
                FROM mti_brain_feedback
                WHERE message_id = m.id
                ORDER BY created_at DESC
                LIMIT 1
            ) f ON m.id IS NOT NULL
            WHERE t.id = :tid {user_filter}
            ORDER BY m.created_at ASC,
                     CASE m.role
                         WHEN 'user' THEN 0
                         WHEN 'assistant' THEN 1
                         ELSE 2
                     END ASC
        """),
        params,
    )
    rows = result.fetchall()

    if not rows or rows[0].id is None:
        return None, None

    # Build thread from first row
    r = rows[0]
    thread = MTIBrainThread(
        id=r.id,
        project_id=r.project_id,
        title=r.title,
        starred=r.starred,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )

    # Build messages (skip if LEFT JOIN produced no messages)
    messages = []
    seen_msg_ids = set()
    for r in rows:
        if r.msg_id is not None and r.msg_id not in seen_msg_ids:
            seen_msg_ids.add(r.msg_id)
            msg = MTIBrainMessage(
                id=r.msg_id,
                thread_id=thread_id,
                conversation_id=r.conversation_id,
                parent_conversation_id=r.parent_conversation_id,
                role=r.role,
                content=r.content,
                reasoning=r.reasoning,
                metadata_=r.metadata,
                created_at=r.msg_created_at,
            )
            # Attach feedback if present
            msg._feedback_liked = r.feedback_liked
            msg._feedback_comment = r.feedback_comment
            messages.append(msg)

    return thread, messages


async def cleanup_thread_dashboards(thread_id: uuid.UUID) -> None:
    """Delete all S3 dashboard objects for a thread before DB CASCADE removes the rows.

    Called before delete_thread so the S3 objects are cleaned up first.
    DB rows are removed automatically by the ON DELETE CASCADE on thread_id.
    This is best-effort: S3 failures are logged but not re-raised.
    """
    import asyncio
    from app.db.session import async_read_session_factory
    from app.services.dashboard_builder import delete_from_s3

    try:
        async with async_read_session_factory() as session:
            result = await session.execute(
                select(MTIBrainDashboard.s3_key)
                .where(MTIBrainDashboard.thread_id == thread_id)
                .where(MTIBrainDashboard.s3_key != "")
            )
            keys = [row[0] for row in result.fetchall()]

        if keys:
            await asyncio.gather(*[delete_from_s3(k) for k in keys], return_exceptions=True)
            logger.info("dashboard cleanup: deleted %d S3 objects for thread=%s", len(keys), thread_id)
    except Exception as exc:
        logger.warning("dashboard cleanup: failed for thread=%s: %s", thread_id, exc)


async def delete_thread(
    db: AsyncSession,
    thread_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> bool:
    """Delete a thread and all its messages (cascade). 1 round-trip.

    Args:
        db: Async database session.
        thread_id: The thread identifier to delete.
        user_id: Optional user filter for ownership check.

    Returns:
        True if the thread was deleted, False if it did not exist.
    """
    await cleanup_thread_dashboards(thread_id)
    stmt = delete(MTIBrainThread).where(MTIBrainThread.id == thread_id)
    if user_id:
        stmt = stmt.where(MTIBrainThread.user_id == user_id)
    result = await db.execute(stmt)
    deleted = result.rowcount > 0
    if deleted:
        logger.info(f"Thread deleted: {thread_id}")
    return deleted


async def bulk_delete_threads(
    db: AsyncSession,
    thread_ids: list[uuid.UUID],
    user_id: uuid.UUID | None = None,
) -> int:
    """Delete multiple threads. 1 round-trip.

    Args:
        db: Async database session.
        thread_ids: List of thread identifiers to delete.
        user_id: Optional user filter for ownership check.

    Returns:
        The number of threads actually deleted.
    """
    stmt = delete(MTIBrainThread).where(MTIBrainThread.id.in_(thread_ids))
    if user_id:
        stmt = stmt.where(MTIBrainThread.user_id == user_id)
    result = await db.execute(stmt)
    return result.rowcount


async def star_thread(
    db: AsyncSession,
    thread_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> bool | None:
    """Toggle the starred flag on a thread. 1 round-trip (UPDATE RETURNING).

    Args:
        db: Async database session.
        thread_id: The thread identifier to star/unstar.
        user_id: Optional user filter for ownership check.

    Returns:
        The new starred boolean value, or None if the thread was not found.
    """
    stmt = (
        update(MTIBrainThread)
        .where(MTIBrainThread.id == thread_id)
        .values(starred=~MTIBrainThread.starred)
        .returning(MTIBrainThread.starred)
    )
    if user_id:
        stmt = stmt.where(MTIBrainThread.user_id == user_id)
    result = await db.execute(stmt)
    row = result.one_or_none()
    return row[0] if row else None


async def rename_thread(
    db: AsyncSession,
    thread_id: uuid.UUID,
    title: str,
    user_id: uuid.UUID | None = None,
) -> bool:
    """Rename a thread. 1 round-trip.

    Args:
        db: Async database session.
        thread_id: The thread identifier to rename.
        title: The new title (truncated to 500 characters).
        user_id: Optional user filter for ownership check.

    Returns:
        True if the thread was found and renamed, False otherwise.
    """
    stmt = (
        update(MTIBrainThread)
        .where(MTIBrainThread.id == thread_id)
        .values(title=title[:500], updated_at=datetime.now(timezone.utc))
    )
    if user_id:
        stmt = stmt.where(MTIBrainThread.user_id == user_id)
    result = await db.execute(stmt)
    return result.rowcount > 0


async def move_threads(
    db: AsyncSession,
    thread_ids: list[uuid.UUID],
    project_id: uuid.UUID | None,
    user_id: uuid.UUID | None = None,
) -> int:
    """Move threads to a project, or remove from a project. 1 round-trip.

    Args:
        db: Async database session.
        thread_ids: List of thread identifiers to move.
        project_id: Target project ID, or None to disassociate from any project.
        user_id: Optional user filter for ownership check.

    Returns:
        The number of threads that were updated.
    """
    stmt = (
        update(MTIBrainThread)
        .where(MTIBrainThread.id.in_(thread_ids))
        .values(project_id=project_id)
    )
    if user_id:
        stmt = stmt.where(MTIBrainThread.user_id == user_id)
    result = await db.execute(stmt)
    return result.rowcount


async def update_thread_title(
    db: AsyncSession,
    thread_id: uuid.UUID,
    title: str,
) -> None:
    """Auto-set title from first question (only if currently NULL). 1 round-trip."""
    await db.execute(
        update(MTIBrainThread)
        .where(MTIBrainThread.id == thread_id, MTIBrainThread.title.is_(None))
        .values(title=title[:500], updated_at=datetime.now(timezone.utc))
    )


async def touch_thread(db: AsyncSession, thread_id: uuid.UUID) -> None:
    """Bump the thread's updated_at timestamp to now. 1 round-trip.

    Args:
        db: Async database session.
        thread_id: The thread identifier to touch.

    Returns:
        None.
    """
    await db.execute(
        update(MTIBrainThread)
        .where(MTIBrainThread.id == thread_id)
        .values(updated_at=datetime.now(timezone.utc))
    )


async def save_message_and_touch(
    db: AsyncSession,
    thread_id: uuid.UUID,
    conversation_id: uuid.UUID,
    role: str,
    content: str,
    reasoning: str | None = None,
    metadata: dict | None = None,
    parent_conversation_id: uuid.UUID | None = None,
    auto_title: str | None = None,
    user_id: uuid.UUID | None = None,
) -> tuple[MTIBrainMessage, bool] | None:
    """Save a message, touch thread, and optionally auto-set title in one flush.

    Combines message insert, timestamp bump, and title coalesce in a
    single flush to minimise round-trips. When ``user_id`` is supplied, the
    thread UPDATE additionally filters on ``user_id`` and the function
    returns ``None`` if no matching thread is found — letting the caller
    issue a 404 without a separate thread_exists round-trip.

    Args:
        db: Async database session.
        thread_id: The thread to save the message to.
        conversation_id: The conversation this message belongs to.
        role: Message role (e.g. ``"user"`` or ``"assistant"``).
        content: The message text content.
        reasoning: Optional LLM reasoning/chain-of-thought text.
        metadata: Optional metadata dictionary stored with the message.
        parent_conversation_id: Optional parent conversation for retry/edit flows.
        auto_title: Optional title to set on the thread if it has no title yet.
        user_id: When provided, scopes the thread UPDATE so the call also
            serves as an ownership gate; returns None if the thread doesn't
            belong to this user (or doesn't exist).

    Returns:
        A tuple of (MTIBrainMessage, bool) where the bool is True when this is
        the first user message in the thread (no prior user messages exist),
        or ``None`` when ``user_id`` was passed and no matching thread was
        found.
    """
    # Combine thread touch + first-message check into a single round-trip
    # using a CTE. The UPDATE returns the matched row (if any) so we can
    # detect missing/unauthorised threads, and the EXISTS subquery runs in
    # the same statement.
    user_filter_sql = "AND user_id = :user_id" if user_id is not None else ""
    title_sql = (
        "title = COALESCE(title, :auto_title), " if auto_title else ""
    )
    combined = await db.execute(
        text(f"""
            WITH touch AS (
                UPDATE mti_brain_thread
                SET {title_sql}updated_at = :now
                WHERE id = :thread_id {user_filter_sql}
                RETURNING id
            )
            SELECT
                (SELECT count(*) FROM touch) AS touched,
                EXISTS(
                    SELECT 1 FROM mti_brain_message
                    WHERE thread_id = :thread_id AND role = 'user'
                ) AS has_prior_user_msg
        """),
        {
            "thread_id": str(thread_id),
            "now": datetime.now(timezone.utc),
            **({"user_id": str(user_id)} if user_id is not None else {}),
            **({"auto_title": auto_title[:500]} if auto_title else {}),
        },
    )
    row = combined.fetchone()
    if user_id is not None and (row is None or row.touched == 0):
        return None
    is_first_message = not row.has_prior_user_msg

    message = MTIBrainMessage(
        thread_id=thread_id,
        conversation_id=conversation_id,
        parent_conversation_id=parent_conversation_id,
        role=role,
        content=content,
        reasoning=reasoning,
        metadata_=metadata,
    )
    db.add(message)

    await db.flush()
    return message, is_first_message


async def list_threads(
    db: AsyncSession,
    project_id: uuid.UUID | None = None,
    limit: int = 20,
    offset: int = 0,
    user_id: uuid.UUID | None = None,
    starred: bool | None = None,
    label: str | None = None,
) -> list[dict]:
    """List threads with last message preview. 1 round-trip (single query).

    Uses LATERAL joins so the last-message and count subqueries only run
    for the threads returned after LIMIT/OFFSET, not across the entire
    mti_brain_message table.

    Args:
        db: Async database session.
        project_id: Optional project filter; returns all threads if None.
        limit: Maximum number of threads to return.
        offset: Pagination offset.
        user_id: Optional user filter; returns only threads owned by this user.
        starred: Optional starred filter; True returns only starred threads,
            False returns only non-starred, None returns both.

    Returns:
        A list of thread dictionaries containing id, project_id, title,
        starred, last_message preview, created_at, and updated_at.
    """
    filters: list[str] = []
    params: dict = {"limit": limit, "offset": offset}
    if user_id:
        filters.append(_safe_filter("t.user_id = :user_id"))
        params["user_id"] = str(user_id)
    if project_id:
        filters.append(_safe_filter("t.project_id = :project_id"))
        params["project_id"] = str(project_id)
    if starred is not None:
        filters.append(_safe_filter("t.starred = :starred"))
        params["starred"] = starred

    where_clause = ("WHERE " + " AND ".join(filters)) if filters else ""

    # When filtering by label, JOIN against the label table so the DB returns
    # only matching threads regardless of pagination offset. We still honour
    # LIMIT/OFFSET so callers can page through large label sets if needed.
    label_join = ""
    if label is not None:
        label_join = "JOIN mti_brain_thread_label tl ON tl.thread_id = t.id AND tl.label = :label_filter"
        if user_id:
            label_join += " AND tl.user_id = :user_id"
        params["label_filter"] = label

    result = await db.execute(
        text(f"""
            SELECT
                t.id, t.project_id, t.title, t.starred,
                t.created_at, t.updated_at,
                lm.content AS last_message
            FROM mti_brain_thread t
            {label_join}
            LEFT JOIN LATERAL (
                SELECT m.content
                FROM mti_brain_message m
                WHERE m.thread_id = t.id
                ORDER BY m.created_at DESC,
                         CASE m.role
                             WHEN 'assistant' THEN 0
                             WHEN 'user' THEN 1
                             ELSE 2
                         END ASC
                LIMIT 1
            ) lm ON true
            {where_clause}
            ORDER BY t.updated_at DESC
            LIMIT :limit OFFSET :offset
        """),
        params,
    )

    return [
        {
            "id": row.id,
            "project_id": row.project_id,
            "title": row.title,
            "starred": row.starred,
            "last_message": (row.last_message[:150] + "...") if row.last_message and len(row.last_message) > 150 else row.last_message,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
        for row in result.fetchall()
    ]


# ─── Full-text search ───


# Split tokens at digit-letter boundaries so "4week" is treated like "4 week"
# (and "week4" like "week 4") before the FTS parser sees it. Without this,
# PostgreSQL's default tokenizer keeps mixed alphanumeric runs as a single
# token, and the trigram path (>= 0.55) doesn't match either side of the
# boundary against the indexed words.
_DIGIT_LETTER_BOUNDARY = re.compile(r"(\d)([a-zA-Z])|([a-zA-Z])(\d)")


def _normalize_search(text: str) -> str:
    """Insert spaces at digit-letter boundaries (``"4week"`` → ``"4 week"``)."""
    return _DIGIT_LETTER_BOUNDARY.sub(
        lambda m: f"{m.group(1) or m.group(3)} {m.group(2) or m.group(4)}",
        text,
    )


def _build_search_sql(
    with_project: bool = False,
    with_user: bool = False,
    with_starred: bool = False,
) -> str:
    """Build thread search SQL with layered matching.

    Match layers (combined via OR):
      1. Full-text search on tsvector (stemming, English stopword removal).
      2. Exact substring on title/content.
      3. Per-word trigram fuzzy: every non-stopword in the query must
         word_similarity > 0.55 with some word in the target text. Catches
         typos like "volmue" -> "volume".

    Search words are pre-filtered to drop English stopwords (via
    ``to_tsvector('english')``) and tokens shorter than 3 characters, so
    queries like "the volume report" behave like "volume report" and the
    Layer 3 guard isn't satisfied vacuously by short or common words.

    Results are ordered by ``rank DESC`` (relevance first), with
    ``updated_at`` as the recency tiebreaker. Rows below ``rank > 0.4``
    are dropped as noise.

    Args:
        with_project: If True, include a ``project_id`` filter clause in the SQL.
        with_user: If True, include a ``user_id`` filter clause in the SQL.

    Returns:
        The raw SQL string for the thread search query.
    """
    project_filter_thread = _safe_filter("AND t.project_id = :project_id") if with_project else ""
    project_filter_msg = _safe_filter("AND t.project_id = :project_id") if with_project else ""
    user_filter_thread = _safe_filter("AND t.user_id = CAST(:user_id AS uuid)") if with_user else ""
    user_filter_msg = _safe_filter("AND t.user_id = CAST(:user_id AS uuid)") if with_user else ""
    starred_filter_thread = _safe_filter("AND t.starred = :starred") if with_starred else ""
    starred_filter_msg = _safe_filter("AND t.starred = :starred") if with_starred else ""

    return f"""
WITH params AS (
    SELECT
        websearch_to_tsquery('english', :search_text) AS q,
        :search_text AS raw_text,
        ARRAY(
            SELECT word
            FROM unnest(string_to_array(lower(trim(:search_text)), ' ')) AS word
            WHERE length(word) >= 3
              AND to_tsvector('english', word)::text != ''
        ) AS search_words
),
-- Helper: ALL search words must fuzzy-match (word_similarity > 0.55)
-- at least one word in the target text. Stopwords are already filtered
-- out of search_words by the params CTE.
thread_hits AS (
    SELECT
        t.id AS thread_id, t.project_id, t.title, t.created_at, t.updated_at,
        t.starred,
        (SELECT m.id FROM mti_brain_message m
         WHERE m.thread_id = t.id AND m.role = 'user'
         ORDER BY m.created_at ASC LIMIT 1) AS message_id,
        NULL::text AS matched_content, NULL::text AS headline,
        'thread' AS match_type,
        -- Words from the title that fuzzy/exact-matched any search word.
        -- Lets the frontend highlight what FTS/Levenshtein actually hit
        -- (e.g. "stress" when the user typed "stressss"). Empty array
        -- when only Layer 1 (full-text/stemming) matched.
        COALESCE(
            ARRAY(
                SELECT DISTINCT tw.word
                FROM unnest(string_to_array(lower(t.title), ' ')) AS tw(word)
                WHERE EXISTS (
                    SELECT 1 FROM unnest(p.search_words) AS sw(word)
                    WHERE word_similarity(sw.word, tw.word) > 0.55
                       OR (length(sw.word) >= 4
                           AND levenshtein_less_equal(sw.word, tw.word, 2)
                                <= (CASE WHEN length(sw.word) <= 5 THEN 1 ELSE 2 END))
                       OR tw.word ILIKE '%' || sw.word || '%'
                )
            ),
            ARRAY[]::text[]
        ) AS matched_terms,
        COALESCE(ts_rank(t.search_vector, q), 0) * 1.5
            + COALESCE(word_similarity(raw_text, t.title), 0) * 0.8
            + CASE WHEN t.title ILIKE '%' || raw_text || '%' THEN 1.0 ELSE 0 END AS rank
    FROM mti_brain_thread t CROSS JOIN params p
    WHERE (
        -- Layer 1: full-text search
        t.search_vector @@ q
        -- Layer 2: exact substring
        OR t.title ILIKE '%' || p.raw_text || '%'
        -- Layer 3: per-word fuzzy match (typo-tolerant). Each search word
        -- matches a target word if EITHER trigram similarity is high OR edit
        -- distance is small (Levenshtein, length-bounded). No `%` pre-filter:
        -- it was too strict on length-mismatched query/content pairs (e.g.
        -- one-word query vs. multi-sentence message diluted overall similarity
        -- below 0.3). The NOT EXISTS short-circuits cheaply on its own.
        OR (
            array_length(p.search_words, 1) > 0
            AND NOT EXISTS (
                SELECT 1 FROM unnest(p.search_words) AS sw(word)
                WHERE NOT EXISTS (
                    SELECT 1 FROM unnest(string_to_array(lower(t.title), ' ')) AS tw(word)
                    WHERE word_similarity(sw.word, tw.word) > 0.55
                       OR (length(sw.word) >= 4
                           AND levenshtein_less_equal(sw.word, tw.word, 2)
                                <= (CASE WHEN length(sw.word) <= 5 THEN 1 ELSE 2 END))
                  )
            )
        )
    )
    {project_filter_thread}
    {user_filter_thread}
    {starred_filter_thread}
),
message_hits AS (
    SELECT
        m.thread_id, t.project_id, t.title, t.created_at, t.updated_at,
        t.starred,
        m.id AS message_id,
        m.content AS matched_content,
        ts_headline('english', m.content, q,
            'MaxWords=60, MinWords=30, ShortWord=3, HighlightAll=false, StartSel=<b>, StopSel=</b>'
        ) AS headline,
        'message' AS match_type,
        -- Same fuzzy/exact word extraction as thread_hits, but applied to
        -- the matched message body. Used for fuzzy/typo highlighting that
        -- ts_headline can't catch (it only highlights FTS-matched tokens).
        COALESCE(
            ARRAY(
                SELECT DISTINCT mw.word
                FROM unnest(string_to_array(lower(m.content), ' ')) AS mw(word)
                WHERE EXISTS (
                    SELECT 1 FROM unnest(p.search_words) AS sw(word)
                    WHERE word_similarity(sw.word, mw.word) > 0.55
                       OR (length(sw.word) >= 4
                           AND levenshtein_less_equal(sw.word, mw.word, 2)
                                <= (CASE WHEN length(sw.word) <= 5 THEN 1 ELSE 2 END))
                       OR mw.word ILIKE '%' || sw.word || '%'
                )
            ),
            ARRAY[]::text[]
        ) AS matched_terms,
        COALESCE(ts_rank(m.search_vector, q), 0) * 1.0
            + COALESCE(word_similarity(p.raw_text, m.content), 0) * 0.6
            + CASE WHEN m.content ILIKE '%' || p.raw_text || '%' THEN 0.8 ELSE 0 END AS rank
    FROM mti_brain_message m
    JOIN mti_brain_thread t ON t.id = m.thread_id
    CROSS JOIN params p
    WHERE m.role = 'user'
      AND EXISTS (
          SELECT 1 FROM mti_brain_message a
          WHERE a.conversation_id = m.conversation_id
            AND a.role = 'assistant'
            AND a.content IS NOT NULL
            AND a.content != ''
      )
      AND (
        m.search_vector @@ q
        OR m.content ILIKE '%' || p.raw_text || '%'
        -- Layer 3: per-word fuzzy match (typo-tolerant). No `%` pre-filter:
        -- with long messages and short queries, the symmetric `%` similarity
        -- gets diluted below threshold even when individual words match well.
        OR (
            array_length(p.search_words, 1) > 0
            AND NOT EXISTS (
                SELECT 1 FROM unnest(p.search_words) AS sw(word)
                WHERE NOT EXISTS (
                    SELECT 1 FROM unnest(string_to_array(lower(m.content), ' ')) AS tw(word)
                    WHERE word_similarity(sw.word, tw.word) > 0.55
                       OR (length(sw.word) >= 4
                           AND levenshtein_less_equal(sw.word, tw.word, 2)
                                <= (CASE WHEN length(sw.word) <= 5 THEN 1 ELSE 2 END))
                  )
            )
        )
      )
      {project_filter_msg}
      {user_filter_msg}
      {starred_filter_msg}
),
all_hits AS (
    SELECT * FROM thread_hits UNION ALL SELECT * FROM message_hits
),
best_per_thread AS (
    SELECT DISTINCT ON (thread_id)
        thread_id, project_id, title, starred, created_at, updated_at,
        message_id, match_type, matched_content, headline, matched_terms, rank
    FROM all_hits
    WHERE rank > 0.4
    ORDER BY thread_id, rank DESC
)
SELECT thread_id, project_id, title, starred, created_at, updated_at,
    message_id, match_type, matched_content, headline, matched_terms, rank,
    CASE WHEN match_type = 'thread' THEN title
         ELSE COALESCE(headline, LEFT(matched_content, 120))
    END AS preview
FROM best_per_thread
ORDER BY rank DESC, updated_at DESC
LIMIT :limit OFFSET :offset
"""


def _search_sql_for(
    with_project: bool, with_user: bool, with_starred: bool,
):
    return text(_build_search_sql(
        with_project=with_project,
        with_user=with_user,
        with_starred=with_starred,
    ))


# Pre-build every variant once at module load. Cheap (~8 strings); avoids
# per-request SQL string concatenation cost.
_SEARCH_SQL_VARIANTS: dict[tuple[bool, bool, bool], object] = {
    (p, u, s): _search_sql_for(with_project=p, with_user=u, with_starred=s)
    for p in (False, True)
    for u in (False, True)
    for s in (False, True)
}


async def search_threads(
    db: AsyncSession,
    search_text: str,
    project_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    limit: int = 20,
    offset: int = 0,
    starred: bool | None = None,
) -> list[dict]:
    """Full-text search across threads and messages. 1 round-trip.

    Args:
        db: Async database session.
        search_text: The user's search query string.
        project_id: Optional project filter; searches all projects if None.
        user_id: Optional user filter; searches only this user's threads.
        limit: Maximum number of results to return.
        offset: Pagination offset.
        starred: Optional starred filter; True returns only starred threads,
            False only non-starred, None returns both.

    Returns:
        A list of result dictionaries with thread_id, project_id, title,
        starred, match_type, preview, headline, matched_terms, rank,
        created_at, and updated_at.
    """
    if len(search_text.strip()) < 2:
        return []

    search_text = _normalize_search(search_text)
    params: dict = {"search_text": search_text, "limit": limit, "offset": offset}
    if project_id is not None:
        params["project_id"] = str(project_id)
    if user_id is not None:
        params["user_id"] = str(user_id)
    if starred is not None:
        params["starred"] = starred

    query = _SEARCH_SQL_VARIANTS[
        (project_id is not None, user_id is not None, starred is not None)
    ]

    result = await db.execute(query, params)
    return [
        {
            "thread_id": row.thread_id,
            "project_id": row.project_id,
            "title": row.title,
            "starred": row.starred,
            "message_id": row.message_id,
            "match_type": row.match_type,
            "preview": row.preview,
            "headline": row.headline,
            "matched_terms": list(row.matched_terms) if row.matched_terms else [],
            "rank": round(float(row.rank), 3),
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
        for row in result.fetchall()
    ]


# ─── Messages ───


async def get_question_text(db: AsyncSession, conversation_id: uuid.UUID) -> str | None:
    """Get user question text for a conversation. 1 round-trip."""
    result = await db.execute(
        select(MTIBrainMessage.content)
        .where(
            MTIBrainMessage.conversation_id == conversation_id, MTIBrainMessage.role == "user"
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_question_and_parent(
    db: AsyncSession,
    conversation_id: uuid.UUID,
) -> tuple[str | None, uuid.UUID | None]:
    """Get question text and parent conversation ID in 1 round-trip.

    Used for retry/edit flows to retrieve the original question and its lineage.

    Args:
        db: Async database session.
        conversation_id: The conversation identifier to look up.

    Returns:
        A tuple of (question_content, parent_conversation_id), or (None, None)
        if the conversation was not found.
    """
    result = await db.execute(
        select(MTIBrainMessage.content, MTIBrainMessage.parent_conversation_id)
        .where(
            MTIBrainMessage.conversation_id == conversation_id, MTIBrainMessage.role == "user"
        )
        .limit(1)
    )
    row = result.one_or_none()
    if not row:
        return None, None
    return row.content, row.parent_conversation_id


async def is_first_conversation(
    db: AsyncSession,
    thread_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> bool:
    """Check if a conversation is the first one in a thread (oldest user message).

    Args:
        db: Async database session.
        thread_id: The thread identifier to check within.
        conversation_id: The conversation identifier to test.

    Returns:
        True if the given conversation holds the earliest user message in the
        thread, False otherwise.
    """
    result = await db.execute(
        select(MTIBrainMessage.conversation_id)
        .where(MTIBrainMessage.thread_id == thread_id, MTIBrainMessage.role == "user")
        .order_by(MTIBrainMessage.created_at.asc())
        .limit(1)
    )
    first_conv = result.scalar_one_or_none()
    return first_conv == conversation_id


async def get_conversation_history(
    db: AsyncSession,
    thread_id: uuid.UUID,
    before_conversation_id: uuid.UUID | None = None,
    max_pairs: int = 3,
) -> str:
    """Load recent Q/A pairs from DB for LLM conversation context.

    Groups by conversation_id (each has one user + one assistant message).
    If before_conversation_id is given, returns pairs BEFORE that
    conversation (for retry/edit). Otherwise returns the latest pairs
    in the thread (for new ask).

    Returns a formatted string like:
        User: <question>
        Assistant: <answer>
        ...

    Returns "(no prior context)" if no prior complete pairs exist.
    """
    from sqlalchemy import and_
    from sqlalchemy.orm import aliased

    UserMsg = aliased(MTIBrainMessage)
    AsstMsg = aliased(MTIBrainMessage)

    # Join user and assistant messages on conversation_id to get complete pairs
    # Only include ORIGINAL conversations (not retries/edits) — like ChatGPT/Claude
    pair_q = (
        select(
            UserMsg.content.label("question"),
            AsstMsg.content.label("answer"),
            UserMsg.created_at.label("asked_at"),
        )
        .select_from(UserMsg)
        .join(
            AsstMsg,
            and_(
                AsstMsg.conversation_id == UserMsg.conversation_id,
                AsstMsg.role == "assistant",
            ),
        )
        .where(UserMsg.thread_id == thread_id)
        .where(UserMsg.role == "user")
        .where(UserMsg.parent_conversation_id.is_(None))
        .where(UserMsg.content.isnot(None))
        .where(UserMsg.content != "")
        .where(AsstMsg.content.isnot(None))
        .where(AsstMsg.content != "")
    )

    if before_conversation_id is not None:
        # Get the timestamp of the target conversation's user message
        cutoff_row = await db.execute(
            select(MTIBrainMessage.created_at)
            .where(MTIBrainMessage.thread_id == thread_id)
            .where(MTIBrainMessage.conversation_id == before_conversation_id)
            .where(MTIBrainMessage.role == "user")
            .limit(1)
        )
        cutoff_ts = cutoff_row.scalar_one_or_none()
        if cutoff_ts is None:
            return "(no prior context)"
        pair_q = pair_q.where(UserMsg.created_at < cutoff_ts)

    # Get last N complete pairs ordered by when user asked
    rows = await db.execute(
        pair_q.order_by(UserMsg.created_at.desc()).limit(max_pairs)
    )
    pairs = rows.all()
    if not pairs:
        return "(no prior context)"

    # Reverse to chronological order and format
    pairs = list(reversed(pairs))
    lines = []
    for question, answer, _ in pairs:
        lines.append(f"User: {(question or '')[:200]}")
        lines.append(f"Assistant: {(answer or '')[:1500]}")
    return "\n".join(lines)


async def delete_from_conversation(
    db: AsyncSession,
    thread_id: uuid.UUID,
    conversation_id: uuid.UUID,
    *,
    inclusive: bool = True,
) -> int:
    """Delete messages from a conversation point forward in a thread.

    Used by retry/edit to implement truncation: everything at or after
    the target conversation's timestamp is removed before the new response
    is generated. Single CTE — avoids a separate SELECT round-trip.
    """
    op_sql = ">=" if inclusive else ">"
    result = await db.execute(
        text(f"""
            WITH conv_cutoff AS (
                SELECT min(created_at) AS cutoff
                FROM mti_brain_message
                WHERE thread_id = :tid AND conversation_id = :cid
            )
            DELETE FROM mti_brain_message
            WHERE thread_id = :tid
              AND created_at {op_sql} (SELECT cutoff FROM conv_cutoff)
        """),
        {"tid": str(thread_id), "cid": str(conversation_id)},
    )
    deleted = result.rowcount
    logger.info(
        f"Truncated thread {thread_id} from conversation {conversation_id} "
        f"({'inclusive' if inclusive else 'exclusive'}): {deleted} messages deleted"
    )
    return deleted


async def save_message(
    db: AsyncSession,
    thread_id: uuid.UUID,
    conversation_id: uuid.UUID,
    role: str,
    content: str,
    reasoning: str | None = None,
    metadata: dict | None = None,
    parent_conversation_id: uuid.UUID | None = None,
) -> MTIBrainMessage:
    """Save a single message to a thread. 1 round-trip (INSERT).

    Args:
        db: Async database session.
        thread_id: The thread to save the message to.
        conversation_id: The conversation this message belongs to.
        role: Message role (e.g. ``"user"`` or ``"assistant"``).
        content: The message text content.
        reasoning: Optional LLM reasoning/chain-of-thought text.
        metadata: Optional metadata dictionary stored with the message.
        parent_conversation_id: Optional parent conversation for retry/edit flows.

    Returns:
        The newly created MTIBrainMessage instance.
    """
    message = MTIBrainMessage(
        thread_id=thread_id,
        conversation_id=conversation_id,
        parent_conversation_id=parent_conversation_id,
        role=role,
        content=content,
        reasoning=reasoning,
        metadata_=metadata,
    )
    db.add(message)
    await db.flush()
    return message


# ─── Projects ───


async def create_project(
    db: AsyncSession,
    name: str,
    description: str | None = None,
    user_id: uuid.UUID | None = None,
) -> MTIBrainProject:
    """Create a new project. 1 round-trip (INSERT).

    Args:
        db: Async database session.
        name: The project name.
        description: Optional project description.
        user_id: Owner user UUID.

    Returns:
        The newly created MTIBrainProject instance.
    """
    project = MTIBrainProject(name=name, description=description, user_id=user_id)
    db.add(project)
    await db.flush()
    logger.info(f"Project created: {project.id} ({name})")
    return project


async def get_project(
    db: AsyncSession,
    project_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> dict | None:
    """Get a project with its thread list. 1 round-trip (single JOIN query).

    Args:
        db: Async database session.
        project_id: The project identifier to retrieve.
        user_id: Optional user filter for ownership check.

    Returns:
        A dictionary with project details and a nested threads list, or None
        if the project does not exist.
    """
    user_filter = _safe_filter("AND p.user_id = :user_id") if user_id else ""
    params: dict = {"pid": str(project_id)}
    if user_id:
        params["user_id"] = str(user_id)

    result = await db.execute(
        text(f"""
            SELECT
                p.id, p.name, p.description, p.starred, p.created_at, p.updated_at,
                t.id AS thread_id, t.title AS thread_title, t.starred AS thread_starred,
                t.created_at AS thread_created_at, t.updated_at AS thread_updated_at
            FROM mti_brain_project p
            LEFT JOIN LATERAL (
                SELECT id, title, starred, created_at, updated_at
                FROM mti_brain_thread
                WHERE project_id = p.id
                ORDER BY updated_at DESC
                LIMIT 100
            ) t ON true
            WHERE p.id = :pid {user_filter}
            ORDER BY t.updated_at DESC
        """),
        params,
    )
    rows = result.fetchall()
    if not rows or rows[0].id is None:
        return None

    r = rows[0]
    threads = [
        {
            "id": row.thread_id,
            "title": row.thread_title,
            "starred": row.thread_starred,
            "created_at": row.thread_created_at,
            "updated_at": row.thread_updated_at,
        }
        for row in rows
        if row.thread_id is not None
    ]
    return {
        "id": r.id,
        "name": r.name,
        "description": r.description,
        "starred": r.starred,
        "threads": threads,
        "created_at": r.created_at,
        "updated_at": r.updated_at,
    }


_PROJECT_SEARCH_SQL = text("""
    WITH params AS (
        SELECT
            websearch_to_tsquery('english', :search_text) AS q,
            :search_text AS raw_text,
            ARRAY(
                SELECT word
                FROM unnest(string_to_array(lower(trim(:search_text)), ' ')) AS word
                WHERE length(word) >= 3
                  AND to_tsvector('english', word)::text != ''
            ) AS search_words,
            -- Whitespace-collapsed lowercase form, used for Layer 4
            -- so "kitkat" matches "Kit Kat" and vice versa.
            regexp_replace(lower(trim(:search_text)), '\\s+', '', 'g') AS smushed_text
    ),
    matched AS (
        SELECT
            p.id, p.name, p.description, p.starred,
            p.created_at, p.updated_at,
            GREATEST(
                COALESCE(ts_rank(p.search_vector, params.q), 0),
                COALESCE(word_similarity(params.raw_text, p.name), 0),
                COALESCE(word_similarity(params.raw_text, COALESCE(p.description, '')), 0)
            ) AS sim_score
        FROM mti_brain_project p, params
        WHERE (
            -- Layer 1: full-text search on (name + description) tsvector
            p.search_vector @@ params.q
            -- Layer 2: exact substring (uses trigram GIN on name/description)
            OR p.name ILIKE '%' || params.raw_text || '%'
            OR p.description ILIKE '%' || params.raw_text || '%'
            -- Layer 4: whitespace-collapsed comparison for short labels.
            -- Handles concatenation and noise typos that per-word Layer 3
            -- can't see because of the space boundary:
            --   "kitkat"   ↔ "Kit Kat"  (smushed substring, either direction)
            --   "kitkattt" → "Kit Kat"  (search contains smushed name)
            --   "kkitakat" → "Kit Kat"  (Levenshtein on smushed name)
            OR (params.smushed_text != '' AND (
                replace(lower(p.name), ' ', '') ILIKE '%' || params.smushed_text || '%'
                OR replace(lower(COALESCE(p.description, '')), ' ', '') ILIKE '%' || params.smushed_text || '%'
                OR (length(replace(lower(p.name), ' ', '')) >= 3
                    AND params.smushed_text ILIKE '%' || replace(lower(p.name), ' ', '') || '%')
                OR (length(params.smushed_text) >= 4
                    AND length(replace(lower(p.name), ' ', '')) >= 4
                    AND levenshtein_less_equal(params.smushed_text, replace(lower(p.name), ' ', ''), 3)
                        <= GREATEST(2, length(params.smushed_text) / 3))
            ))
            -- Layer 3: per-word trigram fuzzy (>= 0.55), pre-filtered by `%`
            -- so the per-word loop only runs on rows with overall trigram similarity.
            OR (
                array_length(params.search_words, 1) > 0
                AND NOT EXISTS (
                    SELECT 1 FROM unnest(params.search_words) AS sw(word)
                    WHERE NOT EXISTS (
                        SELECT 1 FROM unnest(string_to_array(
                            lower(p.name || ' ' || COALESCE(p.description, '')), ' '
                        )) AS tw(word)
                        WHERE word_similarity(sw.word, tw.word) > 0.55
                           OR (length(sw.word) >= 4
                               AND levenshtein_less_equal(sw.word, tw.word, 2)
                                    <= (CASE WHEN length(sw.word) <= 5 THEN 1 ELSE 2 END))
                    )
                )
            )
        )
    )
    SELECT
        m.id, m.name, m.description, m.starred,
        m.created_at, m.updated_at,
        (SELECT count(*) FROM mti_brain_thread t WHERE t.project_id = m.id) AS thread_count,
        m.sim_score
    FROM matched m
    ORDER BY m.sim_score DESC, m.updated_at DESC
""")


_PROJECT_SEARCH_SQL_WITH_USER = text("""
    WITH params AS (
        SELECT
            websearch_to_tsquery('english', :search_text) AS q,
            :search_text AS raw_text,
            ARRAY(
                SELECT word
                FROM unnest(string_to_array(lower(trim(:search_text)), ' ')) AS word
                WHERE length(word) >= 3
                  AND to_tsvector('english', word)::text != ''
            ) AS search_words,
            regexp_replace(lower(trim(:search_text)), '\\s+', '', 'g') AS smushed_text
    ),
    matched AS (
        SELECT
            p.id, p.name, p.description, p.starred,
            p.created_at, p.updated_at,
            GREATEST(
                COALESCE(ts_rank(p.search_vector, params.q), 0),
                COALESCE(word_similarity(params.raw_text, p.name), 0),
                COALESCE(word_similarity(params.raw_text, COALESCE(p.description, '')), 0)
            ) AS sim_score
        FROM mti_brain_project p, params
        WHERE p.user_id = CAST(:user_id AS uuid)
          AND (
            -- Layer 1: full-text search
            p.search_vector @@ params.q
            -- Layer 2: exact substring (uses trigram GIN)
            OR p.name ILIKE '%' || params.raw_text || '%'
            OR p.description ILIKE '%' || params.raw_text || '%'
            -- Layer 4: whitespace-collapsed comparison for short labels.
            -- Handles concatenation and noise typos that per-word Layer 3
            -- can't see because of the space boundary:
            --   "kitkat"   ↔ "Kit Kat"  (smushed substring, either direction)
            --   "kitkattt" → "Kit Kat"  (search contains smushed name)
            --   "kkitakat" → "Kit Kat"  (Levenshtein on smushed name)
            OR (params.smushed_text != '' AND (
                replace(lower(p.name), ' ', '') ILIKE '%' || params.smushed_text || '%'
                OR replace(lower(COALESCE(p.description, '')), ' ', '') ILIKE '%' || params.smushed_text || '%'
                OR (length(replace(lower(p.name), ' ', '')) >= 3
                    AND params.smushed_text ILIKE '%' || replace(lower(p.name), ' ', '') || '%')
                OR (length(params.smushed_text) >= 4
                    AND length(replace(lower(p.name), ' ', '')) >= 4
                    AND levenshtein_less_equal(params.smushed_text, replace(lower(p.name), ' ', ''), 3)
                        <= GREATEST(2, length(params.smushed_text) / 3))
            ))
            -- Layer 3: per-word trigram fuzzy (>= 0.55), pre-filtered by `%`
            OR (
                array_length(params.search_words, 1) > 0
                AND NOT EXISTS (
                    SELECT 1 FROM unnest(params.search_words) AS sw(word)
                    WHERE NOT EXISTS (
                        SELECT 1 FROM unnest(string_to_array(
                            lower(p.name || ' ' || COALESCE(p.description, '')), ' '
                        )) AS tw(word)
                        WHERE word_similarity(sw.word, tw.word) > 0.55
                           OR (length(sw.word) >= 4
                               AND levenshtein_less_equal(sw.word, tw.word, 2)
                                    <= (CASE WHEN length(sw.word) <= 5 THEN 1 ELSE 2 END))
                    )
                )
            )
        )
    )
    SELECT
        m.id, m.name, m.description, m.starred,
        m.created_at, m.updated_at,
        (SELECT count(*) FROM mti_brain_thread t WHERE t.project_id = m.id) AS thread_count,
        m.sim_score
    FROM matched m
    ORDER BY m.sim_score DESC, m.updated_at DESC
""")


async def list_projects(
    db: AsyncSession,
    search: str | None = None,
    user_id: uuid.UUID | None = None,
) -> list[dict]:
    """List projects with thread count. 1 round-trip.

    When a search term is provided, uses pg_trgm for fuzzy matching.

    Args:
        db: Async database session.
        search: Optional search text for fuzzy filtering by name/description.
        user_id: Optional user filter.

    Returns:
        A list of project dictionaries with id, name, description, starred,
        thread_count, created_at, and updated_at.
    """
    if search and search.strip():
        params: dict = {"search_text": _normalize_search(search.strip())}
        if user_id:
            sql = _PROJECT_SEARCH_SQL_WITH_USER
            params["user_id"] = str(user_id)
        else:
            sql = _PROJECT_SEARCH_SQL
        result = await db.execute(sql, params)
        return [
            {
                "id": row.id,
                "name": row.name,
                "description": row.description,
                "starred": row.starred,
                "thread_count": row.thread_count,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            }
            for row in result.fetchall()
        ]

    query = (
        select(
            MTIBrainProject,
            func.count(MTIBrainThread.id).label("thread_count"),
        )
        .outerjoin(MTIBrainThread, MTIBrainThread.project_id == MTIBrainProject.id)
        .group_by(MTIBrainProject.id)
        .order_by(MTIBrainProject.updated_at.desc())
    )

    if user_id:
        query = query.where(MTIBrainProject.user_id == user_id)

    result = await db.execute(query)
    return [
        {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "starred": p.starred,
            "thread_count": count,
            "created_at": p.created_at,
            "updated_at": p.updated_at,
        }
        for p, count in result.all()
    ]


async def star_project(
    db: AsyncSession,
    project_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> bool | None:
    """Toggle the starred flag on a project. 1 round-trip (UPDATE RETURNING).

    Args:
        db: Async database session.
        project_id: The project identifier to star/unstar.
        user_id: Optional user filter for ownership check.

    Returns:
        The new starred boolean value, or None if the project was not found.
    """
    stmt = (
        update(MTIBrainProject)
        .where(MTIBrainProject.id == project_id)
        .values(starred=~MTIBrainProject.starred)
        .returning(MTIBrainProject.starred)
    )
    if user_id:
        stmt = stmt.where(MTIBrainProject.user_id == user_id)
    result = await db.execute(stmt)
    row = result.one_or_none()
    return row[0] if row else None


async def update_project(
    db: AsyncSession,
    project_id: uuid.UUID,
    name: str | None = None,
    description: str | None = None,
    user_id: uuid.UUID | None = None,
) -> dict | None:
    """Update project name and/or description. 1 round-trip (UPDATE RETURNING).

    Args:
        db: Async database session.
        project_id: The project identifier to update.
        name: New project name, or None to leave unchanged.
        description: New project description, or None to leave unchanged.
        user_id: Optional user filter for ownership check.

    Returns:
        A dictionary with the updated project fields, or None if the project
        was not found.
    """
    values: dict = {"updated_at": datetime.now(timezone.utc)}
    if name is not None:
        values["name"] = name
    if description is not None:
        values["description"] = description

    stmt = (
        update(MTIBrainProject)
        .where(MTIBrainProject.id == project_id)
        .values(**values)
        .returning(
            MTIBrainProject.id,
            MTIBrainProject.name,
            MTIBrainProject.description,
            MTIBrainProject.starred,
            MTIBrainProject.created_at,
            MTIBrainProject.updated_at,
        )
    )
    if user_id:
        stmt = stmt.where(MTIBrainProject.user_id == user_id)

    result = await db.execute(stmt)
    row = result.one_or_none()
    if not row:
        return None
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description,
        "starred": row.starred,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


async def delete_project(
    db: AsyncSession,
    project_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
) -> bool:
    """Delete a project and cascade-delete its threads. 1 round-trip.

    Args:
        db: Async database session.
        project_id: The project identifier to delete.
        user_id: Optional user filter for ownership check.

    Returns:
        True if the project was deleted, False if it did not exist.
    """
    stmt = delete(MTIBrainProject).where(MTIBrainProject.id == project_id)
    if user_id:
        stmt = stmt.where(MTIBrainProject.user_id == user_id)
    result = await db.execute(stmt)
    deleted = result.rowcount > 0
    if deleted:
        logger.info(f"Project deleted: {project_id}")
    return deleted


# ─── Title Generation ───


def make_title(question: str) -> str | None:
    """Interim title for a new thread, derived from the user's question.

    Returns the question with internal whitespace collapsed, capped at
    the 500-char column limit. The previous heuristic truncated to 7
    words / 80 chars, which produced titles ending mid-sentence
    ("...forecasted and", "...balance in our"). Showing the full
    question is more useful in the sidebar/chats list and is still
    visually constrained by the frontend (truncate in the sidebar,
    line-clamp-2 on the wide list pages).

    Replaced later by an LLM-generated title via :func:`save_smart_title`
    once that path is wired up.
    """
    text_in = (question or "").strip()
    if not text_in:
        return None
    return " ".join(text_in.split())[:500]


async def save_smart_title(thread_id: uuid.UUID, title: str) -> None:
    """Overwrite the thread title with the LLM-generated one.

    Opens its own database session so it can be called from a background task
    without sharing the request-scoped session.

    Args:
        thread_id: The thread identifier to update.
        title: The LLM-generated title to set (truncated to 500 characters).

    Returns:
        None.
    """
    from app.db import async_session_factory

    try:
        async with async_session_factory() as db:
            await db.execute(
                update(MTIBrainThread)
                .where(MTIBrainThread.id == thread_id)
                .values(title=title[:500], updated_at=datetime.now(timezone.utc))
            )
            await db.commit()
        logger.info(f"Smart title set for thread {thread_id}: {title!r}")
    except Exception as e:
        logger.warning(f"Failed to save smart title for {thread_id}: {e}")
