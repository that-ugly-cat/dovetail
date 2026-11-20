"""SQLite session and budget accounting."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event, select
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
    event.listen(_engine, "connect", _pragmas)
    _Session = sessionmaker(bind=_engine, future=True, expire_on_commit=False)
    return _engine


# How long a connection waits for another one's write before giving up. The
# default is five seconds, which is shorter than a single page of a sweep.
BUSY_TIMEOUT_MS = 30_000


def _pragmas(dbapi_connection, _record) -> None:
    """Set on every connection, because two of the three are per connection.

    **WAL**, so that a reader never waits for a writer. In the default journal
    mode a writer that has outgrown its page cache takes the file exclusively
    and keeps it until it commits, and a consultation is a write transaction
    that upserts thousands of journals: on 20 Nov 2026 the page showing a run in
    progress answered 500 with `database is locked`, raised by the query that
    looks up who is asking. The mode is stored in the file, so setting it again
    is a no-op; it is set here rather than once because a database restored
    from a dump comes back in the default mode.

    **busy_timeout**, so that a second *writer* waits its turn instead of
    failing at once. WAL does not change that there is one writer at a time.

    **synchronous=NORMAL** is what WAL is meant to run with: a power cut can
    lose the last transaction, never corrupt the file.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


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


# The ledger the instance's own credential spends from. A person who has stored
# their own OpenAlex key spends from a ledger of their own, named after them:
# OpenAlex counts each key's budget separately, so one counter for everybody
# would refuse a run that the key paying for it can perfectly well afford — or
# let the shared key be charged for it in the books and not in fact.
SHARED_LEDGER = "openalex"


def user_ledger(user_id: int) -> str:
    return f"{SHARED_LEDGER}:u{user_id}"


def budget_of(provider: str = SHARED_LEDGER) -> int:
    """What one day is worth on this ledger. A personal key is by definition a
    key, so its ledger is never the anonymous allowance."""
    if provider == SHARED_LEDGER:
        return config.daily_budget()
    return config.BUDGET_WITH_KEY


def credits_spent(s: Session, provider: str = "openalex") -> int:
    row = s.scalar(
        select(BudgetLedger).where(
            BudgetLedger.day == _today_utc(), BudgetLedger.provider == provider
        )
    )
    return row.credits_used if row else 0


def credits_remaining(s: Session, provider: str = "openalex") -> int:
    return max(0, budget_of(provider) - credits_spent(s, provider))


def spend(s: Session, credits: int, provider: str = "openalex") -> None:
    """Record a charge, refusing if the budget does not cover it.

    The check is deliberately *before* the call: the budget really does run out
    (it did while this spec was being written), and a refusal that says how much
    is missing beats a 429 read after the fact.
    """
    remaining = credits_remaining(s, provider)
    if credits > remaining:
        if provider != SHARED_LEDGER:
            raise BudgetExhausted(
                f"need {credits} credits, {remaining} left of {budget_of(provider)} "
                f"on your own OpenAlex key for today. The budget resets at "
                f"midnight UTC."
            )
        raise BudgetExhausted(
            f"need {credits} credits, {remaining} left of {config.daily_budget()} "
            f"for today. The budget resets at midnight UTC. With no key the "
            f"account is anonymous and worth {config.BUDGET_ANONYMOUS} credits: "
            f"a free OpenAlex key — your own in Settings, or OPENALEX_API_KEY on "
            f"the instance — raises it to {config.BUDGET_WITH_KEY}."
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
    row.credits_used = max(row.credits_used, budget_of(provider))
    s.flush()
