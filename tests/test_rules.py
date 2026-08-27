"""The rules the adversarial review forced into v0.2 of the spec.

They are easy to break by accident — "if the data is missing I'll just exclude
it" sounds prudent and is in fact the worst failure — so they live here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from dovetail import config, db
from dovetail.db import BudgetExhausted
from dovetail.matching import constraints as constraints_mod
from dovetail.matching import criteria as criteria_mod
from dovetail.matching.pipeline import Refusal, Row, cut, guard_rail
from dovetail.matching.scoring import Score


@dataclass
class FakeVenue:
    id: int = 1
    issn_l: str | None = "0000-0000"
    oa_model: str = "closed_or_unknown"
    apc_usd: int | None = None
    indexed_in: list | None = None
    anvur_class: str | None = None
    works_count: int | None = 500
    h_index: int | None = 40
    host_organization_name: str | None = "A publisher"
    is_core: bool | None = True
    publication_time_weeks: int | None = None
    article_types: list | None = None


# --- missing data must not exclude ---------------------------------------


def test_unknown_apc_does_not_exclude():
    """`apc_usd` is null on 92.7% of journals: excluding on absence would
    discard the corpus, not the expensive ones."""
    outcomes = constraints_mod.evaluate(FakeVenue(apc_usd=None), {"max_apc": 3000})
    assert [o.outcome for o in outcomes] == [constraints_mod.NEEDS_CHECK]
    assert not any(o.excludes() for o in outcomes)


def test_known_apc_over_the_cap_excludes():
    outcomes = constraints_mod.evaluate(FakeVenue(apc_usd=4550), {"max_apc": 3000})
    assert any(o.excludes() for o in outcomes)


def test_snsf_excludes_hybrids_but_flags_the_unknown():
    hybrid = constraints_mod.evaluate(
        FakeVenue(oa_model="hybrid", apc_usd=4550), {"funder": "snsf"}
    )
    assert any(o.excludes() for o in hybrid)

    unknown = constraints_mod.evaluate(FakeVenue(oa_model="closed_or_unknown"), {"funder": "snsf"})
    assert unknown and not any(o.excludes() for o in unknown)
    assert unknown[0].outcome == constraints_mod.NEEDS_CHECK


def test_an_already_rejecting_venue_really_is_excluded():
    """This one does: it is not data that ages, it is something that happened."""
    outcomes = constraints_mod.evaluate(FakeVenue(id=7), {"exclude_venues": [7]})
    assert any(o.excludes() for o in outcomes)


def test_a_stale_field_flags_and_does_not_exclude():
    old = datetime.now(timezone.utc) - timedelta(days=config.STALE_DAYS + 10)
    outcomes = constraints_mod.evaluate(
        FakeVenue(apc_usd=1000), {"max_apc": 3000}, verified_at={"apc_usd": old}
    )
    assert outcomes and outcomes[0].outcome == constraints_mod.NEEDS_CHECK


# --- the ANVUR band is per sector ----------------------------------------


def test_the_anvur_band_is_matched_per_sector():
    """A bare letter means nothing: Future of Science and Ethics is band A for
    11/C3, not in general. With equality, a journal covering two sectors would
    be excluded."""
    venue = FakeVenue(anvur_class="11/C3:A, 11/C2:A")
    assert not any(
        o.excludes() for o in constraints_mod.evaluate(venue, {"anvur_class": "11/C3:A"})
    )
    assert any(o.excludes() for o in constraints_mod.evaluate(venue, {"anvur_class": "11/C1:A"}))


# --- merit against logistics ---------------------------------------------


def test_four_logistics_criteria_and_one_merit_shows_red():
    """The exact shape of the choice the tool was built after."""
    crits = [
        criteria_mod.Crit(criteria_mod.MERIT, "adjacent genre", 1.0, ""),
        criteria_mod.Crit(criteria_mod.LOGISTICS, "fast", 1.0, ""),
        criteria_mod.Crit(criteria_mod.LOGISTICS, "open access", 1.0, ""),
        criteria_mod.Crit(criteria_mod.LOGISTICS, "low APC", 1.0, ""),
    ]
    assert criteria_mod.count_merit(crits) == 1
    assert criteria_mod.is_red(crits) is True


def test_two_merit_criteria_are_enough():
    crits = [
        criteria_mod.Crit(criteria_mod.MERIT, "subject", 1.0, ""),
        criteria_mod.Crit(criteria_mod.MERIT, "disciplinary family", 1.0, ""),
    ]
    assert criteria_mod.is_red(crits) is False


def test_right_discipline_absent_subject_is_not_merit():
    """High field with zero topic is the shape of an «out of scope» desk reject:
    it has to be shown, and it must not count as a merit criterion."""
    score = Score(topic=0.0, subfield=0.05, field=0.4, stage2_reachable=False)
    crits = criteria_mod.build(FakeVenue(), score, [], word_count=6000)
    warnings = [c for c in crits if "subject absent" in c.label]
    assert warnings and warnings[0].kind == criteria_mod.LOGISTICS
    assert criteria_mod.is_red(crits) is True


# --- guard rail -----------------------------------------------------------


def test_a_short_abstract_is_refused():
    with pytest.raises(Refusal, match="too short"):
        guard_rail("A paper about bioethics and adolescents.")


def test_a_long_abstract_passes():
    guard_rail(" ".join(["word"] * (config.MIN_ABSTRACT_WORDS + 1)))


# --- venue quality --------------------------------------------------------


def test_a_news_outlet_is_excluded():
    """OpenAlex's `type:journal` includes TV news and aggregators. In the first
    live run FOX6 News Milwaukee came tenth in the shortlist with three merit
    criteria: 2811 "works", h-index 1, no publisher."""
    outcomes = constraints_mod.evaluate(FakeVenue(is_core=False), {})
    assert any(o.excludes() and o.constraint == "is_core" for o in outcomes)


def test_unknown_is_core_does_not_exclude():
    """None is not False: a journal we know nothing about must not be discarded."""
    assert not any(o.excludes() for o in constraints_mod.evaluate(FakeVenue(is_core=None), {}))


def test_oa_outside_doaj_with_no_publisher_raises_a_flag():
    risk = constraints_mod.predatory_risk(
        FakeVenue(oa_model="oa_outside_doaj", host_organization_name=None)
    )
    assert risk["level"] == "high"
    assert len(risk["flags"]) >= 2


def test_an_ordinary_journal_raises_no_flag():
    assert constraints_mod.predatory_risk(FakeVenue(oa_model="full_oa"))["level"] == "none"


# --- ordering -------------------------------------------------------------


def test_ordering_is_not_dominated_by_subfield():
    """With subfield alone, a journal with a near-zero topic score overtook the
    one with the highest subject overlap in the list."""
    cognitive = Score(topic=0.0172, subfield=0.5742, field=0.44, stage2_reachable=True)
    philosophical = Score(topic=0.3255, subfield=0.5317, field=0.37, stage2_reachable=True)
    assert philosophical.combined() > cognitive.combined()


# --- the third bucket -----------------------------------------------------


def _row(venue, score, outcomes=()):
    return Row(
        venue=venue,
        score=score,
        outcomes=list(outcomes),
        criteria=[],
        predatory={"flags": [], "level": "none"},
    )


def test_a_venue_with_no_profile_does_not_vanish():
    """Before the fix it vanished: excluded by no constraint, scoring zero, and
    therefore in neither list. The code knew it could not classify it and threw
    that knowledge away exactly where it mattered."""
    unknown = _row(
        FakeVenue(id=99),
        Score(0.0, 0.0, 0.0, False, False, ("insufficient profile",)),
    )
    shortlist, excluded_shown, unclassifiable = cut([unknown])
    assert unknown not in shortlist
    assert unknown not in excluded_shown
    assert unknown in unclassifiable


def test_an_excluded_venue_is_not_called_unclassifiable():
    """Whoever a constraint excludes has a declared reason: that is another list."""
    venue = FakeVenue(id=98, is_core=False)
    row = _row(
        venue,
        Score(0.0, 0.0, 0.0, False, False, ("insufficient profile",)),
        constraints_mod.evaluate(venue, {}),
    )
    _, excluded_shown, unclassifiable = cut([row])
    assert row not in unclassifiable
    assert row in excluded_shown


# --- budget ---------------------------------------------------------------


def test_the_budget_refuses_before_spending(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    db.create_all(tmp_path / "t.db")
    with db.session_scope() as s:
        assert db.credits_remaining(s) == config.BUDGET_ANONYMOUS
        # Ten classifications exhaust the anonymous budget.
        for _ in range(10):
            db.spend(s, config.COST_TEXT)
        assert db.credits_remaining(s) == 0
        with pytest.raises(BudgetExhausted, match="OPENALEX_API_KEY"):
            db.spend(s, config.COST_TEXT)


def test_a_key_multiplies_the_budget_by_ten(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "fake")
    db.create_all(tmp_path / "t2.db")
    with db.session_scope() as s:
        assert db.credits_remaining(s) == config.BUDGET_WITH_KEY


def test_a_429_aligns_the_local_counter(tmp_path, monkeypatch):
    """The ledger only counts what goes through the tool, but the same IP can
    spend elsewhere. When the server says the till is empty, it is right."""
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    db.create_all(tmp_path / "t3.db")
    with db.session_scope() as s:
        assert db.credits_remaining(s) == config.BUDGET_ANONYMOUS
        db.mark_exhausted(s)
        assert db.credits_remaining(s) == 0
        with pytest.raises(BudgetExhausted):
            db.spend(s, config.COST_SOURCES)


# --- two sources disagreeing on the APC -----------------------------------


def test_doaj_wins_on_the_apc():
    """Same journal, same day: OpenAlex said 2290 USD, DOAJ said 2390. DOAJ is
    self-declared by the publisher and kept current through delisting."""
    from dovetail.sources.doaj import reconcile_apc

    result = reconcile_apc(2290, {"max": [{"price": 2390, "currency": "USD"}]})
    assert result["usd"] == 2390
    assert result["source"] == "doaj"


def test_a_wide_disagreement_is_reported_and_not_silently_resolved():
    """On either side of a threshold that gap decides the shortlist, so hiding
    it behind one number would be a choice made for the reader."""
    from dovetail.sources.doaj import reconcile_apc

    result = reconcile_apc(1000, {"max": [{"price": 3000, "currency": "USD"}]})
    assert result["disagreement"] == {"openalex": 1000, "doaj": 3000, "gap": 0.667}


def test_a_narrow_disagreement_is_not_noise_worth_raising():
    from dovetail.sources.doaj import reconcile_apc

    assert reconcile_apc(2290, {"max": [{"price": 2390, "currency": "USD"}]})["disagreement"] is None


def test_a_missing_doaj_apc_falls_back_to_openalex():
    from dovetail.sources.doaj import reconcile_apc

    assert reconcile_apc(2290, None) == {"usd": 2290, "source": "openalex", "disagreement": None}


# --- the API's hard text limit --------------------------------------------


def test_a_long_abstract_is_cut_to_the_api_limit():
    """`/text/*` rejects anything over 2000 characters combined, on GET and on
    POST alike. Found by a validation run failing on four papers out of seven."""
    from dovetail.sources.openalex import fit_text

    title, abstract, dropped = fit_text("A title", "word " * 600)
    assert len(title) + len(abstract) <= config.MAX_TEXT_CHARS
    assert dropped > 0


def test_a_normal_abstract_is_left_alone():
    from dovetail.sources.openalex import fit_text

    assert fit_text("A title", "a normal abstract")[2] == 0


def test_the_cut_lands_on_a_word_boundary():
    from dovetail.sources.openalex import fit_text

    _, abstract, _ = fit_text("t", "supercalifragilistic " * 200)
    assert not abstract.endswith("supercalifragilisti")


def test_the_title_is_never_the_part_that_is_cut():
    """The title is the shortest and most informative half; cutting it to fit a
    long abstract would throw away the better signal."""
    from dovetail.sources.openalex import fit_text

    title = "A perfectly ordinary paper title"
    kept, _, _ = fit_text(title, "word " * 600)
    assert kept == title
