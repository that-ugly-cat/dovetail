"""Symmetric encryption for per-user Anthropic API keys.

The house pattern, shared with AutoCode and LSSR: Fernet, with a server-side key
from `FERNET_KEY`. Keys are decrypted in memory only when a judgement runs, and
never logged.

**Why per-user and not one key on the box.** Stage 5 spends real money on a
model, and the convention for a borant app that spends is that the credential
belongs to the person spending: a judgement started from one account bills that
account and nobody else. It also means this box holds no Anthropic credential of
its own — the same argument that keeps `venue_history` open, settled the other
way because here there is a place to put the key.

**Lazy, unlike AutoCode's.** There the module reads the environment at import and
the app cannot start without it. Here stage 5 is optional — everything through
stage 4 works with no key at all — so a missing `FERNET_KEY` has to be a
disabled feature and not a dead process.
"""

from __future__ import annotations

import os
from functools import lru_cache


class CryptoUnavailable(RuntimeError):
    """No `FERNET_KEY`, so stored keys can be neither written nor read."""


@lru_cache(maxsize=1)
def _fernet():
    key = os.environ.get("FERNET_KEY")
    if not key:
        raise CryptoUnavailable(
            "FERNET_KEY is not set, so per-user Anthropic keys cannot be stored. "
            "Generate one with: python -c \"from cryptography.fernet import "
            'Fernet; print(Fernet.generate_key().decode())"'
        )
    from cryptography.fernet import Fernet

    return Fernet(key.encode())


def available() -> bool:
    """Whether this instance can store a key at all. Read at call time so a
    key added to the environment does not need a code change to take effect."""
    return bool(os.environ.get("FERNET_KEY"))


def encrypt_api_key(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_api_key(encrypted: str) -> str:
    return _fernet().decrypt(encrypted.encode()).decode()


def mask_api_key(plain: str) -> str:
    """Display form: only the last four characters survive.

    Enough to tell two keys apart, not enough to use one.
    """
    tail = plain[-4:] if len(plain) >= 4 else ""
    return f"sk-ant-…{tail}"
