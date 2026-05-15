"""Alembic migration environment.

Loads the project's :class:`Settings` for the database URL and registers
:data:`Base.metadata` so future migrations can use ``--autogenerate``.

The application uses asyncpg at runtime, but alembic itself runs sync.
We swap ``+asyncpg`` for ``+psycopg2`` when constructing the migration URL.

IMPORTANT — non-Alembic table protection
-----------------------------------------
LangGraph creates its own tables (checkpoints, checkpoint_writes,
checkpoint_blobs, checkpoint_migrations, …) directly in the same database.
``include_object`` below tells autogenerate to ONLY touch tables that are
explicitly defined in Base.metadata.  Any table that exists in the DB but
has no corresponding SQLAlchemy model is silently skipped — it will never
appear in a generated migration as a DROP or ALTER.
"""

from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.db.base import Base
# Importing every model module registers their tables on Base.metadata
# so that autogenerate sees them. Avoid removing these even if they look
# unused — drop one and the corresponding table disappears from diffs.
from app.models import conversation as _conversation  # noqa: F401
from app.models import execution_log as _execution_log  # noqa: F401
from app.models import user as _user  # noqa: F401
from app.models import user_features as _user_features  # noqa: F401

config = context.config

# Override sqlalchemy.url from env-driven settings; swap async → sync driver.
sync_url = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
config.set_main_option("sqlalchemy.url", sync_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Whitelist of table names Alembic owns.  Built once at import time from
# the registered metadata so it stays in sync with the models automatically.
_MANAGED_TABLES: frozenset[str] = frozenset(target_metadata.tables.keys())


def include_object(
    object: Any,  # noqa: A002
    name: str,
    type_: str,
    reflected: bool,
    compare_to: Any,
) -> bool:
    """Return True only for objects that belong to our SQLAlchemy models.

    ``reflected=True, compare_to=None`` means the object was found in the
    live database but has NO matching entry in Base.metadata — i.e. it was
    created outside Alembic (LangGraph tables, manual DDL, etc.).  We skip
    those entirely so autogenerate never emits a DROP or spurious ALTER for
    them.
    """
    if type_ == "table":
        return name in _MANAGED_TABLES
    # For indexes, constraints, columns, etc.: follow the parent table rule.
    # Alembic only visits these when it's already inside a managed table, so
    # returning True here is safe.
    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emit SQL, no DB connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (against a live DB)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
