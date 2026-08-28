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


def test_the_only_writes_are_proposals():
    writes = {n for n in tool_names() if n.startswith(("propose_", "match_"))}
    others = tool_names() - writes
    assert writes == {"propose_venue", "propose_update", "match_venues"}
    # Everything else must be a read, by name and by intent.
    assert all(n.startswith(("list_", "get_", "search_", "explain_", "budget_")) for n in others)


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
