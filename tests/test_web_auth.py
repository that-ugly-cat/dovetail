"""Who gets in, and what they may do once in.

These are the tests that matter most in this repository, because the failures
they catch are silent: a route that forgot its dependency still renders, and a
hidden button still looks like a permission.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dovetail import auth, config, db
from dovetail.matching import pipeline
from dovetail.models import MatchRun, Role, User, Venue
from dovetail.web import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-not-a-default")
    monkeypatch.setenv("DOVETAIL_DB", str(tmp_path / "web.db"))
    monkeypatch.delenv("AUTH_MODE", raising=False)
    db.create_all(tmp_path / "web.db")
    with db.session_scope() as s:
        s.add_all(
            [
                User(
                    email="reader@example.org",
                    name="Reader",
                    hashed_password=auth.hash_password("pw-reader"),
                    role=Role.READER,
                ),
                User(
                    email="admin@example.org",
                    name="Admin",
                    hashed_password=auth.hash_password("pw-admin"),
                    role=Role.ADMIN,
                ),
            ]
        )
    return TestClient(app, follow_redirects=False)


@pytest.fixture()
def gateway_client(client):
    """The same app, but the request appears to come from 127.0.0.1.

    Without this the peer is the literal string `testclient`, which is not an
    address, and the trusted-proxy check rejects it — correctly, and that is
    itself worth knowing: a peer the app cannot parse is never trusted.
    """
    return TestClient(app, follow_redirects=False, client=("127.0.0.1", 45678))


def sign_in(client: TestClient, email: str, password: str) -> None:
    r = client.post("/login", data={"email": email, "password": password})
    assert r.status_code == 303, r.text


# --- the way in -----------------------------------------------------------


def test_anonymous_is_sent_to_the_login_page(client):
    r = client.get("/app")
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_the_front_page_never_looks_at_who_is_reading_it(client):
    """The rule that makes one button cover four cases.

    On Caddy's public branch the identity headers are stripped by construction,
    so a page that consults `user` would be always-logged-out behind the gate and
    sometimes-logged-in standalone: the same page with two behaviours, and the
    difference invisible to every test that runs locally. Here it is checked from
    both sides — signed in and holding a cookie, and vouched for by the gate — and
    the byte-for-byte comparison is the point.
    """
    anonymous = client.get("/")
    assert anonymous.status_code == 200
    assert 'href="/app"' in anonymous.text

    sign_in(client, "admin@example.org", "pw-admin")
    assert client.get("/").text == anonymous.text, "the front page changed for a signed-in reader"


def test_a_wrong_password_does_not_say_whether_the_address_exists(client):
    """Two different failures, one message: otherwise the form tells an attacker
    which addresses are registered here."""
    known = client.post("/login", data={"email": "reader@example.org", "password": "wrong"})
    unknown = client.post("/login", data={"email": "nobody@example.org", "password": "wrong"})
    assert "Wrong email or password" in known.text
    assert known.text == unknown.text


def test_the_session_cookie_is_httponly(client):
    """A cookie readable from JavaScript is a session any injected script can steal."""
    r = client.post("/login", data={"email": "reader@example.org", "password": "pw-reader"})
    assert "httponly" in r.headers["set-cookie"].lower()


# --- what a reader may not do --------------------------------------------


def test_a_reader_can_read(client):
    sign_in(client, "reader@example.org", "pw-reader")
    for path in ("/app", "/app/venues", "/app/runs", "/app/proposals"):
        assert client.get(path).status_code == 200, path


def test_a_reader_cannot_approve_even_by_posting_directly(client):
    """The point of the test: the reader's page hides the button, but hiding it
    is decoration. The route itself must decline."""
    sign_in(client, "reader@example.org", "pw-reader")
    assert client.post("/app/proposals/1/approve").status_code == 403
    assert client.post("/app/proposals/1/reject").status_code == 403


def test_a_reader_cannot_reach_user_administration(client):
    sign_in(client, "reader@example.org", "pw-reader")
    assert client.get("/app/admin/users").status_code == 403
    assert client.post("/app/admin/users/1/role", data={"role": "admin"}).status_code == 403


def test_an_admin_can_reach_the_same_places(client):
    sign_in(client, "admin@example.org", "pw-admin")
    assert client.get("/app/admin/users").status_code == 200
    # 400 and not 403: the door opened, the proposal simply does not exist.
    assert client.post("/app/proposals/1/approve").status_code == 400


def test_an_admin_cannot_demote_themselves(client):
    """Locking yourself out is not a permission question; the app declines to
    make the mistake on your behalf."""
    sign_in(client, "admin@example.org", "pw-admin")
    with db.session_scope() as s:
        admin_id = s.query(User).filter(User.email == "admin@example.org").one().id
    r = client.post(f"/app/admin/users/{admin_id}/role", data={"role": "reader"})
    assert r.status_code == 400


# --- the gateway ----------------------------------------------------------


def test_identity_headers_are_ignored_when_the_mode_is_local(client, monkeypatch):
    """The default is local on purpose: an app that believes an identity header
    with nothing in front of it lets in whoever sends that header."""
    r = client.get("/app", headers={"X-Borant-Sub": "sub-123", "X-Borant-Email": "x@example.org"})
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_identity_headers_are_ignored_from_an_untrusted_peer(client, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "gateway")
    monkeypatch.setenv("BORANT_TRUSTED_PROXY", "10.9.9.9")  # not the test client
    # 503 and not a redirect: under the gate, a request with no identity means
    # forward_auth did not run. That is a fault for the operator to fix, and
    # bouncing it to a page on the public branch is how the loop was built.
    r = client.get("/app", headers={"X-Borant-Sub": "sub-123"})
    assert r.status_code == 503
    assert "BORANT_TRUSTED_PROXY" in r.text


def test_a_vouched_for_stranger_arrives_as_a_reader(gateway_client, monkeypatch):
    """A new profile is harmless by construction: a reader cannot spend credits
    or approve anything, so an unknown subject costs a row and a read-only screen."""
    monkeypatch.setenv("AUTH_MODE", "gateway")
    monkeypatch.setenv("BORANT_TRUSTED_PROXY", "127.0.0.1")
    r = gateway_client.get(
        "/app", headers={"X-Borant-Sub": "sub-new", "X-Borant-Email": "stranger@example.org"}
    )
    assert r.status_code == 200
    with db.session_scope() as s:
        u = s.query(User).filter(User.borant_sub == "sub-new").one()
        assert u.role is Role.READER
        assert u.hashed_password, "no password means AUTH_MODE=local is not a way back"


def test_an_unrecognised_hint_is_a_typo_and_not_a_role(gateway_client, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "gateway")
    monkeypatch.setenv("BORANT_TRUSTED_PROXY", "127.0.0.1")
    gateway_client.get("/app", headers={"X-Borant-Sub": "sub-typo", "X-Borant-Hint": "administrator"})
    with db.session_scope() as s:
        assert s.query(User).filter(User.borant_sub == "sub-typo").one().role is Role.READER


def test_the_gateway_looks_users_up_by_subject_and_not_by_email(gateway_client, monkeypatch):
    """A typo in someone else's admin panel must not hand one person another
    person's account — and the collision must not crash the app either, which is
    the ordinary case of switching the gate on for an app that already had users.
    """
    monkeypatch.setenv("AUTH_MODE", "gateway")
    monkeypatch.setenv("BORANT_TRUSTED_PROXY", "127.0.0.1")
    gateway_client.get("/app", headers={"X-Borant-Sub": "sub-x", "X-Borant-Email": "admin@example.org"})
    with db.session_scope() as s:
        # The existing admin is untouched; a separate profile was made.
        assert s.query(User).filter(User.email == "admin@example.org").one().borant_sub is None
        made = s.query(User).filter(User.borant_sub == "sub-x").one()
        # A separate profile under a synthetic address, for a person to merge.
        assert made.email != "admin@example.org"
        assert made.role is Role.READER


def test_a_leftover_cookie_does_not_outlive_the_gate(client, monkeypatch):
    """In gateway mode the header decides. A cookie from before must not keep a
    revoked session alive."""
    sign_in(client, "admin@example.org", "pw-admin")
    monkeypatch.setenv("AUTH_MODE", "gateway")
    monkeypatch.setenv("BORANT_TRUSTED_PROXY", "127.0.0.1")
    r = client.get("/app")  # cookie still present, no identity header
    assert r.status_code == 503


# --- the secret -----------------------------------------------------------


def test_there_is_no_default_secret(monkeypatch):
    """A default JWT secret is the same as no secret, because everyone has it."""
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        auth.secret_key()


def test_the_root_does_not_loop_in_gateway_mode(client, monkeypatch):
    """`/` is public in Caddy, so the gate never fires there. The app used to see
    no identity, send the visitor to /login, and /login sent them back to `/`.
    Anyone opening the bare domain got an infinite redirect, and it only appeared
    once the deploy switched to gateway mode.

    Both public pages now point *into* the gate, at a path Caddy protects, which
    is what makes forward_auth run.
    """
    monkeypatch.setenv("AUTH_MODE", "gateway")
    front = client.get("/")
    assert front.status_code == 200 and 'href="/app"' in front.text

    r = client.get("/login")
    assert r.status_code == 200
    assert "Continue with Borant ID" in r.text and 'href="/app"' in r.text
    # And no password form to fill in, since there is nothing to type.
    assert 'type="password"' not in r.text


# --- starting a consultation, which is the screen that spends -------------


def test_only_an_admin_can_start_a_consultation(client):
    """The form and the route, both. Hiding the button on the reader's page is a
    decoration; the POST is the thing that has to decline."""
    sign_in(client, "reader@example.org", "pw-reader")
    assert client.get("/app/runs/new").status_code == 403
    assert client.post(
        "/app/runs/new", data={"title": "T", "abstract": "word " * 100}
    ).status_code == 403


def test_the_cost_is_shown_before_the_button_and_not_after(client):
    """A cost seen afterwards is not a decision. The estimate is free — it makes
    no calls — so it can sit on the form itself."""
    sign_in(client, "admin@example.org", "pw-admin")
    r = client.get("/app/runs/new")
    assert r.status_code == 200
    assert str(config.COST_TEXT) in r.text
    assert str(pipeline.cost_ceiling(discover=True)) in r.text


def test_the_estimate_names_every_call_that_spends():
    """The guard against the drift that already happened once.

    The first estimate was written in the web layer and counted the paginated
    sweep alone, missing the two other calls stage 2 makes. A form promising «at
    most 125» then watched a real run spend 133 — and an estimate that is under
    is worse than none, because it is the one people believe.

    So instead of trusting a comment to stay true, this reads the source of
    `generate_candidates` and fails if it calls anything the estimate does not
    name. Adding a call to stage 2 without pricing it now breaks the suite.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(pipeline.generate_candidates))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "client"
    }
    priced = {t["call"] for t in pipeline.cost_terms(discover=True)}
    assert called <= priced, f"stage 2 calls {called - priced} and the estimate does not price it"


def test_the_ceiling_is_above_what_a_real_run_spent():
    """Pinned to a measurement, not to an intention.

    28 Aug 2026, a full sweep on a three-topic abstract: 133 credits — one
    classification at 100, twenty-one pages of /sources, one grouped /works at
    10, two recovery batches. The ceiling has to sit above that and stay there.
    """
    assert pipeline.cost_ceiling(discover=True) >= 133
    # Without the sweep only the classification is left, and nothing else may
    # creep into that path: it is the one an operator picks to spend less.
    assert pipeline.cost_ceiling(discover=False) == config.COST_TEXT


def test_a_short_abstract_is_refused_without_writing_a_run(client):
    """The guard rail costs nothing to evaluate, so failing it must not leave a
    refused run in the table. Below the threshold the classification is unstable
    enough that a shortlist would mean nothing."""
    sign_in(client, "admin@example.org", "pw-admin")
    r = client.post("/app/runs/new", data={"title": "T", "abstract": "three words only"})
    assert r.status_code == 200
    assert "too short" in r.text
    with db.session_scope() as s:
        assert s.query(MatchRun).count() == 0


def test_a_run_with_no_budget_is_refused_before_it_starts(client, monkeypatch):
    """Refusing before spending, with the number, beats a 429 read afterwards."""
    sign_in(client, "admin@example.org", "pw-admin")
    with db.session_scope() as s:
        db.spend(s, config.daily_budget())
    r = client.post("/app/runs/new", data={"title": "T", "abstract": "word " * 100})
    assert r.status_code == 200 and "Not enough budget" in r.text
    with db.session_scope() as s:
        assert s.query(MatchRun).count() == 0


def test_a_run_started_from_the_web_is_never_left_saying_running(client, monkeypatch):
    """The whole reason the status column exists.

    The row is committed before the sweep starts, so the browser has somewhere to
    go. If the work then dies, the row must say so: «running» forever and «died
    halfway» are the same picture, and they want opposite reactions.
    """
    sign_in(client, "admin@example.org", "pw-admin")

    def explode(*a, **kw):
        raise RuntimeError("the network fell over")

    monkeypatch.setattr("dovetail.matching.pipeline.run_match", explode)
    r = client.post(
        "/app/runs/new",
        data={"title": "T", "abstract": "word " * 100, "discover": "on"},
    )
    assert r.status_code == 303
    with db.session_scope() as s:
        run = s.query(MatchRun).one()
        assert run.status == "failed"
        assert run.error_code == "unexpected"
        # The code travels, the detail comes from the library verbatim: whoever
        # raised it does not know what language it will be read in.
        assert "RuntimeError" in run.error_detail
        assert run.finished_at is not None


# --- declaring a journal by hand ------------------------------------------


def test_only_an_admin_can_declare_a_journal(client):
    sign_in(client, "reader@example.org", "pw-reader")
    assert client.get("/app/venues/new").status_code == 403
    assert client.post("/app/venues/new", data={"display_name": "X"}).status_code == 403


def test_new_is_not_read_as_a_venue_id(client):
    """`/app/venues/new` is registered before `/app/venues/{venue_id}`. The other
    way round, an `int` path parameter swallows "new" and answers 422."""
    sign_in(client, "admin@example.org", "pw-admin")
    assert client.get("/app/venues/new").status_code == 200
    assert client.get("/app/runs/new").status_code == 200


def test_declaring_a_journal_keeps_unknown_as_a_third_answer(client):
    """Leaving a box unknown must not become «no».

    A constraint never excludes on a missing field, it marks — so turning every
    unticked box into False would convert «nobody checked» into «no», which is
    the one substitution this tool is built to refuse.
    """
    sign_in(client, "admin@example.org", "pw-admin")
    r = client.post(
        "/app/venues/new",
        data={"display_name": "Future of Science and Ethics", "is_in_doaj": "", "is_oa": "no"},
    )
    assert r.status_code == 303
    with db.session_scope() as s:
        v = s.query(Venue).filter(Venue.display_name == "Future of Science and Ethics").one()
        assert v.is_in_doaj is None, "unknown became a no"
        assert v.is_oa is False
        # Not a quality judgement: being outside OpenAlex's curated index is not
        # the same as being excluded from it.
        assert v.is_core is None
        assert {fv.source for fv in v.verifications} == {"manual"}
