"""Ingest tribal knowledge .md files into the mti_brain_tribal_knowledge pgvector table.

Reads every .md file under data/Synthetic Company Tribal Knowledge/, strips YAML
frontmatter, embeds the body via Cohere Embed v4 (AWS Bedrock), then upserts each
row into PostgreSQL.

Usage (run from repo root):
    python backend/scripts/ingest_tribal_knowledge.py

Requirements: backend/.env must be present with POSTGRES_* and AWS_BEARER_TOKEN_BEDROCK.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import sys
from pathlib import Path


def _json_default(obj):
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
REPO_ROOT = BACKEND_DIR.parent

sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings
from app.services.embeddings import embed_texts_sync

DATA_DIR = REPO_ROOT / "data" / "Synthetic Company Tribal Knowledge"


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except Exception:
        meta = {}
    return meta, parts[2].strip()


def _collect_files() -> list[dict]:
    records = []
    for path in sorted(DATA_DIR.rglob("*.md")):
        rel = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
        folder = path.parent.name if path.parent != DATA_DIR else "root"
        raw = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(raw)
        records.append({
            "source_file": rel,
            "file_name": path.name,
            "folder": folder,
            "content": body,
            "metadata": meta,
        })
    return records


async def _insert_records(records: list[dict], embeddings: list[list[float]]) -> None:
    import asyncpg

    conn = await asyncpg.connect(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        database=settings.POSTGRES_DB,
    )

    inserted = 0
    failed = 0
    for record, embedding in zip(records, embeddings):
        embedding_str = "[" + ",".join(str(round(v, 8)) for v in embedding) + "]"
        try:
            await conn.execute(
                """
                INSERT INTO mti_brain_tribal_knowledge
                    (source_file, file_name, folder, content, embedding, search_vector, metadata)
                VALUES ($1, $2, $3, $4, $5::vector, to_tsvector('english', $4), $6::jsonb)
                ON CONFLICT (source_file) DO UPDATE SET
                    file_name     = EXCLUDED.file_name,
                    folder        = EXCLUDED.folder,
                    content       = EXCLUDED.content,
                    embedding     = EXCLUDED.embedding,
                    search_vector = EXCLUDED.search_vector,
                    metadata      = EXCLUDED.metadata
                """,
                record["source_file"],
                record["file_name"],
                record["folder"],
                record["content"],
                embedding_str,
                json.dumps(record["metadata"], default=_json_default),
            )
            print(f"  OK  {record['source_file']}")
            inserted += 1
        except Exception as exc:
            print(f"  ERR {record['source_file']}: {exc}")
            failed += 1

    await conn.close()
    print(f"\nDone: {inserted} inserted/updated, {failed} failed out of {len(records)} files.")


def main() -> None:
    if not DATA_DIR.exists():
        print(f"ERROR: data directory not found: {DATA_DIR}")
        sys.exit(1)

    records = _collect_files()
    if not records:
        print("No .md files found.")
        sys.exit(0)

    print(f"Found {len(records)} .md files. Embedding via Cohere Embed v4...")
    contents = [r["content"] for r in records]
    embeddings = embed_texts_sync(contents)
    print(f"Embedding complete. Inserting into PostgreSQL...")

    asyncio.run(_insert_records(records, embeddings))


if __name__ == "__main__":
    main()
