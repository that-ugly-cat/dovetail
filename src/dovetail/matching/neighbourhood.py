"""The citation neighbourhood: a second way to a candidate. SPEC.md §6b.

Stage 2 reaches a journal through OpenAlex's topics, twice over: the manuscript
is classified into topics, and a journal is found if one of those sits among its
top twenty-five. Both halves read the **object** of a text. A normative paper
about a technology is classified by the technology, and matched to the journals
that build it rather than the ones that discuss it. Three real consultations in
2026 came back that way: the journals of the object, and none of those the
argument belonged to.

This module does not use the topics at all. It asks where the manuscript's own
bibliography is being read: for each reference, which journals published the
works that cite it. A journal reached through many different references, and
dense in them relative to what it publishes, is one where this conversation is
already happening.

What it measures and what it does not, stated once:

- It is a statement about the **bibliography**, not about the argument. A paper
  whose references are four-fifths about its subject gets the subject's
  journals, whatever its thesis. That is why the references are also read field by
  field (`by_field`): the minority conversations are listed on their own, where
  the majority cannot bury them.
- Density is citing links per thousand recent works. It is a concentration
  measure, like the cosine it sits beside, and it favours small specialist
  journals for the same reason. The breadth gate bounds that; it does not
  remove it.
- It is not merged with the scope score, and a journal found only here is put
  in a basket of its own. Two measures built on different evidence are not on
  one axis.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Callable

from sqlalchemy.orm import Session

from .. import config
from ..sources.openalex import OpenAlexClient, short_id

# A DOI, wherever it sits: bare, behind `doi:`, or inside a doi.org URL. The
# suffix is deliberately permissive — real ones carry parentheses, semicolons
# and angle brackets — and the tail is cleaned afterwards.
_DOI = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+", re.IGNORECASE)
_TRAILING = ".,;:)]}"


def extract_dois(text: str) -> list[str]:
    """Every DOI in a pasted reference list, lower-cased, in order, once each.

    A reference list is the input because it is what an author has to hand. The
    entries without a DOI — books, mostly — are simply not seen, and the report
    says how many DOIs there were, so a list that is mostly monographs shows up
    as a thin one instead of passing silently.
    """
    seen: dict[str, None] = {}
    for raw in _DOI.findall(text or ""):
        doi = raw.lower()
        while doi and doi[-1] in _TRAILING:
            # A closing bracket belongs to the DOI when it has an opening one:
            # 10.1016/s1701-2163(16)30050-0 ends in a digit, but
            # 10.1002/(sici)1097-0258(...) can end in `)`.
            if doi[-1] == ")" and doi.count("(") >= doi.count(")"):
                break
            doi = doi[:-1]
        # `|` and `,` are the filter's own separators; a DOI carrying one cannot
        # be sent and would corrupt the batch it travels in.
        if doi and "|" not in doi and "," not in doi:
            seen.setdefault(doi, None)
    return list(seen)


@dataclass
class Evidence:
    """What ties one journal to the manuscript's bibliography."""

    openalex_id: str
    refs_reached: int
    """Distinct references whose citing works this journal published."""
    refs_total: int
    citing_links: int
    """Citation links, not works: a paper citing three of the references counts
    three. It is the numerator of the density and nothing else."""
    recent_works: int
    per_1000: float
    cited_here: int = 0
    """References that were themselves published in this journal."""
    via_field: str | None = None
    """Set on a minority-conversation row: the field of the references that
    lead here. The counts above are then within that field only."""

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class Neighbourhood:
    report: dict
    overall: list[Evidence] = field(default_factory=list)
    by_field: dict[str, list[Evidence]] = field(default_factory=dict)

    def find(self, openalex_id: str) -> Evidence | None:
        for ev in self.overall:
            if ev.openalex_id == openalex_id:
                return ev
        return None


def window_start(today: date | None = None) -> str:
    today = today or date.today()
    return f"{today.year - config.NEIGHBOURHOOD_YEARS_BACK}-01-01"


def breadth_gate(n_references: int) -> int:
    return max(2, math.ceil(config.NEIGHBOURHOOD_MIN_BREADTH_SHARE * n_references))


def recent_works(source: dict, since_year: int) -> int:
    return sum(
        c.get("works_count", 0)
        for c in (source.get("counts_by_year") or [])
        if (c.get("year") or 0) >= since_year
    )


def tally(
    citers: dict[str, list[tuple[str, int]]], work_ids: list[str]
) -> tuple[Counter, Counter]:
    """(breadth, links) per journal, over a subset of the references."""
    breadth: Counter = Counter()
    links: Counter = Counter()
    for wid in work_ids:
        for source_id, n in citers.get(wid, []):
            if source_id and n:
                breadth[source_id] += 1
                links[source_id] += n
    return breadth, links


def rank(
    citers: dict[str, list[tuple[str, int]]],
    work_ids: list[str],
    recent: dict[str, int],
    cited_here: Counter,
    gate: int,
    via_field: str | None = None,
) -> list[Evidence]:
    """Journals past the breadth gate, densest first.

    Pure: everything it needs was fetched by the caller. A journal with no
    recent-output figure is left out rather than given a density of its own
    invention — the figure comes from a source record, and a missing record
    means the fetch was cut short, which the report says.
    """
    breadth, links = tally(citers, work_ids)
    out = []
    for source_id, reached in breadth.items():
        if reached < gate:
            continue
        works = recent.get(source_id) or 0
        if works < config.NEIGHBOURHOOD_MIN_RECENT_WORKS:
            continue
        out.append(
            Evidence(
                openalex_id=source_id,
                refs_reached=reached,
                refs_total=len(work_ids),
                citing_links=links[source_id],
                recent_works=works,
                per_1000=round(1000 * links[source_id] / works, 2),
                cited_here=cited_here.get(source_id, 0),
                via_field=via_field,
            )
        )
    out.sort(key=lambda e: (e.per_1000, e.refs_reached), reverse=True)
    return out


def minority_fields(field_of: dict[str, str]) -> list[tuple[str, list[str]]]:
    """Fields worth reading on their own, largest first, with their references."""
    groups: dict[str, list[str]] = {}
    for wid, name in field_of.items():
        if name:
            groups.setdefault(name, []).append(wid)
    total = len(field_of) or 1
    picked = [
        (name, wids)
        for name, wids in groups.items()
        if len(wids) >= config.MIN_FIELD_REFERENCES
        and len(wids) / total < config.MAJORITY_FIELD_SHARE
    ]
    picked.sort(key=lambda kv: len(kv[1]), reverse=True)
    return picked


def build(
    session: Session,
    client: OpenAlexClient,
    dois: list[str],
    upsert: Callable[[Session, dict], object],
    today: date | None = None,
) -> Neighbourhood:
    """Fetch, count, and hand back the journals this bibliography leads to.

    `upsert` writes each source record into the venue table, so the journals
    found here become candidates like any other and go through the same
    constraints. It is passed in because it lives in the pipeline, which imports
    this module.
    """
    given = len(dois)
    dois = dois[: config.MAX_REFERENCES]
    since = window_start(today)
    report: dict = {
        "references_given": given,
        "references_used": len(dois),
        "window_since": since,
    }

    works = client.works_by_dois(session, dois) if dois else []
    report["references_resolved"] = len(works)
    if len(works) < config.MIN_REFERENCES:
        report["skipped"] = "too_few_references"
        return Neighbourhood(report)

    cited_here: Counter = Counter()
    field_of: dict[str, str] = {}
    for w in works:
        wid = short_id(w.get("id", ""))
        source = (w.get("primary_location") or {}).get("source") or {}
        if source.get("id"):
            cited_here[short_id(source["id"])] += 1
        field_of[wid] = (
            ((w.get("primary_topic") or {}).get("field") or {}).get("display_name") or ""
        )

    citers: dict[str, list[tuple[str, int]]] = {}
    citing_total = 0
    for wid in field_of:
        got = client.sources_citing(session, wid, since)
        citers[wid] = got["groups"]
        citing_total += got["count"]
    report["citing_works"] = citing_total

    all_ids = list(field_of)
    gate = breadth_gate(len(all_ids))
    fields = minority_fields(field_of)

    # Which journals need a source record: everything past the overall gate,
    # plus everything past a minority field's own (lower) gate. Breadth is known
    # before any record is fetched, so the fetch can be bounded by it.
    breadth_all, _ = tally(citers, all_ids)
    wanted = Counter({s: b for s, b in breadth_all.items() if b >= gate})
    for _name, wids in fields:
        b, _ = tally(citers, wids)
        for s, n in b.items():
            if n >= 2:
                wanted[s] = max(wanted[s], breadth_all[s])
    ordered = [s for s, _ in wanted.most_common()]
    fetch = ordered[: config.NEIGHBOURHOOD_MAX_SOURCES]
    report["sources_past_gate"] = len(ordered)
    report["sources_fetched"] = len(fetch)

    since_year = int(since[:4])
    recent: dict[str, int] = {}
    for src in client.sources_by_ids(session, fetch):
        upsert(session, src)
        recent[short_id(src.get("id", ""))] = recent_works(src, since_year)
    session.flush()

    overall = rank(citers, all_ids, recent, cited_here, gate)
    by_field = {
        name: rank(citers, wids, recent, cited_here, 2, via_field=name)
        for name, wids in fields
    }
    report["breadth_gate"] = gate
    report["venues_past_gate"] = len(overall)
    report["fields"] = dict(Counter(v for v in field_of.values() if v).most_common())
    report["minority_fields"] = [name for name, _ in fields]
    return Neighbourhood(report, overall, by_field)
