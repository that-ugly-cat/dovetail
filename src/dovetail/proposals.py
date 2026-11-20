"""Turning a proposal into a fact.

This is the only place in the codebase where something suggested becomes
something the tool will repeat as true, which is why it is small, explicit, and
reachable from exactly one route behind `require_admin`.

Three kinds, and they differ in what they touch:

- `new_alias` binds a free string from another system to a venue. Until it is
  approved there is no alias at all, and the resolver answers None rather than
  guessing — see `seed.venue_from_alias`.
- `new_venue` creates a hand-declared journal.
- `update_venue` writes named fields onto an existing one — and, under the key
  `article_types`, the article types read off its author guidelines.

That second half did not exist until 20 Nov 2026. `propose_update` had always
advertised itself as the way to file «article types and word limits», and ten
such proposals were filed on 8 Sep 2026; but approval only knew the venue's own
columns, so approving one reported the article types as ignored and wrote
nothing. The table the format criterion reads had no way of being filled.

An approved proposal is not deleted. It carries who approved it and when, which
is the only way to answer "where did this word limit come from" six months later.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .matching.guidelines import ARTICLE_TYPE_FIELDS
from .models import ArticleType, Proposal, ProposalStatus, Venue
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
        ignored = sorted(set(p.fields or {}) - set(fields) - {ARTICLE_TYPES_KEY})
        for k, v in fields.items():
            setattr(venue, k, v)
        session.flush()
        if fields:
            stamp(session, venue, list(fields), f"approved:{by}", p.source_url)
        types = _write_article_types(
            session, venue, (p.fields or {}).get(ARTICLE_TYPES_KEY), p.source_url
        )
        _close(session, p, by)
        # Ignored keys are reported, not silently dropped: an approver who
        # believed they were setting something needs to know they were not.
        return {
            "kind": p.kind,
            "venue_id": venue.id,
            "written": sorted(fields),
            "ignored": ignored,
            "article_types": types,
        }

    raise ProposalError(f"unknown proposal kind {p.kind!r}")


ARTICLE_TYPES_KEY = "article_types"


def _write_article_types(session: Session, venue: Venue, items, source_url) -> dict:
    """Write the article types a proposal carries. Returns what happened to each.

    A type is matched on its name, case aside, and **replaced field by field**:
    guidelines change, and reading the same page next year must update the row
    rather than leave two «Original Article» entries with different limits. A
    type the proposal does not mention is left alone — one page rarely lists
    everything, and nothing here deletes.

    An item that cannot be written is reported with the reason. That covers the
    proposals filed before this existed, whose shape nobody checked against
    anything: better a list of what was skipped than a silent partial write.
    """
    if items is None:
        return {"written": [], "skipped": []}
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        return {"written": [], "skipped": [{"item": repr(items)[:80], "why": "not a list"}]}

    written, skipped = [], []
    existing = {
        t.name.strip().lower(): t
        for t in session.query(ArticleType).filter(ArticleType.venue_id == venue.id)
    }
    for item in items:
        if not isinstance(item, dict) or not str(item.get("name") or "").strip():
            skipped.append({"item": repr(item)[:80], "why": "no name"})
            continue
        name = str(item["name"]).strip()
        limit, scope = item.get("word_limit"), item.get("word_limit_scope")
        if limit is not None and not str(scope or "").strip():
            # The rule of `matching.guidelines`, enforced where it becomes a
            # fact: a limit with no statement of what it counts is not written.
            skipped.append({"item": name, "why": "word_limit without word_limit_scope"})
            continue
        unknown = sorted(set(item) - set(ARTICLE_TYPE_FIELDS) - {"name", "evidence"})
        row = existing.get(name.lower())
        if row is None:
            row = ArticleType(venue_id=venue.id, name=name)
            session.add(row)
            existing[name.lower()] = row
        for key in ARTICLE_TYPE_FIELDS:
            if key in item:
                setattr(row, key, item[key])
        row.source_url = source_url
        row.verified_at = datetime.now(timezone.utc)
        written.append({"name": name, "ignored_keys": unknown} if unknown else {"name": name})
    session.flush()
    return {"written": written, "skipped": skipped}


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
