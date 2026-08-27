"""Who said what about a venue, and when.

This lives on its own because three modules write venue fields — OpenAlex
ingestion, DOAJ enrichment, and hand-declared venues — and each has to leave the
same kind of trace. It used to be a private helper inside the pipeline, which
made `manual.py` reach for `_stamp` across a module boundary and gave
`sources/enrich.py` a circular import the moment it needed the same thing.

The rule it enforces is the one in SPEC.md §10: freshness belongs to a **field**,
not to a record. A journal can have yesterday's topics and an eight-month-old
word limit.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import FieldVerification, Venue


def stamp(
    session: Session,
    venue: Venue,
    fields,
    source: str,
    url: str | None = None,
) -> None:
    """Record that `fields` of `venue` were verified now, from `source`.

    `source` is an attribution and not a category: `openalex`, `doaj`, `manual`,
    `derived-from-9-texts`. Reading it back has to tell you what kind of claim
    the value is, because "a human read this off the journal's website" and "an
    index returned it" age differently and fail differently.
    """
    now = datetime.now(timezone.utc)
    existing = {
        v.field_name: v
        for v in session.scalars(
            select(FieldVerification).where(FieldVerification.venue_id == venue.id)
        )
    }
    for name in fields:
        row = existing.get(name)
        if row is None:
            row = FieldVerification(venue_id=venue.id, field_name=name)
            session.add(row)
        row.verified_at = now
        row.source = source
        row.source_url = url
