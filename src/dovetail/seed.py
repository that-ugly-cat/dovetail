"""Seeding from the venues already used, taken from PaperTrail's vocabulary.

The strings are **verbatim**, typos included: `Medicine health care and
philosopy` with the missing *h*, `journal of moral education` in lowercase,
`neuroethics` without a capital. That is exactly why `VenueAlias` exists, and
copying them in fixed here would hide the problem the table has to solve.

The seed **does not create aliases**: it creates venues (OpenAlex's inventory is
a dated fact) and files an **alias proposal** for each resolution. A resolution
not confirmed by a human is not an alias — see the `VenueAlias` docstring and
SPEC.md §11.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .matching.pipeline import upsert_venue
from .models import Proposal, ProposalStatus, Venue, VenueAlias
from .sources.openalex import OpenAlexClient

# PaperTrail venue vocabulary, workspace `giovanni-spitale`, read 27 Aug 2026.
# Nineteen free strings.
PAPERTRAIL_VENUES: tuple[str, ...] = (
    "American Journal of Bioethics",
    "Behavioral Sciences & the Law",
    "Bioethics",
    "BMC Medical Ethics",
    "Future of Science and Ethics",
    "Heliyon",
    "Il Poligrafo",
    "International Journal of Ethics Education",
    "International Journal of Public Health",
    "JMIR Formative Research",
    "JMIR MHealth UHealth",
    "journal of medical humanities",
    "journal of moral education",
    "Medicine health care and philosopy",
    "NEJM Catalyst",
    "neuroethics",
    "New England Journal of Medicine",
    "npj Digital Medicine",
    "Philosophical Psychology",
)

# Not everything in the vocabulary is a journal: the corpus holds a book
# publisher (Il Poligrafo), and elsewhere arXiv and a paper that ended up on
# LinkedIn. Resolving those as journals would produce nonsense matches.
NOT_JOURNALS: frozenset[str] = frozenset({"Il Poligrafo"})

# ISSNs checked by hand against OpenAlex on 27 Aug 2026: for these we skip the
# lexical search, which is the part that gets things wrong.
KNOWN_ISSNS: dict[str, str] = {
    "Bioethics": "0269-9702",
    "BMC Medical Ethics": "1472-6939",
    "journal of moral education": "0305-7240",
    "Medicine health care and philosopy": "1386-7423",
}


def seed_venues(
    session: Session, client: OpenAlexClient, only: tuple[str, ...] | None = None
) -> dict:
    """Resolve PaperTrail's strings and file alias proposals.

    Costs 1 credit per string not present in `KNOWN_ISSNS`.
    """
    report = {
        "resolved": [],
        "skipped": [],
        "not_found": [],
        "already_known": [],
        "interrupted": [],
    }
    strings = only or PAPERTRAIL_VENUES

    for raw in strings:
        if raw in NOT_JOURNALS:
            report["skipped"].append({"string": raw, "reason": "not a journal"})
            continue

        existing = session.scalar(
            select(VenueAlias).where(
                VenueAlias.alias_string == raw, VenueAlias.source_system == "papertrail"
            )
        )
        if existing is not None:
            report["already_known"].append(raw)
            continue

        # A proposal still in the queue also counts as "already seen": re-filing
        # the same resolution on every run would fill the queue with duplicates
        # and turn approval into clearing a backlog.
        queued = session.scalar(
            select(Proposal).where(
                Proposal.kind == "new_alias",
                Proposal.status == ProposalStatus.PENDING,
                Proposal.fields["alias_string"].as_string() == raw,
            )
        )
        if queued is not None:
            report["already_known"].append(f"{raw} (proposal #{queued.id} queued)")
            continue

        # Each string is independent: an error on one must not lose the work done
        # on the others, and the budget can run out halfway down the list.
        try:
            if raw in KNOWN_ISSNS:
                src = client.source_by_issn(session, KNOWN_ISSNS[raw])
            else:
                hits = client.search_sources(session, raw, per_page=3).get("results") or []
                src = hits[0] if hits else None
        except Exception as e:  # budget, network, broken endpoint
            report["interrupted"].append({"string": raw, "error": f"{type(e).__name__}: {e}"})
            break

        if src is None:
            report["not_found"].append(raw)
            continue

        venue = upsert_venue(session, src)
        session.add(
            Proposal(
                kind="new_alias",
                venue_id=venue.id,
                fields={
                    "alias_string": raw,
                    "source_system": "papertrail",
                    "venue_id": venue.id,
                },
                rationale=(
                    f"PaperTrail's vocabulary contains «{raw}». "
                    + (
                        f"ISSN {KNOWN_ISSNS[raw]} checked by hand on 27 Aug 2026."
                        if raw in KNOWN_ISSNS
                        else "Resolved by lexical search on OpenAlex, which is the part "
                        "that gets things wrong: confirm by eye."
                    )
                ),
                confidence="high" if raw in KNOWN_ISSNS else "low",
                source_url=venue.homepage_url,
                status=ProposalStatus.PENDING,
            )
        )
        report["resolved"].append({"string": raw, "venue": venue.display_name})

    session.flush()
    return report


def approve_alias(session: Session, proposal_id: int, by: str) -> VenueAlias:
    """Approval lives in the UI; this is the function the UI will call, and it is
    here because Phase 1 has no UI yet."""
    from datetime import datetime, timezone

    p = session.get(Proposal, proposal_id)
    if p is None or p.kind != "new_alias":
        raise ValueError(f"proposal {proposal_id} is not an alias")
    if p.status is not ProposalStatus.PENDING:
        raise ValueError(f"proposal {proposal_id} is already {p.status.value}")

    alias = VenueAlias(
        alias_string=p.fields["alias_string"],
        venue_id=p.fields["venue_id"],
        source_system=p.fields["source_system"],
        confirmed_by=by,
        confirmed_at=datetime.now(timezone.utc),
    )
    session.add(alias)
    p.status = ProposalStatus.APPROVED
    session.flush()
    return alias


def venue_from_alias(session: Session, raw: str, source_system: str = "papertrail") -> Venue | None:
    """The only road from a PaperTrail string to a venue. No fuzzy matching on
    the fly: if the alias was never confirmed the answer is None, and the caller
    has to know that."""
    alias = session.scalar(
        select(VenueAlias).where(
            VenueAlias.alias_string == raw, VenueAlias.source_system == source_system
        )
    )
    return session.get(Venue, alias.venue_id) if alias else None
