"""Journals are everybody's; a consultation is its owner's.

A consultation holds the title of a manuscript that is not published, what it
was read as, its bibliography and where it might go. Until 20 Nov 2026 it had
no owner, and every signed-in user could read every one. Each test below is one
of the places another person's consultation used to come through, plus the two
things that changed with it: who may start one, and being able to delete it.

The check that matters in every one of them is made as the *other* person, not
as the owner: a page that works for whoever it belongs to proves nothing.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from dovetail import auth, crypto, db, ownership
from dovetail.models import (
    BlindSheet,
    Criterion,
    CriterionKind,
    MatchResult,
    MatchRun,
    Role,
    User,
    Venue,
)
from dovetail.web import app

SECRET_TITLE = "An Unpublished Manuscript Nobody Else Should See"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """Three people and one consultation, started by Ada.

    Ada and Ben are members with their own OpenAlex key. Carl is a member with
    none. Root is an admin.
    """
    monkeypatch.setenv("JWT_SECRET", "test-secret-not-a-default")
    monkeypatch.setenv("DOVETAIL_DB", str(tmp_path / "o.db"))
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    crypto._fernet.cache_clear()
    db.create_all(tmp_path / "o.db")
    ids = {}
    with db.session_scope() as s:
        for name, role, keyed in (("ada", Role.READER, True), ("ben", Role.READER, True),
                                  ("carl", Role.READER, False), ("root", Role.ADMIN, False)):
            u = User(email=f"{name}@example.org", name=name.title(), role=role,
                     hashed_password=auth.hash_password(f"pw-{name}"),
                     openalex_key_encrypted=crypto.encrypt_api_key(f"key-{name}") if keyed else None)
            s.add(u)
            s.flush()
            ids[name] = u.id
        v = Venue(display_name="A Shared Journal", venue_type="journal", is_core=True)
        s.add(v)
        run = MatchRun(title=SECRET_TITLE, abstract="x", status="done", user_id=ids["ada"])
        s.add(run)
        s.flush()
        res = MatchResult(run_id=run.id, venue_id=v.id, bucket="shortlist", position=1,
                          score_topic=0.5, score_subfield=0.5)
        s.add(res)
        s.flush()
        s.add(Criterion(result_id=res.id, kind=CriterionKind.MERIT, label="a reason", weight=1.0))
        s.add(BlindSheet(run_id=run.id, seed=1, key=[{"n": 1, "venue_id": v.id, "origin": "topics"}]))
        ids["run"], ids["venue"] = run.id, v.id
    return ids


def _as(name: str) -> TestClient:
    c = TestClient(app, follow_redirects=False)
    r = c.post("/login", data={"email": f"{name}@example.org", "password": f"pw-{name}"})
    assert r.status_code == 303, r.text
    return c


# --- the places it used to come through -----------------------------------


def test_the_owner_sees_her_consultation_everywhere(world):
    """The baseline, so that the tests below are not passing on an empty page."""
    ada = _as("ada")
    assert SECRET_TITLE in ada.get("/app/runs").text
    assert SECRET_TITLE in ada.get("/app").text
    assert SECRET_TITLE in ada.get(f"/app/runs/{world['run']}").text
    assert SECRET_TITLE in ada.get(f"/app/venues/{world['venue']}").text


@pytest.mark.parametrize("who", ["ben", "carl", "root"])
def test_nobody_else_finds_it_in_a_list(world, who):
    """Not another member, and not an admin: running the instance does not
    take reading other people's manuscripts."""
    other = _as(who)
    for path in ("/app/runs", "/app", f"/app/venues/{world['venue']}"):
        r = other.get(path)
        assert r.status_code == 200, path
        assert SECRET_TITLE not in r.text, f"{who} sees the title on {path}"


@pytest.mark.parametrize("who", ["ben", "root"])
def test_its_own_page_answers_like_a_consultation_that_does_not_exist(world, who):
    """404 and not 403, with the same words: «not yours» answered differently
    from «no such thing» would say that the thing exists."""
    other = _as(who)
    theirs = other.get(f"/app/runs/{world['run']}")
    missing = other.get("/app/runs/99999")
    assert theirs.status_code == missing.status_code == 404
    assert theirs.json() == missing.json()


def test_nobody_else_can_act_on_it(world):
    ben = _as("ben")
    assert ben.post(f"/app/runs/{world['run']}/read-finalists").status_code == 404
    assert ben.post(f"/app/runs/{world['run']}/delete").status_code == 404
    with db.session_scope() as s:
        assert s.get(MatchRun, world["run"]) is not None


def test_the_journal_itself_is_everybodys(world):
    """The other half of the rule. The journal a consultation brought in is on
    everyone's list, and nothing on it says which manuscript made it come up."""
    ben = _as("ben")
    assert "A Shared Journal" in ben.get("/app/venues").text
    page = ben.get(f"/app/venues/{world['venue']}").text
    assert "A Shared Journal" in page and "Your consultations it appeared in" not in page


def test_an_admin_sees_how_much_and_not_what(world):
    page = _as("root").get("/app/admin/users").text
    assert SECRET_TITLE not in page
    # One consultation against Ada's name, and she is shown as holding a key.
    row = page[page.index("ada@example.org"):page.index("ben@example.org")]
    assert '<td class="n muted">1</td>' in row and ">yes<" in row


def test_a_consultation_with_no_owner_is_the_instances(world):
    """Started from the command line: nobody's name is on it. Admins see it,
    because somebody has to be able to; members do not."""
    with db.session_scope() as s:
        run = MatchRun(title="From The Command Line", abstract="x", status="done")
        s.add(run)
        s.flush()
        run_id = run.id
    assert _as("root").get(f"/app/runs/{run_id}").status_code == 200
    assert "From The Command Line" in _as("root").get("/app/runs").text
    assert _as("ada").get(f"/app/runs/{run_id}").status_code == 404
    assert "From The Command Line" not in _as("ada").get("/app/runs").text


# --- over MCP -------------------------------------------------------------


def _calling_as(name, fn, *args, **kw):
    from dovetail import mcp_server

    with db.session_scope() as s:
        who = s.query(User).filter(User.email == f"{name}@example.org").one()
        s.expunge(who)
    token = mcp_server.caller.set(who)
    try:
        return fn(*args, **kw)
    finally:
        mcp_server.caller.reset(token)


def test_a_key_reads_its_own_consultations_and_no_others(world):
    from dovetail import mcp_server

    mine = _calling_as("ada", mcp_server.list_runs)["runs"]
    assert [r["title"] for r in mine] == [SECRET_TITLE]
    assert _calling_as("ben", mcp_server.list_runs)["runs"] == []
    assert _calling_as("root", mcp_server.list_runs)["runs"] == []

    gone = {"error": f"no consultation {world['run']}"}
    assert _calling_as("ben", mcp_server.explain_match, world["run"], world["venue"]) == gone
    assert _calling_as("ben", mcp_server.estimate_genre_judgement, world["run"]) == gone
    assert _calling_as("ben", mcp_server.judge_finalists, world["run"]) == gone
    assert "error" not in _calling_as("ada", mcp_server.explain_match, world["run"], world["venue"])


def test_a_consultation_started_over_mcp_belongs_to_the_keys_owner(world, monkeypatch):
    from dovetail import mcp_server
    from dovetail.matching.pipeline import Refusal

    seen = {}

    def fake(s, client, *a, **kw):
        seen.update(owner=kw.get("owner_id"), ledger=client.ledger)
        raise Refusal("stop here")

    monkeypatch.setattr(mcp_server, "run_match_full", fake)
    out = _calling_as("ben", mcp_server.match_venues, "T", "word " * 100)
    assert out == {"refused": "stop here"}
    assert seen == {"owner": world["ben"], "ledger": db.user_ledger(world["ben"])}


# --- who may start one ----------------------------------------------------


def test_a_member_with_their_own_key_may_start_a_consultation(world, monkeypatch):
    """It used to take an admin, and the reason was the budget everybody
    shared. Somebody spending their own key costs nobody anything."""
    monkeypatch.setattr("dovetail.web._execute_run", lambda *a, **kw: None)
    ben = _as("ben")
    assert ben.get("/app/runs/new").status_code == 200
    assert "New consultation" in ben.get("/app").text
    r = ben.post("/app/runs/new", data={"title": "Ben's Paper", "abstract": "word " * 100})
    assert r.status_code == 303
    with db.session_scope() as s:
        run = s.query(MatchRun).filter(MatchRun.title == "Ben's Paper").one()
        assert run.user_id == world["ben"]
    # And Ada does not see it, any more than Ben sees hers.
    assert "Ben's Paper" not in _as("ada").get("/app/runs").text


def test_a_member_with_no_key_is_told_what_is_missing(world):
    carl = _as("carl")
    r = carl.get("/app/runs/new")
    assert r.status_code == 403 and "your own OpenAlex key" in r.json()["detail"]
    assert carl.post("/app/runs/new", data={"title": "T", "abstract": "word " * 100}).status_code == 403
    home = carl.get("/app").text
    assert 'href="/app/runs/new"' not in home
    assert "add your own OpenAlex key" in home
    # Over MCP the same door, with the same reason.
    from dovetail import mcp_server

    out = _calling_as("carl", mcp_server.match_venues, "T", "word " * 100)
    assert out["error"] == "needs_your_own_openalex_key"


def test_the_shared_things_still_take_an_admin(world):
    """Having a key lets a person spend their own budget on their own work. It
    does not make them a keeper of what everyone shares."""
    ben = _as("ben")
    assert ben.get("/app/venues/new").status_code == 403
    assert ben.get("/app/admin/users").status_code == 403
    assert ownership.may_consult(None)  # the command line: whoever owns the box


def test_the_role_is_called_what_it_now_does(world):
    assert ">member<" in _as("ben").get("/app").text
    assert ">admin<" in _as("root").get("/app").text


# --- taking it back out ---------------------------------------------------


def test_the_owner_can_delete_her_consultation_and_the_journals_stay(world):
    ada = _as("ada")
    r = ada.post(f"/app/runs/{world['run']}/delete")
    assert r.status_code == 303 and r.headers["location"] == f"/app/runs?deleted={world['run']}"
    with db.session_scope() as s:
        assert s.get(MatchRun, world["run"]) is None
        assert s.query(MatchResult).count() == 0
        assert s.query(Criterion).count() == 0
        assert s.query(BlindSheet).count() == 0
        assert s.get(Venue, world["venue"]) is not None
    assert SECRET_TITLE not in ada.get("/app/runs").text
    assert ada.get(f"/app/runs/{world['run']}").status_code == 404


def test_a_consultation_still_running_is_not_deleted_from_under_itself(world):
    with db.session_scope() as s:
        s.get(MatchRun, world["run"]).status = "running"
    assert _as("ada").post(f"/app/runs/{world['run']}/delete").status_code == 409
    with db.session_scope() as s:
        assert s.get(MatchRun, world["run"]) is not None


# --- the rows that were there before --------------------------------------


def _upgrade(path, monkeypatch, revision):
    monkeypatch.setenv("DOVETAIL_DB", str(path))
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, revision)


def _seed(path, users):
    con = sqlite3.connect(path)
    for email in users:
        con.execute(
            "insert into app_user (email, hashed_password, role, is_active, created_at) "
            "values (?, 'x', 'READER', 1, '2026-01-01')", (email,))
    con.execute(
        "insert into match_run (title, abstract, status, created_at) "
        "values ('An old one', 'x', 'done', '2026-01-01')")
    con.commit()
    con.close()


def _owner(path):
    con = sqlite3.connect(path)
    try:
        return con.execute("select user_id from match_run").fetchone()[0]
    finally:
        con.close()


def test_with_one_account_the_old_consultations_are_that_accounts(tmp_path, monkeypatch):
    path = tmp_path / "one.db"
    _upgrade(path, monkeypatch, "f3a81d5c6e20")
    _seed(path, ["only@example.org"])
    _upgrade(path, monkeypatch, "head")
    assert _owner(path) == 1


def test_with_several_accounts_nobody_is_handed_somebody_elses(tmp_path, monkeypatch):
    """Left without an owner rather than guessed at: giving one person another
    person's manuscript is the mistake the column exists to prevent."""
    path = tmp_path / "two.db"
    _upgrade(path, monkeypatch, "f3a81d5c6e20")
    _seed(path, ["a@example.org", "b@example.org"])
    _upgrade(path, monkeypatch, "head")
    assert _owner(path) is None
