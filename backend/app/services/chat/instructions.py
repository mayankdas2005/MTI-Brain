"""Helpers for loading and formatting user standing instructions."""

from __future__ import annotations

import uuid

from app.models.user_instruction import UserInstruction
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def load_enabled_instructions(
    db: AsyncSession, user_id: uuid.UUID
) -> list[UserInstruction]:
    result = await db.execute(
        select(UserInstruction)
        .where(
            UserInstruction.user_id == user_id,
            UserInstruction.enabled == True,  # noqa: E712
        )
        .order_by(UserInstruction.created_at)
    )
    return list(result.scalars().all())


def format_instructions(instructions: list[UserInstruction]) -> str:
    """Return a prompt-ready block of all enabled instructions.

    All enabled instructions are included regardless of scope.
    Scope is stored for future fine-grained routing but is not applied here.
    """
    if not instructions:
        return ""

    lines = ["STANDING INSTRUCTIONS (explicit rules — follow precisely):"]
    for instr in instructions:
        lines.append(f"  - [{instr.title}]: {instr.content}")
    return "\n".join(lines)


async def seed_default_instructions(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Insert the default standing instructions for a newly created user."""
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    defaults = [
        UserInstruction(
            user_id=user_id,
            title="Acronym Glossary",
            content=(
                "At the end of every response, append a glossary table for any domain-specific or "
                "non-obvious acronyms used in the body. Omit universally known terms (USD, EUR, KPI, SQL, API). "
                "Include treasury, finance, banking, and system acronyms (e.g. ACH, FX, SLA, GL, AP, AR, "
                "KRW, MTM, LGD, PD, EAD, WCF, SCC, RCF, LOC, TMS, ERP, SWIFT, SEPA, RTGS). "
                "Format as a markdown table with columns Acronym and Full Form. "
                "Only include acronyms that actually appear in this response. "
                "If no domain-specific acronyms were used, omit the table entirely."
            ),
            enabled=True,
            scope="all",
            created_at=now,
            updated_at=now,
        ),
    ]
    for item in defaults:
        db.add(item)
    await db.flush()
