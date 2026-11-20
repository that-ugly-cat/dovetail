"""The migrated schema and the declared one must be the same schema.

Production is built by `alembic upgrade head` at container start; the tests and
a fresh local database are built by `create_all`. They are two descriptions of
one thing kept in two places, and nothing compared them: a column added to a
model and forgotten in a migration passes every test and fails on the first
request in production, with «no such column».
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from dovetail import db

ROOT = Path(__file__).resolve().parents[1]


def _columns(path: Path) -> dict[str, set[str]]:
    con = sqlite3.connect(path)
    try:
        tables = [
            r[0] for r in con.execute(
                "select name from sqlite_master where type='table' "
                "and name not like 'sqlite_%' and name != 'alembic_version'")
        ]
        return {t: {c[1] for c in con.execute(f'pragma table_info("{t}")')} for t in tables}
    finally:
        con.close()


def test_migrations_build_the_schema_the_models_declare(tmp_path, monkeypatch):
    migrated, declared = tmp_path / "migrated.db", tmp_path / "declared.db"

    monkeypatch.setenv("DOVETAIL_DB", str(migrated))
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head")

    db.create_all(declared)

    assert _columns(migrated) == _columns(declared)
