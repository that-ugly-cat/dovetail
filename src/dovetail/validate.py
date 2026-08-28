"""RETIRED as validation, kept as diagnostics. See `validation.py` for what replaced it.

The design fails on the question, not on the code: rank-of-true-venue measures a
**rank**, which SPEC §0 says this output is not, and it compares two criteria
rather than one criterion against truth — a paper lands in a journal for
relationships, invitations and speed, which this tool does not model and should
not. Agreement here is therefore not evidence, and disagreement is not a fault.

It answers one question that is still worth asking: *is the true venue reachable
at all*. That is a fact about stage 2, and stage 2 is the part Phase 0 did
validate. Read nothing else off it.

The original docstring follows, because its statement of the circularity is
still correct and still applies to anyone tempted to revive this.

Phase 1b: does the scope score actually rank the right venue high?

The Phase 0 validation could only show that low scores track venues that
rejected the paper. It could never show the other half — that high scores track
venues that took it — because the dataset held no positive outcome. This does
the other half, on published papers whose real venue is known.

**The circularity, stated up front, because it inflates every number here.**
A journal's topic profile on OpenAlex is built from the works it published,
including the very paper being tested. So a published paper is close to
guaranteed to share topics with its own journal, and the rank of the true venue
is optimistic by construction.

Two things bound the damage and neither removes it:

- the effect is roughly one work against the journal's `works_count`, so it is
  negligible on a journal with thousands of works and material on a small one —
  `contamination` below reports that ratio per paper, so the reader can discount;
- the rank of the true venue is compared against the ranks of **all** other
  candidates, which are contaminated by nothing, so a true venue that fails to
  rank well despite the tailwind is a real negative result.

Read a good result here as "not falsified", never as "validated". A clean test
needs papers published after the profiles were built, and that is a later run.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .matching.pipeline import evaluate_all, generate_candidates
from .models import Venue
from .sources.openalex import OpenAlexClient, reconstruct_abstract, short_id


@dataclass
class PaperResult:
    doi: str
    title: str
    true_venue: str
    position: int | None
    total: int
    in_top: bool
    score_topic: float
    score_subfield: float
    contamination: float | None
    note: str = ""


def rank_of_true_venue(
    session: Session,
    client: OpenAlexClient,
    doi: str,
    top_n: int = 12,
    discover: bool = True,
) -> PaperResult:
    """Where does the journal that actually published this paper come out?"""
    work = client.work_by_doi(session, doi)
    title = work.get("title") or work.get("display_name") or ""
    abstract = reconstruct_abstract(work.get("abstract_inverted_index"))

    location = (work.get("primary_location") or {}).get("source") or {}
    true_id = short_id(location.get("id", ""))
    true_name = location.get("display_name") or "(unknown venue)"

    if not abstract:
        return PaperResult(
            doi, title, true_name, None, 0, False, 0.0, 0.0, None,
            "no abstract on OpenAlex: nothing to classify",
        )
    if not true_id:
        return PaperResult(
            doi, title, true_name, None, 0, False, 0.0, 0.0, None,
            "no primary source on the work: nothing to compare against",
        )

    payload = client.classify_text(session, title, abstract)
    topics = payload.get("topics") or []
    topic_ids = [short_id(t.get("id", "")) for t in topics if t.get("id")]

    if discover:
        generate_candidates(session, client, topic_ids)

    candidates = list(session.scalars(select(Venue)))
    rows = evaluate_all(session, topics, candidates, {}, None)
    ranked = sorted(
        (r for r in rows if not r.excluded),
        key=lambda r: r.score.sort_key(),
        reverse=True,
    )

    position = None
    row = None
    for i, r in enumerate(ranked, start=1):
        if r.venue.openalex_id == true_id:
            position, row = i, r
            break

    if row is None:
        return PaperResult(
            doi, title, true_name, None, len(ranked), False, 0.0, 0.0, None,
            "the true venue is not among the candidates at all",
        )

    works = row.venue.works_count or 0
    return PaperResult(
        doi=doi,
        title=title,
        true_venue=row.venue.display_name,
        position=position,
        total=len(ranked),
        in_top=position <= top_n,
        score_topic=row.score.topic,
        score_subfield=row.score.subfield,
        contamination=(1 / works) if works else None,
    )


def summarise(results: list[PaperResult], top_n: int = 12) -> dict:
    scored = [r for r in results if r.position is not None]
    if not scored:
        return {"papers": len(results), "scored": 0, "verdict": "nothing to conclude"}

    hits = [r for r in scored if r.in_top]
    ranks = sorted(r.position for r in scored)
    median = ranks[len(ranks) // 2]
    worst_contamination = max(
        (r.contamination for r in scored if r.contamination is not None), default=None
    )

    return {
        "papers": len(results),
        "scored": len(scored),
        f"true_venue_in_top_{top_n}": f"{len(hits)}/{len(scored)}",
        "median_position": median,
        "worst_position": ranks[-1],
        "worst_contamination": (
            f"{worst_contamination:.2%} of one journal's corpus is the paper itself"
            if worst_contamination
            else None
        ),
        "caveat": (
            "optimistic by construction: each journal's profile was built from its "
            "works, this paper included. Not falsified is the strongest reading."
        ),
    }
