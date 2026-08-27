"""Alembic environment.

The database URL is not read from `alembic.ini`: it comes from
`dovetail.config.db_path()`, so the migrations follow `DOVETAIL_DB` and there is
no second place where the path to the live database is written down.

`render_as_batch=True` matters on SQLite, which cannot ALTER a column: without
it, adding a constraint or changing a type fails at the point where you most
need a migration to work. Batch mode rebuilds the table instead.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from dovetail import config as dovetail_config
from dovetail.models import Base

alembic_config = context.config
if alembic_config.config_file_name is not None:
    fileConfig(alembic_config.config_file_name)

alembic_config.set_main_option("sqlalchemy.url", f"sqlite:///{dovetail_config.db_path()}")

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=alembic_config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        alembic_config.get_section(alembic_config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
