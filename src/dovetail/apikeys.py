"""Keys for the model-facing surface.

Hashed, never stored in the clear, and shown once. The reasoning is the ordinary
one for credentials and worth writing down anyway: a database that leaks should
leak hashes, and an operator who can read a user's key can act as that user
without leaving a trace that says so.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ApiKey, User

PREFIX = "dvt_"


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def issue(session: Session, user: User, label: str | None = None) -> str:
    """Make a key and return the plaintext. This is the only time it exists."""
    key = PREFIX + secrets.token_urlsafe(32)
    session.add(ApiKey(user_id=user.id, key_hash=_hash(key), label=label))
    session.flush()
    return key


def resolve(session: Session, key: str) -> User | None:
    """The user a key belongs to, or None.

    Touching `last_used_at` is what makes a forgotten key visible later: a key
    nobody has used in a year is one to revoke, and without this column that is
    unanswerable.
    """
    if not key:
        return None
    row = session.scalar(
        select(ApiKey).where(ApiKey.key_hash == _hash(key), ApiKey.is_active.is_(True))
    )
    if row is None:
        return None
    user = session.get(User, row.user_id)
    if user is None or not user.is_active:
        return None
    row.last_used_at = datetime.now(timezone.utc)
    session.flush()
    return user
