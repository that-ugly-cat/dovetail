"""Who is asking, and what they are allowed to do.

The borant house pattern, same shape as PaperTrail and LSSR: a JWT in an
httpOnly cookie named `session`, seven days, secret from `JWT_SECRET`, and the
process refuses to start without one.

Two things here are deliberate and easy to get wrong.

**`AUTH_MODE` defaults to `local`.** An app that believes an identity header
with nothing in front of it lets in anyone who can send that header. The gateway
path stays dead code until someone turns it on knowingly, and even then the
headers are read only when the connection came from `BORANT_TRUSTED_PROXY` —
which under Docker is the bridge gateway and *not* 127.0.0.1.

**Authorisation is a dependency, never a template.** `require_admin` is the door;
a template only decides what to draw. Checking a role in Jinja means the button
disappears while the route stays open, which is not a permission — it is a
decoration over one.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Cookie, Depends, HTTPException, Request, status
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Role, User

log = logging.getLogger("dovetail.auth")

ALGORITHM = "HS256"
EXPIRE_DAYS = 7
COOKIE_NAME = "session"


def secret_key() -> str:
    """Read at call time, not at import, so tests can set it and a missing one
    fails where it can be reported rather than at import of an unrelated module.
    """
    key = os.environ.get("JWT_SECRET")
    if not key:
        raise RuntimeError(
            "JWT_SECRET is not set. The web UI will not start without one: a "
            "default secret is the same as no secret, because everyone has it."
        )
    return key


# `local` on purpose. See the module docstring.
def auth_mode() -> str:
    return os.environ.get("AUTH_MODE", "local").strip().lower()


def gateway_mode() -> bool:
    return auth_mode() == "gateway"


def _trusted_networks() -> list:
    raw = os.environ.get("BORANT_TRUSTED_PROXY", "127.0.0.1")
    nets = []
    for chunk in raw.replace(";", ",").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            nets.append(ipaddress.ip_network(chunk, strict=False))
        except ValueError:
            log.warning("BORANT_TRUSTED_PROXY: ignoring %r, not an address or CIDR", chunk)
    return nets


def from_trusted_proxy(request: Request) -> bool:
    peer = request.client.host if request.client else None
    if not peer:
        return False
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(addr in net for net in _trusted_networks())


# --- passwords and tokens -------------------------------------------------


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


def create_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=EXPIRE_DAYS)
    return jwt.encode({"sub": str(user_id), "exp": expire}, secret_key(), algorithm=ALGORITHM)


def _decode_token(token: str) -> int | None:
    try:
        return int(jwt.decode(token, secret_key(), algorithms=[ALGORITHM])["sub"])
    except (JWTError, KeyError, ValueError):
        return None


# --- who is asking --------------------------------------------------------


def user_from_gateway(request: Request, db: Session) -> User | None:
    """The user the gate vouched for, or None.

    Lookup is by `borant_sub` and never by email. An unknown subject gets a fresh
    profile, which is harmless here by construction: a new user is a **reader**,
    and a reader cannot spend credits or approve anything. The failure mode of a
    stranger arriving is an extra row and a read-only screen.

    The gate may hint `admin`. That hint is honoured, and the reason it is safe
    is not in this file: open registration on Borant ID is off, and an access
    request still makes an administrator pick the role when approving. So `admin`
    in that header is there because a person typed it. An unrecognised hint is a
    typo, not a role, and grants nothing but a log line.
    """
    if not gateway_mode():
        return None
    sub = request.headers.get("x-borant-sub")
    if not sub:
        return None
    if not from_trusted_proxy(request):
        log.warning(
            "X-Borant-Sub from %s, outside BORANT_TRUSTED_PROXY: ignored",
            request.client.host if request.client else "?",
        )
        return None

    user = db.scalar(select(User).where(User.borant_sub == sub))
    if user is not None:
        return user if user.is_active else None

    email = (request.headers.get("x-borant-email") or f"{sub}@borant.invalid").strip().lower()

    # The address may already belong to a local account — the ordinary case of
    # turning the gate on for an app that already had users. Linking the two
    # here is exactly what the "never look up by email" rule forbids, and
    # crashing on the unique constraint is not an answer either. So the new
    # profile gets a synthetic address and a loud line: a person links them once,
    # by hand, the way the other borant apps do it.
    if db.scalar(select(User).where(User.email == email)) is not None:
        log.warning(
            "gateway: %s already belongs to a local account. Creating a separate "
            "profile for subject %s instead of linking them — linking by email is "
            "how one person ends up with another person's account. Merge the two "
            "by hand if they are the same person.",
            email,
            sub,
        )
        email = f"{sub}@borant.invalid"

    hint = (request.headers.get("x-borant-hint") or "").strip().lower()
    role = Role.ADMIN if hint == "admin" else Role.READER
    if hint and hint != "admin":
        log.warning("gateway: hint %r is not a role in this app, ignored", hint)
    if role is Role.ADMIN:
        log.warning(
            "gateway: %s (%s) created as ADMIN on the gate's hint. That role can "
            "spend the OpenAlex budget and approve queue entries. Revoke from "
            "/admin/users if it was not intended.",
            email,
            sub,
        )

    user = User(
        email=email,
        name=request.headers.get("x-borant-name") or email,
        # A password nobody knows, rather than none: AUTH_MODE=local has to stay
        # a working way back, and a row with no password is not a way back.
        hashed_password=hash_password(secrets.token_urlsafe(32)),
        borant_sub=sub,
        role=role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    log.info("gateway: new profile for %s (%s), role=%s", email, sub, role.value)
    return user


def user_or_none(
    request: Request, db: Session, session: str | None = None
) -> User | None:
    """For pages that also render logged out. Not a dependency."""
    if gateway_mode():
        # The header wins over the cookie, always: a leftover cookie must not
        # outlive a session the gate has revoked.
        return user_from_gateway(request, db)
    if not session:
        return None
    user_id = _decode_token(session)
    if user_id is None:
        return None
    return db.scalar(select(User).where(User.id == user_id, User.is_active.is_(True)))


def _db_dep():  # pragma: no cover - replaced by the app at import time
    raise RuntimeError("database dependency not wired")


def current_user(
    request: Request,
    session: str | None = Cookie(default=None),
    db: Session = Depends(_db_dep),
) -> User:
    user = user_or_none(request, db, session)
    if user is None:
        if gateway_mode():
            # Fail closed, and say so to the operator rather than to the visitor.
            #
            # Under the gate this route cannot be reached without an identity: if
            # it was, `forward_auth` did not run, which is a configuration fault
            # and not a visitor who needs to sign in. Answering 401 — or worse,
            # redirecting to /login — would send them round a loop the app cannot
            # break from the inside, because /login is on the public branch where
            # the gate never fires. This is the answer Onopedia settled on.
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "AUTH_MODE=gateway, but this request carried no identity. Either "
                "Caddy is not running forward_auth on this path, or the request "
                "did not come from BORANT_TRUSTED_PROXY — under Docker that is "
                "the bridge gateway, not 127.0.0.1.",
            )
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    """The door. Everything that spends credits or turns a proposal into a fact
    goes through here, and nothing relies on a template hiding a button.
    """
    if not user.is_admin():
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This needs an admin: it either spends the shared OpenAlex budget or "
            "makes a proposal true.",
        )
    return user
