"""Stage 4: the constraints.

**The non-negotiable rule**, and the reason this module is separate from
candidate generation: *a constraint never excludes a venue whose relevant field
is missing or stale.* The venue stays, flagged `needs_check`, and goes to the top
of the verification queue.

v0.1 of the spec protected **stale** data and ignored **missing** data, which is
the majority case — `apc_usd` is null on 92.7% of journals. Both produce the same
failure: a silent false negative.

And constraints apply **here**, not in the stage-2 API query. If they lived
there, an excluded venue would never enter the list and there would be nothing
to flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .. import config


EXCLUDED = "excluded"
NEEDS_CHECK = "needs_check"


@dataclass
class Outcome:
    constraint: str
    outcome: str
    reason: str

    def excludes(self) -> bool:
        return self.outcome == EXCLUDED


def _stale(verified_at: datetime | None) -> bool:
    if verified_at is None:
        return True
    if verified_at.tzinfo is None:
        verified_at = verified_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - verified_at > timedelta(days=config.STALE_DAYS)


def evaluate(
    venue, constraints: dict, verified_at: dict[str, datetime] | None = None
) -> list[Outcome]:
    """`verified_at` maps field name to verification date, from
    `FieldVerification`."""
    verified_at = verified_at or {}
    outcomes: list[Outcome] = []

    # -- venues that already bounced this paper ------------------------------
    # This one does exclude: it is not data that ages, it is something that
    # happened.
    excluded_ids = set(constraints.get("exclude_venues") or [])
    if venue.id in excluded_ids or (venue.issn_l and venue.issn_l in excluded_ids):
        outcomes.append(
            Outcome("exclude_venues", EXCLUDED, "this paper has already been rejected here")
        )

    # -- is this the kind of thing you submit a paper to? --------------------
    #
    # Two different facts, and they used to be collapsed into one message.
    #
    # **What it is** is categorical. You do not submit a manuscript for peer
    # review to Zenodo, Figshare, PubMed or OSF Preprints — they are repositories,
    # and the sweep pulls them in because Zenodo carries 12.2 million works and
    # therefore touches every topic there is. That is not a quality judgement and
    # it does not age, so it is worth saying in its own words rather than through
    # a curation flag that happens to catch it.
    #
    # `book series` and `conference` are **marked, not excluded**: they are real
    # places a paper can go, just outside what this tool models (SPEC §13). And a
    # missing type marks too, by the rule at the top of this file.
    kind = (venue.venue_type or "").strip().lower()
    if kind in {"repository", "ebook platform"}:
        outcomes.append(
            Outcome(
                "venue_type",
                EXCLUDED,
                f"a {kind}, not a journal: papers are deposited there, not "
                f"submitted to it for review",
            )
        )
    elif kind in {"book series", "conference"}:
        outcomes.append(
            Outcome(
                "venue_type",
                NEEDS_CHECK,
                f"a {kind}: a real venue, but outside what this tool models — "
                f"it knows about journals",
            )
        )
    elif not kind:
        outcomes.append(
            Outcome("venue_type", NEEDS_CHECK, "no type on record: check what this is")
        )

    # **Whether the record is trustworthy** is the other fact, and `is_core` is
    # OpenAlex's answer to it. Without this check the first live run put **FOX6
    # News Milwaukee** tenth in the shortlist with three merit criteria: a TV
    # news outlet typed `journal`, 2811 "works", h-index 1.
    #
    # Measured 28 Aug 2026, on the worry that it might be a mainstream-only
    # filter: it is not. The twelve finalists of a real run carry 66 to 2,232
    # works and are all core, while the non-core side is *ChemInform*, *Who's
    # Who*, *Inpharma Weekly* — and a **duplicate** BMJ record with 389k works
    # whose two canonical twins are both core. It de-duplicates and curates; it
    # does not favour size.
    #
    # It excludes, but **here and not in the stage-2 query**, so it stays visible
    # among the excluded instead of vanishing without trace.
    if venue.is_core is False:
        outcomes.append(
            Outcome(
                "is_core",
                EXCLUDED,
                "outside OpenAlex's curated subset: an abstracting service, a "
                "magazine, or a duplicate of a record that is in it",
            )
        )

    # -- funder --------------------------------------------------------------
    funder = (constraints.get("funder") or "").lower()
    if funder == "snsf":
        model = getattr(venue.oa_model, "value", venue.oa_model)
        if model == "hybrid":
            outcomes.append(
                Outcome(
                    "funder:snsf",
                    EXCLUDED,
                    f"hybrid (APC {venue.apc_usd} USD): the SNSF does not pay hybrid APCs",
                )
            )
        elif model == "closed_or_unknown":
            outcomes.append(
                Outcome(
                    "funder:snsf",
                    NEEDS_CHECK,
                    "OA status not verifiable: `apc_usd` is missing, and a missing "
                    "APC does not prove the journal is closed — check by hand",
                )
            )
        elif model == "oa_outside_doaj":
            outcomes.append(
                Outcome(
                    "funder:snsf",
                    NEEDS_CHECK,
                    "OA but outside DOAJ: cOAlition S compliance cannot be derived from here",
                )
            )

    # -- APC -----------------------------------------------------------------
    max_apc = constraints.get("max_apc")
    if max_apc is not None:
        if venue.apc_usd is None:
            outcomes.append(
                Outcome(
                    "max_apc",
                    NEEDS_CHECK,
                    "APC unknown: the field is null on 92.7% of journals, so excluding "
                    "here would mean discarding for lack of data",
                )
            )
        elif venue.apc_usd > max_apc:
            outcomes.append(
                Outcome("max_apc", EXCLUDED, f"APC {venue.apc_usd} USD above the {max_apc} cap")
            )
        elif _stale(verified_at.get("apc_usd")):
            outcomes.append(Outcome("max_apc", NEEDS_CHECK, "APC known but not verified recently"))

    # -- indexing ------------------------------------------------------------
    required = constraints.get("must_be_indexed_in") or []
    if required:
        if not venue.indexed_in:
            outcomes.append(Outcome("must_be_indexed_in", NEEDS_CHECK, "indexing not detected"))
        else:
            missing = [r for r in required if r not in venue.indexed_in]
            if missing:
                outcomes.append(
                    Outcome("must_be_indexed_in", EXCLUDED, f"not indexed in {', '.join(missing)}")
                )

    # -- ANVUR band ----------------------------------------------------------
    # The ANVUR band is **per competition sector**: a bare letter means nothing,
    # and Future of Science and Ethics is band A for 11/C3, not in general. So
    # the field carries "sector:band" (e.g. "11/C3:A", comma separated for more
    # than one) and the comparison is membership, not equality: with equality,
    # asking for "11/C3:A" would exclude a journal that also covers another
    # sector.
    anvur = constraints.get("anvur_class")
    if anvur:
        declared = [x.strip() for x in (venue.anvur_class or "").split(",") if x.strip()]
        if not declared:
            outcomes.append(Outcome("anvur_class", NEEDS_CHECK, "ANVUR band not detected"))
        elif anvur not in declared:
            outcomes.append(
                Outcome(
                    "anvur_class",
                    EXCLUDED,
                    f"covers {', '.join(declared)}, {anvur} required",
                )
            )

    return outcomes


def predatory_risk(venue) -> dict:
    """No single one of these signals convicts. Together they raise a flag that a
    human has to close.

    It exists because the merit/logistics grid of §9, on its own, **promotes** a
    predatory journal: fast, OA, low APC, no embargo are four full logistics
    criteria, and its topic profile is broad by construction.
    """
    flags: list[str] = []
    model = getattr(venue.oa_model, "value", venue.oa_model)

    if model == "oa_outside_doaj":
        flags.append("OA but outside DOAJ")
    if venue.host_organization_name is None:
        flags.append("publisher not declared")
    if venue.works_count and venue.h_index is not None:
        if venue.works_count > 1000 and venue.h_index < 15:
            flags.append(f"{venue.works_count} works but h-index {venue.h_index}")
    if venue.indexed_in is not None and not venue.indexed_in:
        flags.append("not indexed")

    return {"flags": flags, "level": "high" if len(flags) >= 2 else ("low" if flags else "none")}
