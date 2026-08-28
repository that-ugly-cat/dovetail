"""Turning a proposal into a fact.

This is the only place in the codebase where something suggested becomes
something the tool will repeat as true, which is why it is small, explicit, and
reachable from exactly one route behind `require_admin`.

Three kinds, and they differ in what they touch:

- `new_alias` binds a free string from another system to a venue. Until it is
  approved there is no alias at all, and the resolver answers None rather than
  guessing — see `seed.venue_from_alias`.
- `new_venue` creates a hand-declared journal.
- `update_venue` writes named fields onto an existing one.

An approved proposal is not deleted. It carries who approved it and when, which
is the only way to answer "where did this word limit come from" six months later.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .models import Proposal, ProposalStatus, Venue
from .provenance import stamp

# Fields a proposal may write. An allowlist and not a blocklist: a proposal that
# could set `id` or `openalex_id` would let the queue rewrite identity, and the
# queue exists to add knowledge, not to repoint records at each other.
WRITABLE = {
    "display_name",
    "issn_l",
    "homepage_url",
    "host_organization_name",
    "is_oa",
    "is_in_doaj",
    "apc_usd",
    "anvur_class",
    "indexed_in",
}


class ProposalError(RuntimeError):
    pass


def approve(session: Session, proposal_id: int, by: str) -> dict:
    p = session.get(Proposal, proposal_id)
    if p is None:
        raise ProposalError(f"no proposal {proposal_id}")
    if p.status is not ProposalStatus.PENDING:
        raise ProposalError(f"proposal {proposal_id} is already {p.status.value}")

    if p.kind == "new_alias":
        from .seed import approve_alias

        alias = approve_alias(session, proposal_id, by)
        return {"kind": p.kind, "alias": alias.alias_string, "venue_id": alias.venue_id}

    if p.kind == "new_venue":
        fields = {k: v for k, v in (p.fields or {}).items() if k in WRITABLE}
        if not fields.get("display_name"):
            raise ProposalError("a venue proposal with no display_name cannot be approved")
        venue = Venue(display_name=fields["display_name"])
        session.add(venue)
        for k, v in fields.items():
            setattr(venue, k, v)
        session.flush()
        stamp(session, venue, list(fields), f"approved:{by}", p.source_url)
        _close(session, p, by)
        return {"kind": p.kind, "venue_id": venue.id, "fields": sorted(fields)}

    if p.kind == "update_venue":
        venue = session.get(Venue, p.venue_id) if p.venue_id else None
        if venue is None:
            raise ProposalError(f"proposal {proposal_id} points at no existing venue")
        fields = {k: v for k, v in (p.fields or {}).items() if k in WRITABLE}
        ignored = sorted(set(p.fields or {}) - set(fields))
        for k, v in fields.items():
            setattr(venue, k, v)
        session.flush()
        if fields:
            stamp(session, venue, list(fields), f"approved:{by}", p.source_url)
        _close(session, p, by)
        # Ignored keys are reported, not silently dropped: an approver who
        # believed they were setting something needs to know they were not.
        return {"kind": p.kind, "venue_id": venue.id, "written": sorted(fields), "ignored": ignored}

    raise ProposalError(f"unknown proposal kind {p.kind!r}")


def reject(session: Session, proposal_id: int, by: str) -> dict:
    p = session.get(Proposal, proposal_id)
    if p is None:
        raise ProposalError(f"no proposal {proposal_id}")
    if p.status is not ProposalStatus.PENDING:
        raise ProposalError(f"proposal {proposal_id} is already {p.status.value}")
    p.status = ProposalStatus.REJECTED
    p.rationale = f"{p.rationale}\n\n[rejected by {by} on {_now()}]"
    session.flush()
    return {"proposal_id": proposal_id, "status": "rejected"}


def _close(session: Session, p: Proposal, by: str) -> None:
    p.status = ProposalStatus.APPROVED
    p.rationale = f"{p.rationale}\n\n[approved by {by} on {_now()}]"
    session.flush()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
