"""The stages, in order. SPEC.md §6.

    1. text profile, with a guard rail
    2. candidate generation, **without constraints**
    3. scope score at three levels
    4. constraints, criteria, cut

Stage 5 (reading the finalists with an LLM) belongs to Phase 4 and is not here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..models import (
    Criterion,
    CriterionKind,
    FieldVerification,
    MatchResult,
    MatchRun,
    Venue,
)
from ..sources.doaj import DoajClient
from ..sources.enrich import enrich_from_doaj
from ..sources.openalex import OpenAlexClient, normalize_source, short_id
from ..provenance import stamp
from . import constraints as constraints_mod
from . import criteria as criteria_mod
from .scoring import INSUFFICIENT_PROFILE, score_venue, text_profile, venue_profile


class Refusal(RuntimeError):
    """The matcher refuses to produce a shortlist.

    Failing out loud is acceptable, guessing is not — the same rule that governs
    section segmentation for the anatomy in §7.
    """


# --- stage 1 --------------------------------------------------------------


def guard_rail(abstract: str) -> None:
    words = len((abstract or "").split())
    if words < config.MIN_ABSTRACT_WORDS:
        raise Refusal(
            f"abstract too short ({words} words, minimum {config.MIN_ABSTRACT_WORDS}). "
            f"Classification is unstable below this threshold: verified on 27 Aug 2026 "
            f"that the same paper cut down to one sentence comes back with topics "
            f"**disjoint** from those of the full abstract, and with them the whole "
            f"candidate pool changes."
        )


def classify(session: Session, client: OpenAlexClient, title: str, abstract: str) -> dict:
    guard_rail(abstract)
    payload = client.classify_text(session, title, abstract)
    topics = payload.get("topics") or []
    if not topics:
        raise Refusal("OpenAlex returned no topics for this text.")
    primary = max(t.get("score", 0.0) for t in topics)
    if primary < config.MIN_PRIMARY_TOPIC_SCORE:
        raise Refusal(
            f"classification too uncertain (primary topic at {primary:.3f}, "
            f"threshold {config.MIN_PRIMARY_TOPIC_SCORE}). The abstract is probably "
            f"too generic for a shortlist to mean anything."
        )
    return payload


# --- what a run costs, before it is started -------------------------------

# Every call in a run that spends OpenAlex credits, with the ceiling on how many
# times it can happen. It lives **here**, next to the code that makes the calls,
# because the first version of it lived in the web layer and was wrong on its
# first live run: it counted the paginated sweep and neither of the other two
# calls stage 2 makes, so a form promising "at most 125" watched a run spend 133.
#
# An estimate that is under is worse than no estimate, because it is trusted.
# `test_the_estimate_names_every_call_that_spends` walks the source of
# `generate_candidates` and fails if a call appears there that is not named here,
# which is the drift that produced the error in the first place.
def cost_terms(discover: bool = True) -> list[dict]:
    from .. import config as _c

    terms = [
        {
            "call": "classify_text",
            "label": "Classifying the manuscript",
            "detail": "/text/topics, once — the one call that cannot be skipped",
            "credits": _c.COST_TEXT,
        }
    ]
    if discover:
        terms += [
            {
                "call": "sources_by_topics",
                "label": "Sweeping for candidates",
                "detail": f"/sources, up to {_c.MAX_CANDIDATE_PAGES} pages of 200",
                "credits": _c.MAX_CANDIDATE_PAGES * _c.COST_SOURCES,
            },
            {
                "call": "journals_publishing_on",
                "label": "Journals that publish on the subject as a sideline",
                "detail": "/works grouped by source, once — the topics filter cannot reach them",
                "credits": _c.COST_WORKS,
            },
            {
                "call": "sources_by_ids",
                "label": "Fetching the records those groups named",
                # Group results cap at 200, and the batch is 100, so this is two
                # calls at the very most however broad the topic.
                "detail": "/sources by id, at most two batches of 100",
                "credits": 2 * _c.COST_SOURCES,
            },
        ]
    return terms


def cost_ceiling(discover: bool = True) -> int:
    return sum(t["credits"] for t in cost_terms(discover))


# --- stage 2 --------------------------------------------------------------


def generate_candidates(
    session: Session, client: OpenAlexClient, topic_ids: list[str]
) -> tuple[list[Venue], dict]:
    """One call, topics joined by OR. **No constraints here**: the hard filters
    belong to stage 4, where a venue can be flagged instead of vanishing.

    Venues found this way go straight into the table rather than into the
    proposal queue: OpenAlex's inventory is a dated, attributed fact, whereas the
    queue exists for what someone *infers* — guidelines, a model's judgement.
    """
    payload = client.sources_by_topics(session, topic_ids)
    found: list[Venue] = []
    seen_openalex_ids: set[str] = set()
    for src in payload.get("results") or []:
        venue = upsert_venue(session, src)
        found.append(venue)
        if venue.openalex_id:
            seen_openalex_ids.add(venue.openalex_id)
    session.flush()

    # Second mechanism, because the first one cannot reach everything: a journal
    # only matches `topics.id` if the topic is in its **top 25**, and that list
    # is truncated. Grouping works by source finds the journals that publish on
    # the subject as a sideline. Measured: 76 works of the International Journal
    # of Public Health on one paper's topics, and the sources filter misses it.
    by_volume = client.journals_publishing_on(session, topic_ids)
    missing = [
        e["openalex_id"]
        for e in by_volume
        if e["openalex_id"].startswith("S") and e["openalex_id"] not in seen_openalex_ids
    ]
    added = 0
    for src in client.sources_by_ids(session, missing):
        venue = upsert_venue(session, src)
        found.append(venue)
        added += 1
    session.flush()

    return found, {
        "pool": payload["meta"]["count"],
        "fetched": len(found),
        "pages": payload.get("pages"),
        "truncated": payload.get("truncated", False),
        "added_by_volume": added,
        "volume_groups": len(by_volume),
    }


def upsert_venue(session: Session, src: dict) -> Venue:
    fields = normalize_source(src)
    venue = None
    if fields.get("issn_l"):
        venue = session.scalar(select(Venue).where(Venue.issn_l == fields["issn_l"]))
    if venue is None and fields.get("openalex_id"):
        venue = session.scalar(select(Venue).where(Venue.openalex_id == fields["openalex_id"]))
    if venue is None:
        venue = Venue(display_name=fields["display_name"])
        session.add(venue)

    for k, v in fields.items():
        setattr(venue, k, v)
    session.flush()
    stamp(session, venue, fields.keys(), "openalex")
    return venue


# --- stages 3 and 4 -------------------------------------------------------


@dataclass
class Row:
    venue: Venue
    score: object
    outcomes: list
    criteria: list
    predatory: dict

    @property
    def excluded(self) -> bool:
        return any(o.excludes() for o in self.outcomes)

    @property
    def is_red(self) -> bool:
        return criteria_mod.is_red(self.criteria)


def evaluate_all(
    session: Session,
    text_topics: list[dict],
    candidates: list[Venue],
    constraints: dict,
    word_count,
) -> list[Row]:
    text = text_profile(text_topics)
    rows: list[Row] = []
    for v in candidates:
        profile = venue_profile(v.topics or [])
        score = score_venue(text, profile, v.works_count, v.topics_coverage)
        verified_at = {
            fv.field_name: fv.verified_at
            for fv in session.scalars(
                select(FieldVerification).where(FieldVerification.venue_id == v.id)
            )
        }
        outcomes = constraints_mod.evaluate(v, constraints, verified_at)
        crits = criteria_mod.build(v, score, outcomes, word_count)
        rows.append(Row(v, score, outcomes, crits, constraints_mod.predatory_risk(v)))
    return rows


def _reevaluate(
    session: Session, rows: list[Row], constraints: dict, word_count
) -> None:
    """Recompute constraints, criteria and predatory risk after enrichment.

    Also the one place `Venue.predatory_risk` is written: it used to be computed
    per run and dropped into the result's flags, leaving the column on the venue
    permanently empty — a field that looked like state and was not.
    """
    for r in rows:
        verified_at = {
            fv.field_name: fv.verified_at
            for fv in session.scalars(
                select(FieldVerification).where(FieldVerification.venue_id == r.venue.id)
            )
        }
        r.outcomes = constraints_mod.evaluate(r.venue, constraints, verified_at)
        r.criteria = criteria_mod.build(r.venue, r.score, r.outcomes, word_count)
        r.predatory = constraints_mod.predatory_risk(r.venue)
        r.venue.predatory_risk = r.predatory
    session.flush()


def cut(rows: list[Row]) -> tuple[list[Row], list[Row], list[Row]]:
    """Return (shortlist, excluded_shown, unclassifiable).

    **The third bucket is not a detail.** A venue with no profile — a journal
    OpenAlex does not index, such as *Future of Science and Ethics* — is excluded
    by no constraint and scores zero, so it used to land in neither list: it
    vanished. The code knew it could not classify it (`insufficient profile`) and
    threw that knowledge away at exactly the point where it mattered. That is the
    failure described in §14.4, committed in here.

    Now it comes out in a list of its own, declared: not ranked alongside the
    others, because a score that does not exist cannot be compared, but not
    hidden either.
    """
    unclassifiable = [
        r for r in rows if not r.excluded and INSUFFICIENT_PROFILE in r.score.notes
    ]
    skip = {id(r) for r in unclassifiable}

    eligible = [
        r for r in rows if not r.excluded and id(r) not in skip and r.score.subfield > 0
    ]
    eligible.sort(key=lambda r: r.score.sort_key(), reverse=True)
    shortlist = eligible[: config.MAX_SHORTLIST]

    if len(shortlist) >= config.MIN_SHORTLIST:
        return shortlist, [], unclassifiable

    excluded = [r for r in rows if r.excluded]
    excluded.sort(key=lambda r: r.score.sort_key(), reverse=True)
    return shortlist, excluded[: config.MAX_SHORTLIST - len(shortlist)], unclassifiable


# --- orchestration --------------------------------------------------------


def run_match(
    session: Session,
    client: OpenAlexClient,
    title: str,
    abstract: str,
    word_count: int | None = None,
    constraints: dict | None = None,
    discover: bool = True,
    precomputed_profile: dict | None = None,
    doaj: DoajClient | None = None,
    run: MatchRun | None = None,
) -> tuple[MatchRun, list[Row], list[Row], list[Row]]:
    constraints = constraints or {}
    if run is None:
        run = MatchRun(
            title=title,
            abstract=abstract,
            word_count=word_count,
            constraints=constraints,
            scoring_config_version=config.SCORING_CONFIG_VERSION,
        )
        session.add(run)
    else:
        # A row the caller created and committed before starting, so it had an
        # id to send the browser to while this was still running. Its constraints
        # are overwritten rather than merged: the sweep report goes in there too,
        # and the caller only wrote what the form asked for.
        run.constraints = constraints
        run.scoring_config_version = config.SCORING_CONFIG_VERSION

    # The profile can be reused: redoing it costs 100 credits, i.e. a hundred
    # /sources calls, and it is the spend the daily budget caps at ten.
    if precomputed_profile is not None:
        guard_rail(abstract)
        payload = precomputed_profile
    else:
        try:
            payload = classify(session, client, title, abstract)
        except Refusal as e:
            run.refused_reason = str(e)
            session.flush()
            raise

    run.text_profile = payload
    topics = payload.get("topics") or []
    topic_ids = [short_id(t.get("id", "")) for t in topics if t.get("id")]

    candidates: list[Venue] = []
    sweep = None
    if discover:
        candidates, sweep = generate_candidates(session, client, topic_ids)

    # To the discovered candidates we add whatever is already in the table: the
    # PaperTrail seed holds venues the user knows, which may not surface from
    # this particular text's topics.
    seen = {v.id for v in candidates}
    for v in session.scalars(select(Venue)):
        if v.id not in seen:
            candidates.append(v)

    rows = evaluate_all(session, topics, candidates, constraints, word_count)
    shortlist, excluded_shown, unclassifiable = cut(rows)

    # DOAJ runs on the finalists only, then constraints and criteria are redone
    # for those rows: enrichment can change both. Skipping the second pass would
    # leave a shortlist judged on a record that no longer exists — and it is how
    # the "declared N weeks to publication" criterion could never fire, since
    # `publication_time_weeks` was still None when the criteria were built.
    if doaj is not None:
        enriched = enrich_from_doaj(session, doaj, [r.venue for r in shortlist])
        if enriched["enriched"] or enriched["disagreements"]:
            _reevaluate(session, shortlist, constraints, word_count)
        run.constraints = {**constraints, "_doaj": enriched}
    if sweep is not None:
        run.constraints = {**(run.constraints or constraints), "_sweep": sweep}

    # Numbered **inside** each basket, and the basket recorded.
    #
    # This used to be one running counter over the three lists concatenated,
    # which threw the basket away one step after `cut` computed it and stated an
    # order between rows that are not comparable — a venue with no profile is
    # not "below" a scored one, it is not on the same axis. It showed up the
    # first time a hand-declared journal reached a run: score 0.0000, position
    # 13, in a shortlist capped at twelve.
    numbered = (
        [("shortlist", r) for r in shortlist]
        + [("excluded", r) for r in excluded_shown]
        + [("unclassifiable", r) for r in unclassifiable]
    )
    counters: dict[str, int] = {}
    for bucket, r in numbered:
        counters[bucket] = counters.get(bucket, 0) + 1
        result = MatchResult(
            run_id=run.id,
            venue_id=r.venue.id,
            score_topic=r.score.topic,
            score_subfield=r.score.subfield,
            score_field=r.score.field,
            bucket=bucket,
            position=counters[bucket],
            venue_snapshot={
                "topics": r.venue.topics,
                "works_count": r.venue.works_count,
                "oa_model": getattr(r.venue.oa_model, "value", r.venue.oa_model),
                "apc_usd": r.venue.apc_usd,
            },
            excluded_by=[
                {"constraint": o.constraint, "outcome": o.outcome, "reason": o.reason}
                for o in r.outcomes
            ],
            flags=list(r.score.notes) + r.predatory["flags"],
        )
        session.add(result)
        session.flush()
        for c in r.criteria:
            session.add(
                Criterion(
                    result_id=result.id,
                    kind=CriterionKind(c.kind),
                    label=c.label,
                    weight=c.weight,
                    evidence=c.evidence,
                )
            )

    session.flush()
    return run, shortlist, excluded_shown, unclassifiable
