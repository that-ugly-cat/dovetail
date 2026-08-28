#!/usr/bin/env python
"""Link a local account to a Borant ID subject, by hand, once.

The app deliberately never does this for you. Matching on email is how one
person ends up with another person's account, so when the gate vouches for a
subject whose address already belongs to a local user, the app makes a *separate*
profile and logs a line asking for this script.

    uv run python map_borant.py                       # show what exists
    uv run python map_borant.py <local-email> <sub>   # link them

Linking moves the subject onto the local account and deletes the profile the
gate created, keeping the local role. Nothing is guessed and everything it did
is printed.
"""

import sys

from sqlalchemy import select

from dovetail import db
from dovetail.models import User


def show(s):
    print(f"{'id':>4}  {'email':<34} {'role':<7} {'borant_sub'}")
    for u in s.scalars(select(User).order_by(User.id)):
        print(f"{u.id:>4}  {u.email:<34} {u.role.value:<7} {u.borant_sub or '—'}")


def link(s, email: str, sub: str) -> None:
    local = s.scalar(select(User).where(User.email == email.strip().lower()))
    if local is None:
        sys.exit(f"no local user {email}")
    ghost = s.scalar(select(User).where(User.borant_sub == sub))

    if ghost is not None and ghost.id == local.id:
        print(f"{email} is already linked to {sub}; nothing to do")
        return
    if local.borant_sub and local.borant_sub != sub:
        sys.exit(f"{email} is already linked to {local.borant_sub}; refusing to move it")

    local.borant_sub = sub
    print(f"linked {email} (role {local.role.value}) to subject {sub}")
    if ghost is not None:
        # The gate-made profile is a placeholder with no history of its own.
        print(f"removing the gate-made profile #{ghost.id} ({ghost.email})")
        s.delete(ghost)
    s.flush()


if __name__ == "__main__":
    db.init_engine()
    with db.session_scope() as session:
        if len(sys.argv) == 1:
            show(session)
        elif len(sys.argv) == 3:
            link(session, sys.argv[1], sys.argv[2])
            print()
            show(session)
        else:
            sys.exit(__doc__)
