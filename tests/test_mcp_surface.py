"""What the MCP surface may and may not do.

The interesting test here is the one that fails when someone adds a tool: the
guarantee that nothing on this surface approves anything. It is what makes the
server safe to point an agent at, and it is the kind of property that erodes by
accident, one convenient helper at a time.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from dovetail import db
from dovetail.mcp_server import server


def call(name: str, args: dict | None = None):
    async def go():
        res = await server.call_tool(name, args or {})
        if getattr(res, "structuredContent", None):
            return res.structuredContent
        return json.loads(res.content[0].text)

    return asyncio.run(go())


def tool_names() -> set[str]:
    return {t.name for t in asyncio.run(server.list_tools())}


def test_nothing_on_this_surface_approves_or_deletes():
    """SPEC.md §11: approval lives with a person. Proposing is not approving.

    If this fails, someone added a tool that turns a suggestion into a fact
    without anybody looking at it.
    """
    forbidden = [n for n in tool_names() if any(w in n for w in ("approve", "delete", "remove"))]
    assert forbidden == []


def test_every_write_is_named_like_one():
    """The taxonomy the surface is allowed to have.

    Four writes, and none of them turns a suggestion into a fact: two file
    proposals, one records a consultation, one records a genre verdict beside
    the scores. Everything else reads. Keeping this as a name rule is what makes
    it cheap enough to run on every commit — a tool that writes under a reading
    name is the thing this catches.
    """
    writes = {n for n in tool_names() if n.startswith(("propose_", "match_", "judge_"))}
    assert writes == {"propose_venue", "propose_update", "match_venues", "judge_finalists"}

    reads = tool_names() - writes
    assert all(
        n.startswith(("list_", "get_", "search_", "explain_", "budget_", "estimate_"))
        for n in reads
    ), sorted(reads)


def test_anything_that_spends_has_a_free_estimator_beside_it():
    """The house rule for a tool that costs money: a cost seen afterwards is not
    a decision, so the estimate has to exist and has to be free."""
    names = tool_names()
    assert "budget_status" in names, "match_venues spends OpenAlex credits"
    assert "estimate_genre_judgement" in names, "judge_finalists spends the caller's own money"

    est = next(t for t in asyncio.run(server.list_tools()) if t.name == "estimate_genre_judgement")
    assert "free" in est.description.lower()


def test_the_judgement_tool_says_it_orders_nothing():
    """A model reading a verdict must not take it for a ranking signal. The
    judgement is not reproducible, and the description is where a caller learns
    that — nobody reads the spec."""
    tool = next(t for t in asyncio.run(server.list_tools()) if t.name == "judge_finalists")
    text = tool.description.lower()
    assert "orders nothing" in text or "never as ranking" in text
    assert "your own money" in text or "charged to" in text


def test_every_tool_says_what_it_is_for():
    """An MCP tool with no description is a tool an agent will misuse."""
    for t in asyncio.run(server.list_tools()):
        assert t.description and len(t.description.strip()) > 40, t.name


def test_match_venues_warns_about_its_cost_in_its_own_description():
    """It is the only expensive call and the only slow one. A caller that has to
    read the spec to find that out will not read the spec."""
    tool = next(t for t in asyncio.run(server.list_tools()) if t.name == "match_venues")
    text = tool.description.lower()
    assert "credit" in text and ("slow" in text or "minutes" in text)


def test_a_missing_venue_answers_instead_of_raising(tmp_path, monkeypatch):
    monkeypatch.setenv("DOVETAIL_DB", str(tmp_path / "m.db"))
    db.create_all(tmp_path / "m.db")
    assert "error" in call("get_venue", {"venue_id": 999999})


def test_proposing_a_venue_without_a_name_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("DOVETAIL_DB", str(tmp_path / "m2.db"))
    db.create_all(tmp_path / "m2.db")
    assert "error" in call("propose_venue", {"fields": {}, "rationale": "none"})


# --- the HTTP gate --------------------------------------------------------


@pytest.fixture(scope="module")
def http(tmp_path_factory):
    """The app with two keys issued: `/mcp` behind Caddy is public, so this
    middleware is the only thing in front of a surface that spends money.

    Module-scoped, and not by preference: the MCP session manager refuses to
    `.run()` twice on the same instance, so one lifespan per module is the only
    shape that works. In a server that is the normal case — a process starts once.
    """
    import os

    from fastapi.testclient import TestClient

    tmp_path = tmp_path_factory.mktemp("mcp")
    os.environ["JWT_SECRET"] = "test-secret"
    os.environ["DOVETAIL_DB"] = str(tmp_path / "mcp.db")

    from dovetail import apikeys
    from dovetail.models import Role, User
    from dovetail.web import app as web_app

    db.create_all(tmp_path / "mcp.db")
    keys = {}
    with db.session_scope() as s:
        for role in (Role.READER, Role.ADMIN):
            u = User(email=f"{role.value}@example.org", hashed_password="x", role=role)
            s.add(u)
            s.flush()
            keys[role.value] = apikeys.issue(s, u, "test")

    # As a context manager, so the lifespan actually runs. Without it the MCP
    # session manager is never started and every call answers "Task group is not
    # initialized" — which is trap number one of the deploy, reproduced here by
    # accident and worth keeping in mind: the failure says nothing about MCP.
    with TestClient(web_app) as client:
        yield client, keys


def rpc(client, key, method, params=None):
    return client.post(
        "/mcp",
        headers={
            "X-API-Key": key,
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )


def test_the_mcp_endpoint_is_closed_without_a_key(http):
    client, _ = http
    assert rpc(client, "", "tools/list").status_code == 401
    assert rpc(client, "dvt_invented", "tools/list").status_code == 401


def test_a_reader_key_can_list_and_read(http):
    client, keys = http
    r = rpc(client, keys["reader"], "tools/list")
    assert r.status_code == 200 and len(r.json()["result"]["tools"]) == 13


def test_a_reader_key_cannot_write_to_the_queue(http):
    """The key that lists every tool must still be refused by the ones that cost
    something: `/mcp` sits outside the Borant ID gate, so this is the only check."""
    client, keys = http
    r = rpc(client, keys["reader"], "tools/call",
            {"name": "propose_venue",
             "arguments": {"fields": {"display_name": "X"}, "rationale": "test"}})
    assert "denied" in r.text


def test_an_admin_key_can(http):
    client, keys = http
    r = rpc(client, keys["admin"], "tools/call",
            {"name": "propose_venue",
             "arguments": {"fields": {"display_name": "X"}, "rationale": "test"}})
    assert "proposal_id" in r.text and "denied" not in r.text


def test_the_endpoint_answers_without_the_trailing_slash(http):
    """The transport really lives at /mcp/, and a POST to /mcp would earn a 307.
    A redirect on a POST is a bad thing to hand a client: some drop the body,
    some drop the auth header, and it looks like the server is broken."""
    client, keys = http
    assert rpc(client, keys["reader"], "tools/list").status_code == 200


def test_every_cli_command_survives_python_dash_m():
    """`if __name__ == "__main__"` has to be the last thing in cli.py.

    With commands defined below it, `python -m dovetail.cli` runs `app()` before
    they are registered and they are simply absent. Through the console entry
    point everything works, because the module is imported whole first — so the
    failure appears only in the container, which is exactly where DEPLOY.md says
    to use `python -m`. It cost one deploy step to find.
    """
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-m", "dovetail.cli", "--help"],
        capture_output=True, text=True, timeout=90,
    ).stdout
    for command in ("create-user", "api-key", "serve", "validate-against-published"):
        assert command in out, f"{command} is missing when run as a module"


# --- what the surface must not go on hiding -------------------------------


def test_a_run_still_going_is_distinguishable_over_mcp(tmp_path, monkeypatch):
    """The same distinction the `status` column exists for, from the model's
    side. A consultation started from the web answers before it has finished, so
    a row can exist with nothing in it — and `results: 0` alone cannot tell a
    sweep in progress from a process that died."""
    from dovetail import db, mcp_server
    from dovetail.models import MatchRun

    monkeypatch.setenv("DOVETAIL_DB", str(tmp_path / "m.db"))
    db.create_all(tmp_path / "m.db")
    with db.session_scope() as s:
        s.add(MatchRun(title="alive", abstract="x", status="running"))
        s.add(MatchRun(title="dead", abstract="x", status="failed",
                       error_code="budget", error_detail="ran out"))
    out = {r["title"]: r for r in mcp_server.list_runs()["runs"]}
    assert out["alive"]["status"] == "running" and out["alive"]["error_code"] is None
    assert out["dead"]["status"] == "failed" and out["dead"]["error_code"] == "budget"


def test_a_repository_says_so_to_a_model_too(tmp_path, monkeypatch):
    """A name like «PubMed» with no type beside it gives a model nothing to go
    on, and the sweep does drag repositories in — Zenodo touches every topic
    there is."""
    from dovetail import db, mcp_server
    from dovetail.models import Venue

    monkeypatch.setenv("DOVETAIL_DB", str(tmp_path / "m2.db"))
    db.create_all(tmp_path / "m2.db")
    with db.session_scope() as s:
        s.add(Venue(display_name="A Repository", venue_type="repository"))
        s.add(Venue(display_name="A Journal", venue_type="journal"))

    found = {v["name"]: v for v in mcp_server.search_venues(query="A ")["venues"]}
    assert found["A Repository"]["venue_type"] == "repository"
    assert "deposited" in found["A Repository"]["not_a_journal"]
    # A journal carries the type and no warning: the note is for the exceptions.
    assert found["A Journal"]["venue_type"] == "journal"
    assert "not_a_journal" not in found["A Journal"]


def test_judging_without_a_stored_key_is_refused_with_a_sentence(tmp_path, monkeypatch):
    """The call is charged to a person, so a caller with no credential of their
    own gets told where to put one — not a stack trace, and not silence."""
    from dovetail import mcp_server
    from dovetail.models import MatchResult, MatchRun, Role, User, Venue

    monkeypatch.setenv("DOVETAIL_DB", str(tmp_path / "k.db"))
    db.create_all(tmp_path / "k.db")
    with db.session_scope() as s:
        u = User(email="a@b.c", hashed_password="x", role=Role.ADMIN)
        run = MatchRun(title="t", abstract="x", status="done")
        v = Venue(display_name="J", openalex_id="S1")
        s.add_all([u, run, v])
        s.flush()
        s.add(MatchResult(run_id=run.id, venue_id=v.id, bucket="shortlist", position=1))
        run_id, user_id = run.id, u.id

    class Who:
        id = user_id

        def is_admin(self):
            return True

    token = mcp_server.caller.set(Who())
    try:
        out = mcp_server.judge_finalists(run_id)
    finally:
        mcp_server.caller.reset(token)

    assert "denied" in out
    assert "/app/settings" in out["denied"]


def test_the_estimator_answers_without_a_key_and_without_calling_anything(tmp_path, monkeypatch):
    """It has to work for someone deciding *whether* to store a key."""
    from dovetail import mcp_server
    from dovetail.models import MatchResult, MatchRun, Venue

    monkeypatch.setenv("DOVETAIL_DB", str(tmp_path / "e.db"))
    db.create_all(tmp_path / "e.db")
    with db.session_scope() as s:
        run = MatchRun(title="t", abstract="x", status="done")
        v = Venue(display_name="J", openalex_id="S1")
        s.add_all([run, v])
        s.flush()
        s.add(MatchResult(run_id=run.id, venue_id=v.id, bucket="shortlist", position=1))
        run_id = run.id

    out = mcp_server.estimate_genre_judgement(run_id)
    # Two currencies, kept apart: they come out of different pockets.
    assert out["model_calls"] == 1 and out["usd_estimate"] > 0
    assert out["openalex_credits"] == 10
    assert out["already_judged"] == 0
