"""Whose a consultation is, and who may start one.

Journals are everybody's: one table, filled by every consultation and read by
all. A consultation is its owner's. It holds the title of a manuscript that has
not been published, the topics it was read as, its bibliography and the list of
journals it might go to, and until 20 Nov 2026 every signed-in user could read
every one of them — which nobody noticed with one account and would have been
the first thing noticed with two.

Three rules, decided by the person the tool is for.

**A consultation is seen by whoever started it, and by nobody else.** That
includes admins. Running the instance does not require reading other people's
manuscripts, so an admin gets counts and credits per person and nothing more.

**A consultation with no owner belongs to the instance.** One started from the
command line has nobody's name on it; admins see those, because somebody has
to be able to.

**Whoever has their own OpenAlex key may start one, and nobody else.** The rule
used to be «an admin», and the reason was the budget: a consultation spent what
everybody shared. That budget is gone. Each person pays for their own work with
their own key, admins included, so no role is needed to start a consultation
and no role is enough to start one without a key.

Everything that reads a consultation goes through `visible_to`, and nothing
relies on a page not linking to it: a consultation that is not yours answers
exactly like one that does not exist.
"""

from __future__ import annotations

from sqlalchemy import or_, true
from sqlalchemy.orm import Session

from . import crypto
from .models import MatchRun, User


def visible_to(user: User | None):
    """The condition, for a query, on the consultations `user` may read.

    `None` is a caller with no identity: the command line, or MCP over stdio.
    Both are a process started by whoever owns the box and can read the
    database file directly, so pretending to hide rows from it would protect
    nothing.
    """
    if user is None:
        return true()
    mine = MatchRun.user_id == user.id
    if user.is_admin():
        return or_(mine, MatchRun.user_id.is_(None))
    return mine


def run_for(s: Session, run_id: int, user: User | None) -> MatchRun | None:
    """The consultation, if it exists **and** is this person's to read.

    One answer for «no such consultation» and «not yours», on purpose: a
    different answer for the second would say that the first is false.
    """
    run = s.get(MatchRun, run_id)
    if run is None:
        return None
    if user is None or run.user_id == user.id:
        return run
    if run.user_id is None and user.is_admin():
        return run
    return None


def has_own_openalex_key(user: User | None) -> bool:
    return bool(user is not None and user.openalex_key_encrypted and crypto.available())


def may_consult(user: User | None) -> bool:
    """Whether this person may start something that spends OpenAlex credits: a
    consultation, the check on its journals, describing a journal from its
    articles. A key of their own, whatever their role. The command line has the
    operator's."""
    return user is None or has_own_openalex_key(user)


WHY_NOT = (
    "This needs your own OpenAlex key, which is free: Settings says how to get "
    "one. Everything that spends OpenAlex credits here is paid for by the "
    "person who starts it."
)
