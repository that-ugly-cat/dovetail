"""A consultation in progress must not take the rest of the app down with it.

20 Nov 2026, the first consultation run in production with a reference list:
the run finished and recorded its results, and the page showing it answered
«Internal Server Error». The log said `database is locked`, raised by the
lookup that works out who is asking — a read.

The run was one write transaction lasting as long as its network calls, about
ninety seconds. SQLite in its default journal mode lets readers in while a
writer only *intends* to write, and shuts them out once it has to touch the
file — which a transaction that upserts three thousand journals does well
before it commits. A reader then waits five seconds and gives up. The same
thing had been written down on 11 Oct 2026 as «`list_runs` answers 500 while a
search holds the database» and left there.

Two changes, and a test for each: the database runs in WAL mode, where a
reader never waits for a writer; and a run started from the web commits after
every call it pays for, so the write lock is held for milliseconds at a time
instead of for the whole run.
"""

from __future__ import annotations

import sqlite3

import httpx
import pytest

from dovetail import db
from dovetail.models import Venue
from dovetail.sources.openalex import OpenAlexClient


@pytest.fixture()
def path(tmp_path, monkeypatch):
    p = tmp_path / "lock.db"
    monkeypatch.setenv("DOVETAIL_DB", str(p))
    db.create_all(p)
    with db.session_scope() as s:
        s.add(Venue(display_name="A Journal"))
    return p


def test_a_reader_is_not_shut_out_by_a_writer_that_has_the_file(path):
    """`BEGIN EXCLUSIVE` is the state the long run reached once it had more
    changes than fit in memory. In the default journal mode the read below
    fails with `database is locked`; in WAL it does not wait at all."""
    writer = sqlite3.connect(path, isolation_level=None)
    writer.execute("BEGIN EXCLUSIVE")
    writer.execute("insert into venue (display_name, created_at) values ('B', '2026-01-01')")
    try:
        reader = sqlite3.connect(path, timeout=0.2)
        # What it sees is the last committed state: one journal, not two.
        assert reader.execute("select count(*) from venue").fetchone() == (1,)
        reader.close()
    finally:
        writer.execute("ROLLBACK")
        writer.close()


def test_every_connection_waits_for_a_writer_instead_of_failing_at_once(path):
    """Two writers still take turns. The second one waits, for long enough to
    outlast anything short of a stuck process."""
    with db.session_scope() as s:
        conn = s.connection().connection
        assert conn.execute("pragma journal_mode").fetchone()[0] == "wal"
        assert conn.execute("pragma busy_timeout").fetchone()[0] == 30_000


def test_a_run_from_the_web_lets_go_of_the_database_after_each_call_it_pays_for(path):
    """The ledger is written before every OpenAlex call, so without this the
    write lock is taken at the first call and kept through all the network time
    that follows. Committing there means another writer — somebody saving a
    setting, an MCP key being stamped as used — waits for one call, not for the
    run."""
    http = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"results": [], "meta": {"count": 0}})))
    with db.session_scope() as s:
        client = OpenAlexClient(http)
        client.search_sources(s, "a journal")
        other = sqlite3.connect(path, timeout=0.2)
        assert other.execute("select count(*) from budget_ledger").fetchone() == (0,)

        client.commit_each_call = True
        client.search_sources(s, "a journal")
        assert other.execute("select credits_used from budget_ledger").fetchone() == (2,)
        # And the other connection can write straight away: nothing is held.
        other.execute("insert into venue (display_name, created_at) values ('C', '2026-01-01')")
        other.commit()
        other.close()
