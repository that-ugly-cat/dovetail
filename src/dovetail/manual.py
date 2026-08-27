"""Venues no index knows about.

*Future of Science and Ethics* exists, has a publisher, has an editorial board,
and Spit has a paper accepted there. OpenAlex does not index it. Without this
module the tool is blind on that journal — and before the fix in `cut` it was
worse, because the venue vanished silently instead of declaring itself
unclassifiable.

**Why this writes directly instead of proposing.** The proposal queue exists for
what an *agent* infers: a guideline read by a model, a lexical resolution. Here
the writer is a person looking at the journal, and it is the same person who
would approve. The verification stamp says `manual`, which is an attributed and
dated claim like any other — not a gap dressed up as data.

The cardinality is GrantRadar's: the journals that matter and that no index
covers are about a dozen, not twenty thousand. They are curated by hand once.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .provenance import stamp
from .models import Venue
from .sources.openalex import OpenAlexClient, derive_oa_model, short_id


def add_venue(
    session: Session,
    display_name: str,
    issn_l: str | None = None,
    homepage_url: str | None = None,
    host_organization_name: str | None = None,
    is_in_doaj: bool | None = None,
    is_oa: bool | None = None,
    apc_usd: int | None = None,
    anvur_class: str | None = None,
    note_url: str | None = None,
) -> Venue:
    """Create (or update) a hand-declared venue."""
    venue = None
    if issn_l:
        venue = session.scalar(select(Venue).where(Venue.issn_l == issn_l))
    if venue is None:
        venue = session.scalar(select(Venue).where(Venue.display_name == display_name))
    if venue is None:
        venue = Venue(display_name=display_name)
        session.add(venue)

    fields = {
        "display_name": display_name,
        "issn_l": issn_l,
        "homepage_url": homepage_url,
        "host_organization_name": host_organization_name,
        "is_in_doaj": is_in_doaj,
        "is_oa": is_oa,
        "apc_usd": apc_usd,
        "anvur_class": anvur_class,
    }
    fields = {k: v for k, v in fields.items() if v is not None}
    for k, v in fields.items():
        setattr(venue, k, v)

    venue.oa_model = derive_oa_model(
        {"is_in_doaj": venue.is_in_doaj, "is_oa": venue.is_oa, "apc_usd": venue.apc_usd}
    )
    # `is_core` stays None and not False: being outside OpenAlex's curated index
    # is not a quality judgement, and that is the difference between "I don't
    # know" and "no".
    session.flush()
    stamp(session, venue, list(fields) + ["oa_model"], "manual", note_url)
    return venue


def profile_from_texts(
    session: Session, client: OpenAlexClient, venue: Venue, articles: list[dict]
) -> dict:
    """Build the scope profile from the articles the journal **actually
    published**, classifying them one by one.

    Same idea as the embeddings in §5 — the profile comes from the corpus, not
    from a declared taxonomy — done with the instrument already at hand. It costs
    100 credits per article, so five to ten is the right size: the budget with a
    key buys ninety-nine a day.

    `articles` is a list of `{"title": ..., "abstract": ...}`. Take them from
    different years and make them varied: ten articles from the same special
    issue describe that issue, not the journal.
    """
    counts: dict[str, dict] = {}
    classified = 0

    for art in articles:
        payload = client.classify_text(session, art.get("title", ""), art.get("abstract", ""))
        classified += 1
        for t in payload.get("topics") or []:
            tid = short_id(t.get("id", ""))
            if not tid:
                continue
            row = counts.setdefault(
                tid,
                {
                    "id": tid,
                    "display_name": t.get("display_name"),
                    "count": 0,
                    "subfield": (t.get("subfield") or {}).get("display_name"),
                    "field": (t.get("field") or {}).get("display_name"),
                },
            )
            row["count"] += 1

    venue.topics = sorted(counts.values(), key=lambda r: -r["count"])
    # `topics_coverage` does not mean here what it means on OpenAlex: there it is
    # how much of the output the 25 returned topics cover, here the sample *is*
    # everything we know. Kept None so as not to make an unmeasured coverage look
    # measured.
    venue.topics_coverage = None
    venue.works_count = venue.works_count or None
    session.flush()
    stamp(session, venue, ["topics"], f"derived-from-{classified}-texts")
    return {
        "venue": venue.display_name,
        "articles_classified": classified,
        "topics_found": len(venue.topics),
        "credits_spent": classified * 100,
        "caveat": (
            "profile derived from a sample, not from an index: with few articles "
            "it describes those articles, not the journal"
        ),
    }


def mark_verified(session: Session, venue: Venue, by: str) -> None:
    now = datetime.now(timezone.utc)
    for v in venue.verifications:
        if v.source == "manual":
            v.verified_at = now
            v.source_url = v.source_url or f"verified by {by}"
    session.flush()
