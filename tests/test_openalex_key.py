"""A person's own OpenAlex key, and the till it spends from.

The instance had one key for everybody, so one person's consultation spent what
the next person needed. A key stored in Settings moves a user's spend onto a
ledger of their own. What is tested is that the two tills really are apart —
in the request that goes out, in the books, and on every page that prints a
budget — and that a key is not kept unless OpenAlex takes it.
"""

from __future__ import annotations

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from dovetail import auth, config, crypto, db
from dovetail.models import MatchRun, Role, User, Venue
from dovetail.sources import openalex
from dovetail.sources.openalex import (
    KeyRejected,
    NoOwnKey,
    OpenAlexClient,
    client_for,
    ledger_for,
    verify_key,
)
from dovetail.web import app

PERSONAL = "oa-personal-key-9f3a"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-not-a-default")
    monkeypatch.setenv("DOVETAIL_DB", str(tmp_path / "k.db"))
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("AUTH_MODE", raising=False)
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    crypto._fernet.cache_clear()
    db.create_all(tmp_path / "k.db")
    with db.session_scope() as s:
        s.add_all([
            User(email="admin@example.org", name="Admin",
                 hashed_password=auth.hash_password("pw-admin"), role=Role.ADMIN),
            User(email="other@example.org", name="Other",
                 hashed_password=auth.hash_password("pw-other"), role=Role.ADMIN),
        ])
    c = TestClient(app, follow_redirects=False)
    r = c.post("/login", data={"email": "admin@example.org", "password": "pw-admin"})
    assert r.status_code == 303
    return c


def _admin(s) -> User:
    return s.query(User).filter(User.email == "admin@example.org").one()


def _accepting(monkeypatch, limit=10_000):
    seen = []
    monkeypatch.setattr("dovetail.sources.openalex.verify_key",
                        lambda key, client=None: seen.append(key) or limit)
    return seen


# --- two tills ------------------------------------------------------------


def test_a_personal_ledger_has_a_keyed_budget_whatever_the_instance_has(client):
    """The instance here has no key, so its shared ledger is the anonymous
    thousand. A personal key is by definition a key."""
    with db.session_scope() as s:
        mine = db.user_ledger(7)
        assert db.budget_of(db.SHARED_LEDGER) == config.BUDGET_ANONYMOUS
        assert db.budget_of(mine) == config.BUDGET_WITH_KEY
        db.spend(s, 900, db.SHARED_LEDGER)
        db.spend(s, 2_500, mine)
        assert db.credits_remaining(s, db.SHARED_LEDGER) == 100
        assert db.credits_remaining(s, mine) == 7_500
        # And one person's spend is not another's.
        assert db.credits_remaining(s, db.user_ledger(8)) == config.BUDGET_WITH_KEY


def test_the_request_carries_the_persons_key_and_the_books_their_ledger(client, monkeypatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "instance-key")
    sent = []

    def handler(request):
        sent.append(request.headers.get("authorization"))
        return httpx.Response(200, json={"results": [], "meta": {"count": 0}})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    with db.session_scope() as s:
        OpenAlexClient(http).search_sources(s, "a journal")
        OpenAlexClient(http, api_key=PERSONAL, ledger=db.user_ledger(1)).search_sources(s, "a journal")
        assert sent == ["Bearer instance-key", f"Bearer {PERSONAL}"]
        assert db.credits_spent(s, db.SHARED_LEDGER) == 1
        assert db.credits_spent(s, db.user_ledger(1)) == 1


def test_a_429_on_one_key_empties_that_till_and_not_the_other(client):
    """`mark_exhausted` aligns the books with the server. It has to align the
    books of the key that was refused."""
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429)))
    mine = db.user_ledger(1)
    with db.session_scope() as s:
        with pytest.raises(openalex.RemoteBudgetExhausted):
            OpenAlexClient(http, api_key=PERSONAL, ledger=mine).search_sources(s, "x")
        assert db.credits_remaining(s, mine) == 0
        assert db.credits_remaining(s, db.SHARED_LEDGER) == config.BUDGET_ANONYMOUS


def test_a_person_is_given_their_own_key_or_no_client_at_all(client):
    """There is no key to fall back on. A signed-in person with none of their
    own gets no client, so nothing they start can be charged to anybody else."""
    with db.session_scope() as s:
        me = _admin(s)
        with pytest.raises(NoOwnKey, match="add one in Settings"):
            client_for(me)
        assert ledger_for(me) is None
        me.openalex_key_encrypted = crypto.encrypt_api_key(PERSONAL)
        mine = client_for(me)
        assert mine.ledger == db.user_ledger(me.id) and mine.keyed
        assert mine._headers() == {"Authorization": f"Bearer {PERSONAL}"}
        # The command line has no identity and uses the operator's key. No
        # signed-in user is ever handed this one.
        assert client_for(None).ledger == db.SHARED_LEDGER == ledger_for(None)


def test_a_stored_key_that_can_no_longer_be_read_is_not_a_key(client, monkeypatch):
    """`FERNET_KEY` rotated under a stored key. It used to fall back to the
    instance's key without a word, which is exactly a person's work being paid
    for by somebody else. It now says the key has to be stored again — and the
    pages that only print a budget still open, because they never build it."""
    with db.session_scope() as s:
        _admin(s).openalex_key_encrypted = crypto.encrypt_api_key(PERSONAL)
    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    crypto._fernet.cache_clear()
    with db.session_scope() as s:
        with pytest.raises(NoOwnKey, match="can no longer be read"):
            client_for(_admin(s))
    assert client.get("/app").status_code == 200
    assert client.get("/app/settings").status_code == 200


# --- asking OpenAlex ------------------------------------------------------


@pytest.mark.parametrize("status,code", [(401, "invalid"), (403, "inactive"),
                                         (429, "exhausted"), (503, "unreachable")])
def test_a_key_openalex_will_not_take_is_refused_with_the_reason(status, code):
    """401 is a key OpenAlex does not know; 403 is one that was issued and whose
    activation link was never clicked — which looks identical on the page."""
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, text="no")))
    with pytest.raises(KeyRejected) as e:
        verify_key("some-key", http)
    assert e.value.code == code


def test_an_accepted_key_reports_its_own_daily_limit():
    def handler(request):
        assert request.headers["authorization"] == "Bearer some-key"
        return httpx.Response(200, json={"results": []}, headers={"X-RateLimit-Limit": "10000"})

    assert verify_key("  some-key ", httpx.Client(transport=httpx.MockTransport(handler))) == 10_000


# --- the settings page ----------------------------------------------------


def test_a_key_is_stored_encrypted_and_never_shown_back(client, monkeypatch):
    seen = _accepting(monkeypatch)
    r = client.post("/app/settings/openalex-key", data={"openalex_key": f"  {PERSONAL} "})
    assert r.status_code == 303 and r.headers["location"] == "/app/settings?openalex=stored"
    assert seen == [PERSONAL]  # tried, stripped, before being kept
    with db.session_scope() as s:
        stored = _admin(s).openalex_key_encrypted
        assert stored and PERSONAL not in stored
        assert crypto.decrypt_api_key(stored) == PERSONAL
    page = client.get("/app/settings?openalex=stored").text
    assert PERSONAL not in page and "a key is stored" in page
    assert "credits left on your key" in page


def test_a_rejected_key_is_not_stored_and_does_not_cost_the_one_already_there(client, monkeypatch):
    """A typo while replacing a working key must leave the working key."""
    _accepting(monkeypatch)
    client.post("/app/settings/openalex-key", data={"openalex_key": PERSONAL})

    def reject(key, client=None):
        raise KeyRejected("invalid", "OpenAlex does not know this key")

    monkeypatch.setattr("dovetail.sources.openalex.verify_key", reject)
    r = client.post("/app/settings/openalex-key", data={"openalex_key": "oa-typo"})
    assert r.headers["location"] == "/app/settings?openalex=invalid"
    assert "oa-typo" not in r.headers["location"]
    with db.session_scope() as s:
        assert crypto.decrypt_api_key(_admin(s).openalex_key_encrypted) == PERSONAL
    assert "the one you had is still in" in client.get("/app/settings?openalex=invalid").text


def test_saving_the_field_empty_removes_the_key_without_asking_openalex(client, monkeypatch):
    seen = _accepting(monkeypatch)
    client.post("/app/settings/openalex-key", data={"openalex_key": PERSONAL})
    r = client.post("/app/settings/openalex-key", data={"openalex_key": "   "})
    assert r.headers["location"] == "/app/settings?openalex=cleared"
    assert seen == [PERSONAL]
    with db.session_scope() as s:
        assert _admin(s).openalex_key_encrypted is None


def test_the_page_says_how_to_get_a_key(client):
    """The «?» opens a dialog, and what it says is checkable: the two addresses
    are the ones OpenAlex's own documentation gives."""
    page = client.get("/app/settings").text
    assert '<dialog id="openalex-help"' in page
    assert "getElementById('openalex-help').showModal()" in page
    assert "https://openalex.org/signup" in page
    assert "https://openalex.org/settings/api-key" in page
    assert 'rel="noopener noreferrer"' in page


def test_with_no_way_to_encrypt_nothing_is_stored(client, monkeypatch):
    monkeypatch.delenv("FERNET_KEY", raising=False)
    crypto._fernet.cache_clear()
    assert client.post("/app/settings/openalex-key",
                       data={"openalex_key": PERSONAL}).status_code == 503
    page = client.get("/app/settings")
    assert page.status_code == 200 and 'name="openalex_key"' not in page.text


# --- where it is spent ----------------------------------------------------


def test_with_no_key_there_is_no_budget_to_show_and_with_one_it_is_yours(client, monkeypatch):
    """No shared till. Without a key the pages say what is missing and print no
    number, because any number there would be somebody else's."""
    _accepting(monkeypatch)
    home = client.get("/app").text
    assert "add your own OpenAlex key" in home
    assert 'href="/app/runs/new"' not in home
    assert "consultations left today" not in home
    assert "You need a key to start consultations" in client.get("/app/settings").text

    client.post("/app/settings/openalex-key", data={"openalex_key": PERSONAL})
    form = client.get("/app/runs/new").text
    assert "10,000 of 10,000" in form and "on your own key" in form
    home = client.get("/app").text
    assert "on your own key" in home and 'href="/app/runs/new"' in home
    # The other admin stored nothing, and one person's key opens nothing for another.
    other = TestClient(app, follow_redirects=False)
    other.post("/login", data={"email": "other@example.org", "password": "pw-other"})
    assert other.get("/app/runs/new").status_code == 403


def test_being_an_admin_is_not_a_way_to_spend_without_a_key(client):
    """The role decides who may change what everybody shares. It does not pay
    for anything: an admin with no key starts no consultation, and cannot
    describe a journal from its articles either, because that spends credits."""
    r = client.post("/app/runs/new",
                    data={"title": "T", "abstract": "word " * 100, "discover": "on"})
    assert r.status_code == 403 and "your own OpenAlex key" in r.json()["detail"]
    with db.session_scope() as s:
        assert s.query(MatchRun).count() == 0
        v = Venue(display_name="A Journal")
        s.add(v)
        s.flush()
        venue_id = v.id
    assert client.get(f"/app/venues/{venue_id}/profile").status_code == 403
    # What only changes a shared record, and costs nothing, is still the admin's.
    assert client.get("/app/venues/new").status_code == 200


def test_a_run_spends_the_key_of_whoever_started_it(client, monkeypatch):
    """The background task is handed a user id and not a key: it runs after the
    request is gone, and looks the credential up when it needs it."""
    _accepting(monkeypatch)
    client.post("/app/settings/openalex-key", data={"openalex_key": PERSONAL})
    used = {}

    def fake_run_match(s, oa, *a, **kw):
        used["ledger"], used["key"] = oa.ledger, oa._api_key

    monkeypatch.setattr("dovetail.matching.pipeline.run_match", fake_run_match)
    r = client.post("/app/runs/new",
                    data={"title": "T", "abstract": "word " * 100, "discover": "on"})
    assert r.status_code == 303
    with db.session_scope() as s:
        me = _admin(s)
        assert used == {"ledger": db.user_ledger(me.id), "key": PERSONAL}
        assert s.query(MatchRun).one().status == "done"


def test_the_form_offers_a_language_and_the_core_list_and_records_the_choice(client, monkeypatch):
    """English is chosen when the form opens; the core list is not. What the
    person picks is what the consultation is run with."""
    _accepting(monkeypatch)
    client.post("/app/settings/openalex-key", data={"openalex_key": PERSONAL})
    form = client.get("/app/runs/new").text
    assert '<option value="en" selected>English</option>' in form
    assert 'name="core_only"' in form and 'name="core_only" checked' not in form
    assert 'id="core-help"' in form and "The Leiden core list" in form

    seen = []

    def fake_run_match(s, oa, title, abstract, word_count, constraints, **kw):
        seen.append(constraints)

    monkeypatch.setattr("dovetail.matching.pipeline.run_match", fake_run_match)
    base = {"title": "T", "abstract": "word " * 100, "discover": "on"}
    client.post("/app/runs/new", data={**base, "language": "de", "core_only": "on"})
    client.post("/app/runs/new", data={**base, "language": ""})
    client.post("/app/runs/new", data={**base, "language": "klingon"})
    assert seen == [{"language": "de", "core_only": True}, {}, {}]

    page = client.get("/app/runs/1").text
    assert "journals in German" in page and "Leiden core list only" in page


def test_over_mcp_the_callers_own_key_is_the_one_used_and_there_is_no_other(client, monkeypatch):
    from dovetail import mcp_server

    def as_admin(fn, *a, **kw):
        with db.session_scope() as s:
            me = _admin(s)
            s.expunge(me)
        token = mcp_server.caller.set(me)
        try:
            return fn(*a, **kw)
        finally:
            mcp_server.caller.reset(token)

    # No key: nothing to spend, and nothing is started on anybody else's.
    status = as_admin(mcp_server.budget_status)
    assert status["has_api_key"] is False and status["daily_budget"] == 0
    assert as_admin(mcp_server.match_venues, "T", "word " * 100)["error"] == (
        "needs_your_own_openalex_key")

    with db.session_scope() as s:
        _admin(s).openalex_key_encrypted = crypto.encrypt_api_key(PERSONAL)
    status = as_admin(mcp_server.budget_status)
    assert status["spending_from"] == "your own OpenAlex key"
    assert status["daily_budget"] == config.BUDGET_WITH_KEY

    def key_in_use():
        with db.session_scope() as s:
            return mcp_server._callers_openalex(s)._api_key

    assert as_admin(key_in_use) == PERSONAL

    # Over stdio there is no caller: the key in the environment, which here is
    # absent, so a search that needs one is refused by name.
    assert "command line" in mcp_server.budget_status()["spending_from"]
    assert mcp_server.match_venues("T", "word " * 100)["error"] == "no_openalex_key"


