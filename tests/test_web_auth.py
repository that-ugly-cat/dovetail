"""Who gets in, and what they may do once in.

These are the tests that matter most in this repository, because the failures
they catch are silent: a route that forgot its dependency still renders, and a
hidden button still looks like a permission.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dovetail import auth, db
from dovetail.models import Role, User
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
    r = client.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/login"


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
    for path in ("/", "/venues", "/runs", "/proposals"):
        assert client.get(path).status_code == 200, path


def test_a_reader_cannot_approve_even_by_posting_directly(client):
    """The point of the test: the reader's page hides the button, but hiding it
    is decoration. The route itself must decline."""
    sign_in(client, "reader@example.org", "pw-reader")
    assert client.post("/proposals/1/approve").status_code == 403
    assert client.post("/proposals/1/reject").status_code == 403


def test_a_reader_cannot_reach_user_administration(client):
    sign_in(client, "reader@example.org", "pw-reader")
    assert client.get("/admin/users").status_code == 403
    assert client.post("/admin/users/1/role", data={"role": "admin"}).status_code == 403


def test_an_admin_can_reach_the_same_places(client):
    sign_in(client, "admin@example.org", "pw-admin")
    assert client.get("/admin/users").status_code == 200
    # 400 and not 403: the door opened, the proposal simply does not exist.
    assert client.post("/proposals/1/approve").status_code == 400


def test_an_admin_cannot_demote_themselves(client):
    """Locking yourself out is not a permission question; the app declines to
    make the mistake on your behalf."""
    sign_in(client, "admin@example.org", "pw-admin")
    with db.session_scope() as s:
        admin_id = s.query(User).filter(User.email == "admin@example.org").one().id
    r = client.post(f"/admin/users/{admin_id}/role", data={"role": "reader"})
    assert r.status_code == 400


# --- the gateway ----------------------------------------------------------


def test_identity_headers_are_ignored_when_the_mode_is_local(client, monkeypatch):
    """The default is local on purpose: an app that believes an identity header
    with nothing in front of it lets in whoever sends that header."""
    r = client.get("/", headers={"X-Borant-Sub": "sub-123", "X-Borant-Email": "x@example.org"})
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_identity_headers_are_ignored_from_an_untrusted_peer(client, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "gateway")
    monkeypatch.setenv("BORANT_TRUSTED_PROXY", "10.9.9.9")  # not the test client
    r = client.get("/", headers={"X-Borant-Sub": "sub-123"})
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_a_vouched_for_stranger_arrives_as_a_reader(gateway_client, monkeypatch):
    """A new profile is harmless by construction: a reader cannot spend credits
    or approve anything, so an unknown subject costs a row and a read-only screen."""
    monkeypatch.setenv("AUTH_MODE", "gateway")
    monkeypatch.setenv("BORANT_TRUSTED_PROXY", "127.0.0.1")
    r = gateway_client.get(
        "/", headers={"X-Borant-Sub": "sub-new", "X-Borant-Email": "stranger@example.org"}
    )
    assert r.status_code == 200
    with db.session_scope() as s:
        u = s.query(User).filter(User.borant_sub == "sub-new").one()
        assert u.role is Role.READER
        assert u.hashed_password, "no password means AUTH_MODE=local is not a way back"


def test_an_unrecognised_hint_is_a_typo_and_not_a_role(gateway_client, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "gateway")
    monkeypatch.setenv("BORANT_TRUSTED_PROXY", "127.0.0.1")
    gateway_client.get("/", headers={"X-Borant-Sub": "sub-typo", "X-Borant-Hint": "administrator"})
    with db.session_scope() as s:
        assert s.query(User).filter(User.borant_sub == "sub-typo").one().role is Role.READER


def test_the_gateway_looks_users_up_by_subject_and_not_by_email(gateway_client, monkeypatch):
    """A typo in someone else's admin panel must not hand one person another
    person's account — and the collision must not crash the app either, which is
    the ordinary case of switching the gate on for an app that already had users.
    """
    monkeypatch.setenv("AUTH_MODE", "gateway")
    monkeypatch.setenv("BORANT_TRUSTED_PROXY", "127.0.0.1")
    gateway_client.get("/", headers={"X-Borant-Sub": "sub-x", "X-Borant-Email": "admin@example.org"})
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
    r = client.get("/")  # cookie still present, no identity header
    assert r.status_code == 303 and r.headers["location"] == "/login"


# --- the secret -----------------------------------------------------------


def test_there_is_no_default_secret(monkeypatch):
    """A default JWT secret is the same as no secret, because everyone has it."""
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        auth.secret_key()
