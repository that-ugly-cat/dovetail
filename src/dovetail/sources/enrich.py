"""DOAJ enrichment, applied **to the finalists only**.

Why not during stage 2: candidate generation returns up to 200 venues, and one
DOAJ call each would make every consultation slow for data that only matters
about the dozen rows a human will actually read. DOAJ is free, so this is a
latency decision, not a cost one.

It is also the same rule the whole tool runs on: broad and cheap first, precise
and expensive on what survives. Here it just happens one stage earlier than the
guidelines reading of stage 5.

Note that this only ever touches journals DOAJ knows about — `is_in_doaj` is
true — which under the SNSF constraint is exactly the set that matters.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..provenance import stamp
from ..models import Venue
from .doaj import DoajClient, normalize_journal, reconcile_apc


def enrich_from_doaj(
    session: Session, client: DoajClient, venues: list[Venue]
) -> dict:
    """Fill in the DOAJ half of the record: licence, review process, declared
    time to publication, waiver, and the publisher's own APC.

    Failures are per venue and never abort the run: a consultation that works
    beats one that dies because a journal is missing from DOAJ.
    """
    report = {"enriched": [], "not_in_doaj": [], "failed": [], "disagreements": []}

    for venue in venues:
        if not venue.issn_l or venue.is_in_doaj is not True:
            continue
        try:
            record = client.journal_by_issn(venue.issn_l)
        except Exception as e:
            report["failed"].append({"venue": venue.display_name, "error": str(e)})
            continue

        if record is None:
            # OpenAlex says it is in DOAJ and DOAJ does not have it. That is a
            # disagreement between two sources, not a missing field, and it is
            # worth surfacing rather than silently leaving the record half full.
            report["not_in_doaj"].append(venue.display_name)
            continue

        fields = normalize_journal(record)
        for k, v in fields.items():
            if v is not None:
                setattr(venue, k, v)

        apc = reconcile_apc(venue.apc_usd, venue.doaj_apc)
        if apc["disagreement"]:
            # Do not overwrite: keep both and say so. On either side of a
            # threshold that difference decides the shortlist.
            report["disagreements"].append(
                {"venue": venue.display_name, **apc["disagreement"]}
            )
        elif apc["usd"] is not None:
            venue.apc_usd = apc["usd"]

        session.flush()
        stamp(
            session,
            venue,
            [k for k, v in fields.items() if v is not None] + ["apc_usd"],
            "doaj",
            f"https://doaj.org/api/search/journals/issn:{venue.issn_l}",
        )
        report["enriched"].append(venue.display_name)

    session.flush()
    return report
