"""The criteria that hold a venue up, split between merit and logistics.

This is the tool's original contribution, and it comes from a post-mortem: the
choice made on the validation case rested on four criteria,
**three of them logistical** (fast, open access, low APC) **and only one about
merit** (adjacent genre). The one that failed was that one.

Hence the displayed rule: **fewer than two merit criteria and the venue shows in
red**, however well it does on logistics.
"""

from __future__ import annotations

from dataclasses import dataclass

MERIT = "merit"
LOGISTICS = "logistics"

STRONG_SCOPE = 0.15
WEAK_SCOPE = 0.05


@dataclass
class Crit:
    kind: str
    label: str
    weight: float
    evidence: str


def build(
    venue,
    score,
    constraint_outcomes,
    word_count: int | None = None,
    neighbourhood=None,
) -> list[Crit]:
    crits: list[Crit] = []

    # -- merit ---------------------------------------------------------------

    if score.stage2_reachable:
        crits.append(
            Crit(
                MERIT,
                "publishes on at least one of the text's topics",
                1.0,
                "topic shared between the text and the journal's profile",
            )
        )

    if score.topic >= STRONG_SCOPE:
        crits.append(Crit(MERIT, "strong subject overlap", 1.0, f"topic cosine {score.topic:.3f}"))
    elif score.topic >= WEAK_SCOPE:
        crits.append(
            Crit(MERIT, "partial subject overlap", 0.5, f"topic cosine {score.topic:.3f}")
        )

    if score.subfield >= STRONG_SCOPE:
        crits.append(
            Crit(MERIT, "same disciplinary family", 1.0, f"subfield cosine {score.subfield:.3f}")
        )

    # The "right discipline, absent subject" case: high field, zero topic. It is
    # not a merit criterion, it is a warning — and it is the exact shape of the
    # rejection the tool was built after.
    if score.field >= STRONG_SCOPE and score.topic == 0.0:
        crits.append(
            Crit(
                LOGISTICS,
                "right discipline but subject absent from what it publishes",
                0.0,
                f"field {score.field:.3f}, topic 0.000 — the shape of an "
                f"«out of scope» desk reject",
            )
        )

    # The citation neighbourhood, when a reference list was given. Merit, and
    # evidence of a different kind from the three above: those read OpenAlex's
    # topics, this reads who cites what the manuscript cites.
    if neighbourhood is not None:
        where = f" in {neighbourhood.via_field}" if neighbourhood.via_field else ""
        crits.append(
            Crit(
                MERIT,
                f"its recent papers cite {neighbourhood.refs_reached} of your "
                f"{neighbourhood.refs_total} references{where}",
                1.0,
                f"{neighbourhood.citing_links} citation links, "
                f"{neighbourhood.per_1000:g} per 1000 of the "
                f"{neighbourhood.recent_works} works it published in the window",
            )
        )
        if neighbourhood.cited_here:
            crits.append(
                Crit(
                    MERIT,
                    f"published {neighbourhood.cited_here} of the works you cite",
                    1.0,
                    "primary location of the references, from OpenAlex",
                )
            )

    types = [t for t in (venue.article_types or []) if t.word_limit]
    if types and word_count:
        fitting = [t for t in types if t.word_limit >= word_count]
        if fitting:
            best = min(fitting, key=lambda t: t.word_limit)
            crits.append(
                Crit(
                    MERIT,
                    f"format fits: {best.name} ({best.word_limit} words{_scope(best)})",
                    1.0,
                    best.source_url or "guidelines",
                )
            )
        else:
            # Not an exclusion: the word count given is the author's own figure,
            # and whether it counts the references is exactly what differs from
            # one journal to the next. But it is the shape of the first real
            # failure this tool was consulted on, and it used to pass in silence.
            longest = max(types, key=lambda t: t.word_limit)
            crits.append(
                Crit(
                    LOGISTICS,
                    f"over every word limit on record: the longest is {longest.name}, "
                    f"{longest.word_limit} words{_scope(longest)}, against {word_count}",
                    0.0,
                    longest.source_url or "guidelines",
                )
            )

    # -- logistics -----------------------------------------------------------

    model = getattr(venue.oa_model, "value", venue.oa_model)
    if model == "full_oa":
        crits.append(Crit(LOGISTICS, "fully open access (in DOAJ)", 1.0, "is_in_doaj"))

    if venue.apc_usd is not None and venue.apc_usd <= 2000:
        crits.append(Crit(LOGISTICS, f"moderate APC ({venue.apc_usd} USD)", 1.0, "apc_usd"))

    if venue.publication_time_weeks:
        crits.append(
            Crit(
                LOGISTICS,
                f"declared {venue.publication_time_weeks} weeks to publication",
                1.0,
                "DOAJ publication_time_weeks — time to publication, not the "
                "latency of the editorial decision",
            )
        )

    if venue.indexed_in:
        crits.append(
            Crit(LOGISTICS, f"indexed in {', '.join(venue.indexed_in)}", 1.0, "indexed_in")
        )

    if not any(o.excludes() for o in constraint_outcomes):
        flagged = [o for o in constraint_outcomes if not o.excludes()]
        if not flagged:
            crits.append(Crit(LOGISTICS, "passes every declared constraint", 1.0, "constraints"))

    return crits


def _scope(article_type) -> str:
    """What the limit counts, beside the limit. A number without it misleads:
    8,000 including references and 9,000 excluding them give opposite verdicts
    on the same manuscript."""
    scope = (getattr(article_type, "word_limit_scope", None) or "").strip()
    return f"; counts: {scope}" if scope else "; what it counts is not on record"


def count_merit(crits: list[Crit]) -> int:
    return sum(1 for c in crits if c.kind == MERIT and c.weight > 0)


def is_red(crits: list[Crit]) -> bool:
    """The rule from §9. Not an exclusion: a way of looking at the row."""
    return count_merit(crits) < 2
