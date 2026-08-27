"""The Phase 0 validation, turned into a regression test.

The fixtures are real OpenAlex responses from 27 Aug 2026: `text-case.json` is
the topic classification of a real manuscript's abstract, and the `source-venue-*`
files are real journal records — public data, kept under neutral names.

**What is deliberately not here.** Which manuscript it is, and which of these
journals turned it down. That belongs to an unpublished paper with co-authors
who did not agree to it being published, and to a submission that is still open;
it lives in `validation/case.local.json`, which is gitignored. The numbers below
are the whole finding and they do not need the identities.

What this locks down is the specific fact Phase 0 rests on: **two of the four
venues share no topic at all with the text**, so stage 2 does not generate them
and their zero is arithmetic rather than measurement. If some change ever made
them reachable, this test fails and needs looking at.
"""

from __future__ import annotations

import json
from pathlib import Path

from dovetail.matching.scoring import cosine, score_venue, text_profile, venue_profile
from dovetail.sources.openalex import derive_oa_model, normalize_source

FIXTURES = Path(__file__).parent / "fixtures"

# Venues A and B are the two the candidate generator cannot reach; C is the one
# it can. D is a hybrid, kept for the OA-model branch.
UNREACHABLE = ("venue-a", "venue-b")
REACHABLE = "venue-c"
HYBRID = "venue-d"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def profile_of(name: str):
    return normalize_source(load(f"source-{name}.json"))


def test_the_text_is_not_classified_as_medical_ethics():
    """The finding that started the tool: the classifier puts this manuscript in
    social science and psychology, not in medical ethics — which is where its
    author would have sent it by instinct."""
    payload = load("text-case.json")
    fields = [t["field"]["display_name"] for t in payload["topics"]]
    assert "Social Sciences" in fields
    assert not any(f == "Health Professions" for f in fields)


def test_two_venues_share_no_topic_with_the_text():
    """The core of the validation, and why the zero is not a measurement: there
    is no shared term, so there is nothing to measure."""
    text = text_profile(load("text-case.json")["topics"])

    for name in UNREACHABLE:
        fields = profile_of(name)
        score = score_venue(text, venue_profile(fields["topics"]), fields["works_count"])
        assert score.stage2_reachable is False, f"{name} is now reachable: the retrodiction changed"
        assert score.topic == 0.0


def test_one_venue_does_share_a_topic():
    text = text_profile(load("text-case.json")["topics"])
    fields = profile_of(REACHABLE)
    score = score_venue(text, venue_profile(fields["topics"]), fields["works_count"])
    assert score.stage2_reachable is True
    assert score.topic > 0.0


def test_the_cosine_never_exceeds_one():
    """v0.1's raw dot product produced a field score of 1.16, not comparable
    across levels. The cosine is bounded by one by construction."""
    text = text_profile(load("text-case.json")["topics"])
    for name in (HYBRID, REACHABLE, "venue-b"):
        fields = profile_of(name)
        score = score_venue(text, venue_profile(fields["topics"]), fields["works_count"])
        for level in (score.topic, score.subfield, score.field):
            assert 0.0 <= level <= 1.0


def test_a_journal_that_is_closed_but_charges_an_apc_is_hybrid():
    """`is_oa: false` with `apc_usd` set. This is the case the SNSF constraint
    has to catch, and the only one of the four branches that is certain."""
    src = load(f"source-{HYBRID}.json")
    assert src["is_oa"] is False
    assert src["apc_usd"] == 4550
    assert derive_oa_model(src) == "hybrid"


def test_a_journal_in_doaj_is_full_oa():
    assert derive_oa_model(load(f"source-{REACHABLE}.json")) == "full_oa"


def test_a_journal_with_no_topics_has_an_empty_profile():
    score = score_venue(text_profile(load("text-case.json")["topics"]), venue_profile([]))
    assert score.reliable is False
    assert "insufficient profile" in score.notes


def test_cosine_of_disjoint_vectors():
    assert cosine({"a": 1.0}, {"b": 1.0}) == 0.0
    assert cosine({}, {"b": 1.0}) == 0.0
