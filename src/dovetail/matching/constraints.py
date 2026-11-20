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

    # There used to be a second exclusion here, on `is_core`: a journal outside
    # the list of core sources was never proposed. It came in on 27 Aug 2026,
    # after a TV news outlet typed `journal` came tenth in a shortlist, and it
    # was described as OpenAlex's curated subset. It is not that. `is_core` is
    # the CWTS Core sources list behind the Leiden Ranking, frozen on a snapshot
    # of August 2024, and it left out three journals in four of those on record
    # here — among them 111 with an h-index of 50 or more, journals in MEDLINE
    # and in DOAJ, and most of what is not published in English.
    #
    # Removed on 20 Nov 2026. A journal off that list is proposed like any other.
    # What it costs is known and was measured on a real consultation before
    # removing it: four of the first twelve places, and thirteen of the first
    # twenty-five, go to journals off the list — national-language journals,
    # titles that have ceased, small publishers.
    #
    # So it came back the same day as a choice, off unless asked for. A journal
    # the list was never asked about is marked and not removed, like every
    # other missing fact.
    if constraints.get("core_only"):
        if venue.is_core is False:
            outcomes.append(
                Outcome(
                    "is_core",
                    EXCLUDED,
                    "not on the Leiden core list, and you asked for those journals only",
                )
            )
        elif venue.is_core is None:
            outcomes.append(
                Outcome(
                    "is_core",
                    NEEDS_CHECK,
                    "not known whether it is on the Leiden core list",
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

    # -- language -------------------------------------------------------------
    # Read from the journal's articles (`Venue.languages`). A journal is kept
    # when at least a fifth of them are in the language asked for, which keeps
    # the bilingual ones. Not looked up yet is not "no": it is marked.
    want = str(constraints.get("language") or "").lower()
    if want:
        langs = getattr(venue, "languages", None)
        if langs is None:
            outcomes.append(
                Outcome(
                    "language",
                    NEEDS_CHECK,
                    "the language of its articles has not been looked up",
                )
            )
        elif not langs:
            outcomes.append(
                Outcome("language", NEEDS_CHECK, "no language is on record for its articles")
            )
        else:
            share = next((x.get("share", 0.0) for x in langs if x.get("code") == want), 0.0)
            if share < config.LANGUAGE_MIN_SHARE:
                found = ", ".join(
                    f"{config.language_name(x['code'])} {round(x['share'] * 100)}%"
                    for x in langs[:3]
                )
                outcomes.append(
                    Outcome(
                        "language",
                        EXCLUDED,
                        f"its articles are in {found}: under a fifth are in "
                        f"{config.language_name(want)}",
                    )
                )

    return outcomes
