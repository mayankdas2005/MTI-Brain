"""Service layer for conversation thread and project CRUD operations."""

import uuid
from datetime import datetime, timezone

from app.core.logger import logger
from app.models.conversation import QuestMessage, QuestProject, QuestThread
from sqlalchemy import delete, exists, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession


# ─── Threads ───


async def create_thread(
    db: AsyncSession,
    thread_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    title: str | None = None,
    user_id: uuid.UUID | None = None,
) -> QuestThread:
    """Create a new conversation thread. 1 round-trip (INSERT).

    Args:
        db: Async database session.
        thread_id: Optional explicit thread ID; a random UUID is generated if None.
        project_id: Optional project to associate the thread with.
        title: Optional initial title for the thread.
        user_id: Owner user UUID.

    Returns:
        The newly created QuestThread instance.
    """
    thread = QuestThread(
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
    stmt = select(QuestThread.id).where(QuestThread.id == thread_id).limit(1)
    if user_id:
        stmt = stmt.where(QuestThread.user_id == user_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None


async def get_thread(
    db: AsyncSession, thread_id: uuid.UUID
) -> tuple[QuestThread, list[QuestMessage]] | tuple[None, None]:
    """Get thread with messages, excluding conversations with empty assistant responses.

    Uses a single query with a subquery filter. 1 round-trip.

    Args:
        db: Async database session.
        thread_id: The thread identifier to retrieve.

    Returns:
        A tuple of (QuestThread, list[QuestMessage]) if found, or (None, None)
        if the thread does not exist.
    """
    # Single query: thread columns + filtered messages + feedback via raw SQL
    result = await db.execute(
        text("""
            WITH valid_convos AS (
                SELECT DISTINCT conversation_id
                FROM quest_message
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
            FROM quest_thread t
            LEFT JOIN quest_message m
                ON m.thread_id = t.id
                AND m.conversation_id IN (SELECT conversation_id FROM valid_convos)
            LEFT JOIN LATERAL (
                SELECT f.liked, f.comment
                FROM quest_feedback f
                WHERE f.message_id = m.id
                ORDER BY f.created_at DESC
                LIMIT 1
            ) f ON true
            WHERE t.id = :tid
            ORDER BY m.created_at ASC,
                     CASE m.role
                         WHEN 'user' THEN 0
                         WHEN 'assistant' THEN 1
                         ELSE 2
                     END ASC
        """),
        {"tid": str(thread_id)},
    )
    rows = result.fetchall()

    if not rows or rows[0].id is None:
        return None, None

    # Build thread from first row
    r = rows[0]
    thread = QuestThread(
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
            msg = QuestMessage(
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
    stmt = delete(QuestThread).where(QuestThread.id == thread_id)
    if user_id:
        stmt = stmt.where(QuestThread.user_id == user_id)
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
    stmt = delete(QuestThread).where(QuestThread.id.in_(thread_ids))
    if user_id:
        stmt = stmt.where(QuestThread.user_id == user_id)
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
        update(QuestThread)
        .where(QuestThread.id == thread_id)
        .values(starred=~QuestThread.starred)
        .returning(QuestThread.starred)
    )
    if user_id:
        stmt = stmt.where(QuestThread.user_id == user_id)
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
        update(QuestThread)
        .where(QuestThread.id == thread_id)
        .values(title=title[:500], updated_at=datetime.now(timezone.utc))
    )
    if user_id:
        stmt = stmt.where(QuestThread.user_id == user_id)
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
        update(QuestThread)
        .where(QuestThread.id.in_(thread_ids))
        .values(project_id=project_id)
    )
    if user_id:
        stmt = stmt.where(QuestThread.user_id == user_id)
    result = await db.execute(stmt)
    return result.rowcount


async def update_thread_title(
    db: AsyncSession,
    thread_id: uuid.UUID,
    title: str,
) -> None:
    """Auto-set title from first question (only if currently NULL). 1 round-trip."""
    await db.execute(
        update(QuestThread)
        .where(QuestThread.id == thread_id, QuestThread.title.is_(None))
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
        update(QuestThread)
        .where(QuestThread.id == thread_id)
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
) -> tuple[QuestMessage, bool]:
    """Save a message, touch thread, and optionally auto-set title in one flush.

    Combines message insert, timestamp bump, and title coalesce in a
    single flush to minimise round-trips.

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

    Returns:
        A tuple of (QuestMessage, bool) where the bool is True when this is
        the first user message in the thread (no prior user messages exist).
    """
    # Check if any user messages already exist in this thread (before we insert ours)
    exists_result = await db.execute(
        select(
            exists().where(
                QuestMessage.thread_id == thread_id,
                QuestMessage.role == "user",
            )
        )
    )
    is_first_message = not exists_result.scalar()

    message = QuestMessage(
        thread_id=thread_id,
        conversation_id=conversation_id,
        parent_conversation_id=parent_conversation_id,
        role=role,
        content=content,
        reasoning=reasoning,
        metadata_=metadata,
    )
    db.add(message)

    # Touch + auto-title in single UPDATE
    if auto_title:
        await db.execute(
            update(QuestThread)
            .where(QuestThread.id == thread_id)
            .values(
                title=func.coalesce(QuestThread.title, auto_title[:500]),
                updated_at=datetime.now(timezone.utc),
            )
        )
    else:
        await db.execute(
            update(QuestThread)
            .where(QuestThread.id == thread_id)
            .values(updated_at=datetime.now(timezone.utc))
        )

    await db.flush()
    return message, is_first_message


async def list_threads(
    db: AsyncSession,
    project_id: uuid.UUID | None = None,
    limit: int = 20,
    offset: int = 0,
    user_id: uuid.UUID | None = None,
) -> list[dict]:
    """List threads with last message preview. 1 round-trip (single query).

    Uses LATERAL joins so the last-message and count subqueries only run
    for the threads returned after LIMIT/OFFSET, not across the entire
    quest_message table.

    Args:
        db: Async database session.
        project_id: Optional project filter; returns all threads if None.
        limit: Maximum number of threads to return.
        offset: Pagination offset.
        user_id: Optional user filter; returns only threads owned by this user.

    Returns:
        A list of thread dictionaries containing id, project_id, title,
        starred, last_message preview, message_count, created_at, and updated_at.
    """
    filters = []
    params: dict = {"limit": limit, "offset": offset}
    if user_id:
        filters.append("t.user_id = :user_id")
        params["user_id"] = str(user_id)
    if project_id:
        filters.append("t.project_id = :project_id")
        params["project_id"] = str(project_id)

    where_clause = ("WHERE " + " AND ".join(filters)) if filters else ""

    result = await db.execute(
        text(f"""
            SELECT
                t.id, t.project_id, t.title, t.starred,
                t.created_at, t.updated_at,
                lm.content AS last_message,
                mc.message_count
            FROM quest_thread t
            LEFT JOIN LATERAL (
                SELECT m.content
                FROM quest_message m
                WHERE m.thread_id = t.id
                ORDER BY m.created_at DESC,
                         CASE m.role
                             WHEN 'assistant' THEN 0
                             WHEN 'user' THEN 1
                             ELSE 2
                         END ASC
                LIMIT 1
            ) lm ON true
            LEFT JOIN LATERAL (
                SELECT count(*) AS message_count
                FROM quest_message m
                WHERE m.thread_id = t.id
            ) mc ON true
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
            "message_count": row.message_count or 0,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
        for row in result.fetchall()
    ]


# ─── Full-text search ───


def _build_search_sql(with_project: bool = False, with_user: bool = False) -> str:
    """Build thread search SQL with 3-layer matching.

    Layers:
      1. Full-text search (tsvector) - handles stemming.
      2. Per-word trigram (word_similarity) - handles typos like "volmue" -> "volume".
      3. Per-word phonetic (dmetaphone) - handles phonetic typos like
         "descripency" -> "discrepancy".

    Multi-word searches require each search word to match at least one word
    in the target text (via trigram OR phonetic), preventing "og ew" from
    matching random text. A minimum rank filter (> 0.3) cuts noise.

    Args:
        with_project: If True, include a ``project_id`` filter clause in the SQL.
        with_user: If True, include a ``user_id`` filter clause in the SQL.

    Returns:
        The raw SQL string for the thread search query.
    """
    project_filter_thread = "AND t.project_id = :project_id" if with_project else ""
    project_filter_msg = "AND t.project_id = :project_id" if with_project else ""
    user_filter_thread = "AND t.user_id = CAST(:user_id AS uuid)" if with_user else ""
    user_filter_msg = "AND t.user_id = CAST(:user_id AS uuid)" if with_user else ""

    return f"""
WITH params AS (
    SELECT
        websearch_to_tsquery('english', :search_text) AS q,
        :search_text AS raw_text,
        string_to_array(lower(trim(:search_text)), ' ') AS search_words
),
-- Helper: check if ALL search words fuzzy-match at least one word in the target text
-- A search word "matches" if it has word_similarity > 0.4 OR same dmetaphone with any target word
thread_hits AS (
    SELECT
        t.id AS thread_id, t.project_id, t.title, t.created_at, t.updated_at,
        NULL::text AS matched_content, NULL::text AS headline,
        'thread' AS match_type,
        COALESCE(ts_rank(t.search_vector, q), 0) * 1.5
            + COALESCE(word_similarity(raw_text, t.title), 0) * 0.8
            + CASE WHEN t.title ILIKE '%' || raw_text || '%' THEN 1.0 ELSE 0 END AS rank
    FROM quest_thread t CROSS JOIN params p
    WHERE (
        -- Layer 1: full-text search
        t.search_vector @@ q
        -- Layer 2: exact substring
        OR t.title ILIKE '%' || p.raw_text || '%'
        -- Layer 3: per-word fuzzy + phonetic - ALL search words must match
        OR (
            array_length(p.search_words, 1) > 0
            AND NOT EXISTS (
                SELECT 1 FROM unnest(p.search_words) AS sw(word)
                WHERE length(sw.word) >= 2
                  AND NOT EXISTS (
                    SELECT 1 FROM unnest(string_to_array(lower(t.title), ' ')) AS tw(word)
                    WHERE word_similarity(sw.word, tw.word) > 0.4
                       OR (length(sw.word) >= 3 AND dmetaphone(sw.word) = dmetaphone(tw.word)
                           AND dmetaphone(sw.word) != '')
                  )
            )
        )
    )
    {project_filter_thread}
    {user_filter_thread}
),
message_hits AS (
    SELECT
        m.thread_id, t.project_id, t.title, t.created_at, t.updated_at,
        m.content AS matched_content,
        ts_headline('english', m.content, q,
            'MaxWords=35, MinWords=15, ShortWord=3, StartSel=<b>, StopSel=</b>'
        ) AS headline,
        'message' AS match_type,
        COALESCE(ts_rank(m.search_vector, q), 0) * 1.0
            + COALESCE(word_similarity(p.raw_text, m.content), 0) * 0.6
            + CASE WHEN m.content ILIKE '%' || p.raw_text || '%' THEN 0.8 ELSE 0 END AS rank
    FROM quest_message m
    JOIN quest_thread t ON t.id = m.thread_id
    CROSS JOIN params p
    WHERE m.role = 'user'
      AND EXISTS (
          SELECT 1 FROM quest_message a
          WHERE a.conversation_id = m.conversation_id
            AND a.role = 'assistant'
            AND a.content IS NOT NULL
            AND a.content != ''
      )
      AND (
        m.search_vector @@ q
        OR m.content ILIKE '%' || p.raw_text || '%'
        OR (
            array_length(p.search_words, 1) > 0
            AND NOT EXISTS (
                SELECT 1 FROM unnest(p.search_words) AS sw(word)
                WHERE length(sw.word) >= 2
                  AND NOT EXISTS (
                    SELECT 1 FROM unnest(string_to_array(lower(m.content), ' ')) AS tw(word)
                    WHERE word_similarity(sw.word, tw.word) > 0.4
                       OR (length(sw.word) >= 3 AND dmetaphone(sw.word) = dmetaphone(tw.word)
                           AND dmetaphone(sw.word) != '')
                  )
            )
        )
      )
      {project_filter_msg}
      {user_filter_msg}
),
all_hits AS (
    SELECT * FROM thread_hits UNION ALL SELECT * FROM message_hits
),
best_per_thread AS (
    SELECT DISTINCT ON (thread_id)
        thread_id, project_id, title, created_at, updated_at,
        match_type, matched_content, headline, rank,
        rank + (EXTRACT(EPOCH FROM updated_at) / 100000000.0) AS final_score
    FROM all_hits
    WHERE rank > 0.1
    ORDER BY thread_id, rank DESC
)
SELECT thread_id, project_id, title, created_at, updated_at,
    match_type, matched_content, headline, rank,
    CASE WHEN match_type = 'thread' THEN title
         ELSE COALESCE(headline, LEFT(matched_content, 120))
    END AS preview, final_score
FROM best_per_thread
ORDER BY final_score DESC
LIMIT :limit OFFSET :offset
"""


_SEARCH_SQL = text(_build_search_sql(with_project=False, with_user=False))
_SEARCH_SQL_WITH_PROJECT = text(_build_search_sql(with_project=True, with_user=False))
_SEARCH_SQL_WITH_USER = text(_build_search_sql(with_project=False, with_user=True))
_SEARCH_SQL_WITH_PROJECT_AND_USER = text(_build_search_sql(with_project=True, with_user=True))


async def search_threads(
    db: AsyncSession,
    search_text: str,
    project_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    """Full-text search across threads and messages. 1 round-trip.

    Args:
        db: Async database session.
        search_text: The user's search query string.
        project_id: Optional project filter; searches all projects if None.
        user_id: Optional user filter; searches only this user's threads.
        limit: Maximum number of results to return.
        offset: Pagination offset.

    Returns:
        A list of result dictionaries with thread_id, project_id, title,
        match_type, preview, headline, rank, created_at, and updated_at.
    """
    params: dict = {"search_text": search_text, "limit": limit, "offset": offset}

    if project_id and user_id:
        query = _SEARCH_SQL_WITH_PROJECT_AND_USER
        params["project_id"] = str(project_id)
        params["user_id"] = str(user_id)
    elif project_id:
        query = _SEARCH_SQL_WITH_PROJECT
        params["project_id"] = str(project_id)
    elif user_id:
        query = _SEARCH_SQL_WITH_USER
        params["user_id"] = str(user_id)
    else:
        query = _SEARCH_SQL

    result = await db.execute(query, params)
    return [
        {
            "thread_id": row.thread_id,
            "project_id": row.project_id,
            "title": row.title,
            "match_type": row.match_type,
            "preview": row.preview,
            "headline": row.headline,
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
        select(QuestMessage.content)
        .where(
            QuestMessage.conversation_id == conversation_id, QuestMessage.role == "user"
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
        select(QuestMessage.content, QuestMessage.parent_conversation_id)
        .where(
            QuestMessage.conversation_id == conversation_id, QuestMessage.role == "user"
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
        select(QuestMessage.conversation_id)
        .where(QuestMessage.thread_id == thread_id, QuestMessage.role == "user")
        .order_by(QuestMessage.created_at.asc())
        .limit(1)
    )
    first_conv = result.scalar_one_or_none()
    return first_conv == conversation_id


async def delete_from_conversation(
    db: AsyncSession,
    thread_id: uuid.UUID,
    conversation_id: uuid.UUID,
    *,
    inclusive: bool = True,
) -> int:
    """Delete messages from a conversation point forward in a thread.

    Used by retry/edit to implement truncation: everything at or after
    the target conversation is removed before the new response is
    generated.
    """
    # Find the earliest created_at of the target conversation
    ts_result = await db.execute(
        select(func.min(QuestMessage.created_at)).where(
            QuestMessage.thread_id == thread_id,
            QuestMessage.conversation_id == conversation_id,
        )
    )
    cutoff = ts_result.scalar_one_or_none()
    if cutoff is None:
        return 0

    op = QuestMessage.created_at >= cutoff if inclusive else QuestMessage.created_at > cutoff

    result = await db.execute(
        delete(QuestMessage).where(
            QuestMessage.thread_id == thread_id,
            op,
        )
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
) -> QuestMessage:
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
        The newly created QuestMessage instance.
    """
    message = QuestMessage(
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
) -> QuestProject:
    """Create a new project. 1 round-trip (INSERT).

    Args:
        db: Async database session.
        name: The project name.
        description: Optional project description.
        user_id: Owner user UUID.

    Returns:
        The newly created QuestProject instance.
    """
    project = QuestProject(name=name, description=description, user_id=user_id)
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
    user_filter = "AND p.user_id = :user_id" if user_id else ""
    params: dict = {"pid": str(project_id)}
    if user_id:
        params["user_id"] = str(user_id)

    result = await db.execute(
        text(f"""
            SELECT
                p.id, p.name, p.description, p.starred, p.created_at, p.updated_at,
                t.id AS thread_id, t.title AS thread_title, t.starred AS thread_starred,
                t.created_at AS thread_created_at, t.updated_at AS thread_updated_at
            FROM quest_project p
            LEFT JOIN quest_thread t ON t.project_id = p.id
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
            :search_text AS raw_text,
            string_to_array(lower(trim(:search_text)), ' ') AS search_words
    )
    SELECT
        p.id, p.name, p.description, p.starred, p.created_at, p.updated_at,
        COUNT(t.id) AS thread_count,
        GREATEST(
            COALESCE(word_similarity(params.raw_text, p.name), 0),
            COALESCE(word_similarity(params.raw_text, COALESCE(p.description, '')), 0)
        ) AS sim_score
    FROM quest_project p
    LEFT JOIN quest_thread t ON t.project_id = p.id
    CROSS JOIN params
    WHERE (
        -- Exact substring
        p.name ILIKE '%' || params.raw_text || '%'
        OR p.description ILIKE '%' || params.raw_text || '%'
        -- Per-word fuzzy + phonetic: ALL search words must match some word in name+description
        OR (
            array_length(params.search_words, 1) > 0
            AND NOT EXISTS (
                SELECT 1 FROM unnest(params.search_words) AS sw(word)
                WHERE length(sw.word) >= 2
                  AND NOT EXISTS (
                    SELECT 1 FROM unnest(string_to_array(
                        lower(p.name || ' ' || COALESCE(p.description, '')), ' '
                    )) AS tw(word)
                    WHERE word_similarity(sw.word, tw.word) > 0.4
                       OR (length(sw.word) >= 3 AND dmetaphone(sw.word) = dmetaphone(tw.word)
                           AND dmetaphone(sw.word) != '')
                  )
            )
        )
    )
    GROUP BY p.id, params.raw_text, params.search_words
    ORDER BY sim_score DESC, p.updated_at DESC
""")


_PROJECT_SEARCH_SQL_WITH_USER = text("""
    WITH params AS (
        SELECT
            :search_text AS raw_text,
            string_to_array(lower(trim(:search_text)), ' ') AS search_words
    )
    SELECT
        p.id, p.name, p.description, p.starred, p.created_at, p.updated_at,
        COUNT(t.id) AS thread_count,
        GREATEST(
            COALESCE(word_similarity(params.raw_text, p.name), 0),
            COALESCE(word_similarity(params.raw_text, COALESCE(p.description, '')), 0)
        ) AS sim_score
    FROM quest_project p
    LEFT JOIN quest_thread t ON t.project_id = p.id
    CROSS JOIN params
    WHERE p.user_id = CAST(:user_id AS uuid)
      AND (
        p.name ILIKE '%' || params.raw_text || '%'
        OR p.description ILIKE '%' || params.raw_text || '%'
        OR (
            array_length(params.search_words, 1) > 0
            AND NOT EXISTS (
                SELECT 1 FROM unnest(params.search_words) AS sw(word)
                WHERE length(sw.word) >= 2
                  AND NOT EXISTS (
                    SELECT 1 FROM unnest(string_to_array(
                        lower(p.name || ' ' || COALESCE(p.description, '')), ' '
                    )) AS tw(word)
                    WHERE word_similarity(sw.word, tw.word) > 0.4
                       OR (length(sw.word) >= 3 AND dmetaphone(sw.word) = dmetaphone(tw.word)
                           AND dmetaphone(sw.word) != '')
                  )
            )
        )
    )
    GROUP BY p.id, params.raw_text, params.search_words
    ORDER BY sim_score DESC, p.updated_at DESC
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
        params: dict = {"search_text": search.strip()}
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
            QuestProject,
            func.count(QuestThread.id).label("thread_count"),
        )
        .outerjoin(QuestThread, QuestThread.project_id == QuestProject.id)
        .group_by(QuestProject.id)
        .order_by(QuestProject.updated_at.desc())
    )

    if user_id:
        query = query.where(QuestProject.user_id == user_id)

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
        update(QuestProject)
        .where(QuestProject.id == project_id)
        .values(starred=~QuestProject.starred)
        .returning(QuestProject.starred)
    )
    if user_id:
        stmt = stmt.where(QuestProject.user_id == user_id)
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
        update(QuestProject)
        .where(QuestProject.id == project_id)
        .values(**values)
        .returning(
            QuestProject.id,
            QuestProject.name,
            QuestProject.description,
            QuestProject.starred,
            QuestProject.created_at,
            QuestProject.updated_at,
        )
    )
    if user_id:
        stmt = stmt.where(QuestProject.user_id == user_id)

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
    stmt = delete(QuestProject).where(QuestProject.id == project_id)
    if user_id:
        stmt = stmt.where(QuestProject.user_id == user_id)
    result = await db.execute(stmt)
    deleted = result.rowcount > 0
    if deleted:
        logger.info(f"Project deleted: {project_id}")
    return deleted


# ─── Title Generation (mock) ───


def make_title(question: str) -> str | None:
    text_in = (question or "").strip()
    if not text_in:
        return None
    words = text_in.split()
    title = " ".join(words[:7])
    return title[:80]


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
                update(QuestThread)
                .where(QuestThread.id == thread_id)
                .values(title=title[:500], updated_at=datetime.now(timezone.utc))
            )
            await db.commit()
        logger.info(f"Smart title set for thread {thread_id}: {title!r}")
    except Exception as e:
        logger.warning(f"Failed to save smart title for {thread_id}: {e}")
