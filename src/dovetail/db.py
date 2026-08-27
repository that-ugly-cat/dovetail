"""SQLite session and budget accounting."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from . import config
from .models import Base, BudgetLedger

_engine = None
_Session: sessionmaker | None = None


def init_engine(path: Path | None = None):
    global _engine, _Session
    target = path or config.db_path()
    if target != Path(":memory:"):
        target.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{target}"
    else:
        url = "sqlite://"
    _engine = create_engine(url, future=True)
    _Session = sessionmaker(bind=_engine, future=True, expire_on_commit=False)
    return _engine


def create_all(path: Path | None = None):
    """Build the schema straight from the models, with no version stamp.

    **Test path only.** A real database is created and upgraded by Alembic
    (`dovetail init-db`): a database built here carries no `alembic_version`
    row, so the first migration run against it would find the tables already
    present and fail.
    """
    engine = init_engine(path)
    Base.metadata.create_all(engine)
    return engine


@contextmanager
def session_scope() -> Iterator[Session]:
    if _Session is None:
        init_engine()
    assert _Session is not None
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


# --- Budget ---------------------------------------------------------------


class BudgetExhausted(RuntimeError):
    """Raised *before* spending, not after receiving a 429."""


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def credits_spent(s: Session, provider: str = "openalex") -> int:
    row = s.scalar(
        select(BudgetLedger).where(
            BudgetLedger.day == _today_utc(), BudgetLedger.provider == provider
        )
    )
    return row.credits_used if row else 0


def credits_remaining(s: Session, provider: str = "openalex") -> int:
    return max(0, config.daily_budget() - credits_spent(s, provider))


def spend(s: Session, credits: int, provider: str = "openalex") -> None:
    """Record a charge, refusing if the budget does not cover it.

    The check is deliberately *before* the call: the budget really does run out
    (it did while this spec was being written), and a refusal that says how much
    is missing beats a 429 read after the fact.
    """
    remaining = credits_remaining(s, provider)
    if credits > remaining:
        raise BudgetExhausted(
            f"need {credits} credits, {remaining} left of {config.daily_budget()} "
            f"for today. The budget resets at midnight UTC. With no key the "
            f"account is anonymous and worth {config.BUDGET_ANONYMOUS} credits: "
            f"setting OPENALEX_API_KEY with a free account raises it to "
            f"{config.BUDGET_WITH_KEY}."
        )
    row = s.scalar(
        select(BudgetLedger).where(
            BudgetLedger.day == _today_utc(), BudgetLedger.provider == provider
        )
    )
    if row is None:
        row = BudgetLedger(day=_today_utc(), provider=provider, credits_used=0, calls=0)
        s.add(row)
    row.credits_used += credits
    row.calls += 1
    s.flush()


def mark_exhausted(s: Session, provider: str = "openalex") -> None:
    """Align the local counter to a 429 that came back from the server.

    The ledger only counts what goes through here, and the same IP can spend
    elsewhere — a script, another session, a `curl` by hand. When the server says
    the till is empty it is right, and going on promising credits that do not
    exist would make every following call fail the same way.
    """
    row = s.scalar(
        select(BudgetLedger).where(
            BudgetLedger.day == _today_utc(), BudgetLedger.provider == provider
        )
    )
    if row is None:
        row = BudgetLedger(day=_today_utc(), provider=provider, credits_used=0, calls=0)
        s.add(row)
    row.credits_used = max(row.credits_used, config.daily_budget())
    s.flush()
