"""Phase 1b, redesigned. What can actually be measured about this list.

The first design asked *where does the journal that really published this paper
come out?* and it does not work — not because the code was wrong but because the
question is. Two reasons, and the second is the one that kills it.

**It belongs to an ontology the tool has abandoned.** Rank-of-true-venue measures
a position in an ordered list. SPEC §0 says the output is not a ranking: the
score is computed against one paper, the set is whatever the sweep reached that
day, and «#447 of 3810» is an artefact of how many candidates were fetched.
Measuring a rank inside that denominator inherits a meaning the tool has
disowned.

**And it compares two different criteria, not one criterion against truth.** A
paper lands in a journal for relationships, invitations, speed and institutional
membership — none of which the tool models, and none of which it should. So a
disagreement cannot be read: it may mean the matcher is wrong, or it may mean the
matcher optimises something other than what drove the historical choice. The
measurement cannot separate those, which is what makes it a bad measurement
rather than a discouraging one. Measured: *International Journal of Public
Health* has 52 works on a paper's topics against journals with hundreds of
thousands, and does not surface under any query — that paper went there because
somebody asked.

What replaces it are two measurements that do not have this problem.

**1. Known negatives.** The clean ground truth is not «this journal said yes»,
which is contaminated by everything above. It is **«this journal said no, out of
scope»** — a desk reject motivated on fit is the journal's own statement about
the thing the tool models. A tool that puts such a venue in a shortlist is wrong
in a way nobody has to interpret.

**2. Precision at twelve, judged blind.** The product's promise is a list worth
looking at, so the measurement is whether the list is worth looking at. Twelve
finalists are shuffled with twelve decoys drawn from as close to the manuscript
as the table allows, stripped of score and order, and a person marks each one
«would consider» or not. **The decoy rate is the control and the whole point**:
if decoys are accepted as often as finalists, the list is doing nothing, and no
amount of agreement on the finalists alone would have shown it.

The corollary, learned from the first real sheet: **the measurement is only as
good as how hard the decoys are.** Drawing on field alone put *Learning
Disability Quarterly* against a paper on moderating health misinformation, and a
decoy rejected on sight is a free point rather than a control. Drawing on
subfield gives *Digital Health* and *Informatics for Health and Social Care*,
which is a question worth asking a person.

Neither needs the tool to be a ranking. Both are cheap: (1) costs one
classification per paper, (2) costs nothing at all.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .models import MatchResult, MatchRun, Venue


# --- 1. known negatives ---------------------------------------------------


@dataclass
class NegativeCase:
    """One journal that said no, and why we believe the no was about fit.

    `reason` is free text quoted from the rejection. It is not parsed — it is
    there so a reader can check that the case belongs in this file at all. A
    desk reject for length, or for being out of format, says nothing about scope
    and must not be counted here.
    """

    paper: str
    venue_name: str
    venue_openalex_id: str | None
    reason: str


@dataclass
class NegativeOutcome:
    paper: str
    venue_name: str
    verdict: str  # "unreachable" | "below_cut" | "shortlisted"
    detail: str

    @property
    def caught(self) -> bool:
        """Both non-shortlisted outcomes count, and they are not the same.

        `unreachable` is the strong one: stage 2 would never have produced the
        venue, so the tool could not have suggested it however the scores fell.
        `below_cut` is weaker — it was produced and then not chosen — but it is
        still the tool declining to suggest a journal that went on to say no.
        """
        return self.verdict != "shortlisted"


def check_negatives(
    session: Session,
    text_topics: list[dict],
    cases: list[NegativeCase],
    shortlist_venue_ids: set[int],
    reachable_venue_ids: set[int],
) -> list[NegativeOutcome]:
    """Where did each rejecting venue land, for a run already computed?

    Deliberately takes a finished run rather than doing the matching itself: the
    thing under test is what Dovetail would have *told you*, and re-deriving it
    here would be measuring a second implementation.
    """
    out = []
    for case in cases:
        venue = None
        if case.venue_openalex_id:
            venue = session.scalar(
                select(Venue).where(Venue.openalex_id == case.venue_openalex_id)
            )
        if venue is None:
            venue = session.scalar(
                select(Venue).where(Venue.display_name == case.venue_name)
            )
        if venue is None:
            out.append(
                NegativeOutcome(
                    case.paper, case.venue_name, "unreachable",
                    "not in the table at all: the sweep never produced it",
                )
            )
            continue
        if venue.id in shortlist_venue_ids:
            out.append(
                NegativeOutcome(
                    case.paper, case.venue_name, "shortlisted",
                    f"suggested, and it went on to say: {case.reason[:90]}",
                )
            )
        elif venue.id not in reachable_venue_ids:
            out.append(
                NegativeOutcome(
                    case.paper, case.venue_name, "unreachable",
                    "shares no topic with the text, so stage 2 cannot produce it",
                )
            )
        else:
            out.append(
                NegativeOutcome(
                    case.paper, case.venue_name, "below_cut",
                    "scored but not shortlisted",
                )
            )
    return out


def summarise_negatives(outcomes: list[NegativeOutcome]) -> dict:
    caught = [o for o in outcomes if o.caught]
    missed = [o for o in outcomes if not o.caught]
    return {
        "cases": len(outcomes),
        "caught": f"{len(caught)}/{len(outcomes)}" if outcomes else "0/0",
        "unreachable": sum(1 for o in outcomes if o.verdict == "unreachable"),
        "below_cut": sum(1 for o in outcomes if o.verdict == "below_cut"),
        "shortlisted_anyway": [f"{o.paper} → {o.venue_name}" for o in missed],
        "reading": (
            "A journal that rejected a paper on scope is the journal's own "
            "statement about fit. Catching it is the one direction of this "
            "validation that nothing contaminates — but it bounds the false "
            "positives only. It says nothing about whether the journals the tool "
            "does suggest are good ones; that is what the blind sheet is for."
        ),
    }


# --- 2. precision at twelve, judged blind ---------------------------------


@dataclass
class SheetRow:
    n: int
    venue_id: int
    display_name: str
    publisher: str | None
    oa_model: str | None
    apc_usd: int | None
    is_decoy: bool = field(repr=False, default=False)


def blind_sheet(
    session: Session,
    run: MatchRun,
    seed: int,
    decoys_per_finalist: int = 1,
) -> list[SheetRow]:
    """Finalists and decoys, shuffled, stripped of everything that gives it away.

    What is stripped matters as much as what is kept. **No score, no position,
    no criteria, no flags** — anything the tool computed would tell the reader
    which rows are its own, and the answer would then measure agreement with a
    label rather than judgement of a journal. What is left is what a person
    would see glancing at a journal: its name, who publishes it, whether it is
    open access and what it charges.

    Decoys are drawn from the same **subfield where possible**, falling back to
    the field, and never at random from sixteen thousand. The reason showed up
    in the first real sheet: drawing on field alone put *Learning Disability
    Quarterly* against a paper on moderating health misinformation, and a decoy
    that is rejected on sight is not a control — it is a free point, and enough
    of them would make any list look discriminating. **The measurement is only
    as good as how hard the decoys are**, so they are ordered by how much they
    share with the manuscript and the closest are taken.

    `seed` is explicit and recorded, so the sheet is reproducible and the answers
    can be scored later without keeping state in between.
    """
    finalists = list(
        session.scalars(
            select(MatchResult)
            .where(MatchResult.run_id == run.id, MatchResult.bucket == "shortlist")
            .order_by(MatchResult.position)
        )
    )
    if not finalists:
        return []

    chosen_ids = {r.venue_id for r in finalists}
    fields, subfields = _fields_of(run), _subfields_of(run)
    pool = [
        v
        for v in session.scalars(
            select(Venue).where(Venue.is_core.is_(True), Venue.topics.isnot(None))
        )
        if v.id not in chosen_ids and _fields_of_venue(v) & fields
    ]

    rng = random.Random(seed)
    n_decoys = min(len(pool), len(finalists) * decoys_per_finalist)
    # Shuffle first, then sort by closeness: the shuffle keeps the choice
    # reproducible-but-arbitrary among equally close candidates, and the sort
    # takes the hardest ones. Sorting a pool that arrived in table order would
    # make the sheet depend on insertion order rather than on the seed.
    rng.shuffle(pool)
    pool.sort(key=lambda v: -len(_subfields_of_venue(v) & subfields))
    decoys = pool[:n_decoys]

    rows = [
        (session.get(Venue, r.venue_id), False) for r in finalists
    ] + [(v, True) for v in decoys]
    rng.shuffle(rows)

    return [
        SheetRow(
            n=i,
            venue_id=v.id,
            display_name=v.display_name,
            publisher=v.host_organization_name,
            oa_model=getattr(v.oa_model, "value", v.oa_model),
            apc_usd=v.apc_usd,
            is_decoy=decoy,
        )
        for i, (v, decoy) in enumerate(rows, start=1)
    ]


def _fields_of(run: MatchRun) -> set[str]:
    return {
        (t.get("field") or {}).get("display_name")
        for t in ((run.text_profile or {}).get("topics") or [])
    } - {None}


def _fields_of_venue(venue: Venue) -> set[str]:
    return {t.get("field") for t in (venue.topics or [])} - {None}


def _subfields_of(run: MatchRun) -> set[str]:
    return {
        (t.get("subfield") or {}).get("display_name")
        for t in ((run.text_profile or {}).get("topics") or [])
    } - {None}


def _subfields_of_venue(venue: Venue) -> set[str]:
    return {t.get("subfield") for t in (venue.topics or [])} - {None}


def score_sheet(rows: list[SheetRow], marks: dict[int, bool]) -> dict:
    """Precision on the finalists, against the same judge's rate on the decoys.

    The second number is not a footnote. A judge who says yes to everything
    scores 12/12 on the finalists, and only the decoy rate shows it — so the two
    are reported together and the difference between them is the finding. If
    they are equal, the list added nothing that a plausible journal from the
    same field would not have added by accident.
    """
    finalists = [r for r in rows if not r.is_decoy and r.n in marks]
    decoys = [r for r in rows if r.is_decoy and r.n in marks]
    if not finalists:
        return {"judged": 0, "verdict": "nothing marked"}

    hit = sum(1 for r in finalists if marks[r.n])
    decoy_hit = sum(1 for r in decoys if marks[r.n])
    p_finalists = hit / len(finalists)
    p_decoys = (decoy_hit / len(decoys)) if decoys else None

    return {
        "finalists_judged": len(finalists),
        "finalists_accepted": f"{hit}/{len(finalists)}",
        "precision_at_n": round(p_finalists, 3),
        "decoys_judged": len(decoys),
        "decoys_accepted": f"{decoy_hit}/{len(decoys)}" if decoys else None,
        "decoy_rate": round(p_decoys, 3) if p_decoys is not None else None,
        "lift": round(p_finalists - p_decoys, 3) if p_decoys is not None else None,
        "unmarked": [r.n for r in rows if r.n not in marks],
        "reading": (
            "The lift is the finding, not the precision. A judge who accepts "
            "everything scores perfectly on the finalists; only the decoy rate "
            "shows it. A lift near zero means the list is indistinguishable from "
            "plausible journals of the same field picked at random."
        ),
    }


def sheet_markdown(rows: list[SheetRow], run: MatchRun, seed: int) -> str:
    """The sheet a person fills in. Ordered as shuffled, and giving nothing away."""
    lines = [
        f"# Blind sheet — consultation {run.id}",
        "",
        f"**{run.title}**",
        "",
        "For each journal below, mark **y** if you would consider submitting this",
        "manuscript there and **n** if you would not. Answer from the journal, not",
        "from the order: the rows are shuffled and some of them are not Dovetail's.",
        "",
        "Do not look the journals up. The question is whether the name and these few",
        "facts are enough to make it a candidate worth an hour of your time.",
        "",
        f"Seed `{seed}` — needed to score this sheet.",
        "",
        "| # | y/n | Journal | Publisher | Access | APC |",
        "|---|-----|---------|-----------|--------|-----|",
    ]
    for r in rows:
        apc = f"{r.apc_usd:,}" if r.apc_usd else "—"
        oa = (r.oa_model or "—").replace("_", " ")
        lines.append(
            f"| {r.n} |  | {r.display_name} | {r.publisher or '—'} | {oa} | {apc} |"
        )
    lines += [
        "",
        "---",
        "",
        "Scoring needs the same seed and the same consultation, so the sheet can be",
        "rebuilt and the decoys identified. Nothing on this page says which is which.",
    ]
    return "\n".join(lines)


def parse_marks(text: str) -> dict[int, bool]:
    """Read a filled sheet back.

    Accepts the markdown table with y/n in the second column, and also the
    shorthand `1y 2n 3y`, because a person scoring twenty-four rows will
    reasonably not want to edit a table.
    """
    marks: dict[int, bool] = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 2 and cells[0].isdigit() and cells[1].lower() in {"y", "n"}:
                marks[int(cells[0])] = cells[1].lower() == "y"
            continue
        for token in line.replace(",", " ").split():
            body = token.lower()
            if len(body) >= 2 and body[:-1].isdigit() and body[-1] in "yn":
                marks[int(body[:-1])] = body[-1] == "y"
    return marks
