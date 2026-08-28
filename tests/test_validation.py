"""Phase 1b as redesigned, and the properties that make it a measurement.

Both measures exist because the first design was not wrong in its arithmetic but
in its question. So what is tested here is mostly *what the measurement refuses
to conclude*.
"""

from __future__ import annotations

import pytest

from dovetail import db
from dovetail.models import MatchResult, MatchRun, Venue
from dovetail.validation import (
    NegativeCase,
    blind_sheet,
    check_negatives,
    parse_marks,
    score_sheet,
    summarise_negatives,
)


# --- 1. known negatives ---------------------------------------------------


def _case(venue_name, reason="desk reject, out of scope"):
    return NegativeCase(
        paper="a paper", venue_name=venue_name, venue_openalex_id=None, reason=reason
    )


@pytest.fixture()
def three_venues(tmp_path, monkeypatch):
    monkeypatch.setenv("DOVETAIL_DB", str(tmp_path / "v.db"))
    db.create_all(tmp_path / "v.db")
    with db.session_scope() as s:
        ids = {}
        for name in ("Shortlisted Journal", "Scored But Cut", "Never Reached"):
            v = Venue(display_name=name)
            s.add(v)
            s.flush()
            ids[name] = v.id
        return ids


def test_a_venue_that_rejected_the_paper_and_was_suggested_is_a_miss(three_venues):
    """The one direction nothing contaminates. A journal that said «out of
    scope» is the journal's own statement about the thing the tool models, so
    suggesting it is wrong in a way nobody has to interpret."""
    with db.session_scope() as s:
        out = check_negatives(
            s, [], [_case("Shortlisted Journal")],
            shortlist_venue_ids={three_venues["Shortlisted Journal"]},
            reachable_venue_ids={three_venues["Shortlisted Journal"]},
        )
    assert out[0].verdict == "shortlisted"
    assert out[0].caught is False


def test_unreachable_and_below_cut_both_count_but_stay_distinguishable(three_venues):
    """They are not the same result. `unreachable` means stage 2 could not have
    produced the venue however the scores fell; `below_cut` means it was
    produced and then not chosen. Collapsing them would hide which half of the
    pipeline did the work."""
    with db.session_scope() as s:
        out = check_negatives(
            s,
            [],
            [_case("Scored But Cut"), _case("Never Reached")],
            shortlist_venue_ids=set(),
            reachable_venue_ids={three_venues["Scored But Cut"]},
        )
    by_name = {o.venue_name: o for o in out}
    assert by_name["Scored But Cut"].verdict == "below_cut"
    assert by_name["Never Reached"].verdict == "unreachable"
    assert all(o.caught for o in out)

    summary = summarise_negatives(out)
    assert summary["caught"] == "2/2"
    assert summary["unreachable"] == 1 and summary["below_cut"] == 1


def test_the_summary_refuses_to_claim_the_other_half(three_venues):
    """Catching false positives says nothing about whether the journals the tool
    *does* suggest are good ones. The report has to say so, or the number gets
    read as a validation of the list."""
    with db.session_scope() as s:
        out = check_negatives(s, [], [_case("Never Reached")], set(), set())
    reading = summarise_negatives(out)["reading"]
    assert "bounds the false positives only" in reading
    assert "blind sheet" in reading


# --- 2. the blind sheet ---------------------------------------------------


@pytest.fixture()
def run_with_field(tmp_path, monkeypatch):
    """A run whose text is in one field, four finalists, and a pool of decoys —
    half in that field, half in another."""
    monkeypatch.setenv("DOVETAIL_DB", str(tmp_path / "b.db"))
    db.create_all(tmp_path / "b.db")
    # Names are deliberately uninformative: a fixture that calls a row
    # "Finalist 3" would let a test pass by reading the name, which is the very
    # leak the sheet exists to prevent.
    with db.session_scope() as s:
        run = MatchRun(
            title="A paper", abstract="x", status="done",
            text_profile={"topics": [{"field": {"display_name": "Social Sciences"}}]},
        )
        s.add(run)
        s.flush()
        chosen, same_field, other_field = [], [], []
        for i in range(4):
            v = Venue(
                display_name=f"Journal A{i}", is_core=True,
                topics=[{"id": "T1", "field": "Social Sciences"}],
            )
            s.add(v)
            s.flush()
            s.add(MatchResult(run_id=run.id, venue_id=v.id, bucket="shortlist", position=i + 1))
            chosen.append(v.id)
        for i in range(20):
            v = Venue(
                display_name=f"Journal B{i}", is_core=True,
                topics=[{"id": "T2", "field": "Social Sciences"}],
            )
            s.add(v)
            s.flush()
            same_field.append(v.id)
        for i in range(20):
            v = Venue(
                display_name=f"Journal C{i}", is_core=True,
                topics=[{"id": "T3", "field": "Chemistry"}],
            )
            s.add(v)
            s.flush()
            other_field.append(v.id)
        return {"run": run.id, "chosen": set(chosen),
                "same_field": set(same_field), "other_field": set(other_field)}


def test_the_sheet_gives_nothing_away(run_with_field):
    """No score, no position, no criteria. Anything the tool computed would tell
    the reader which rows are its own, and the answer would then measure
    agreement with a label instead of judgement of a journal."""
    with db.session_scope() as s:
        run = s.get(MatchRun, run_with_field["run"])
        rows = blind_sheet(s, run, seed=7)
        from dovetail.validation import sheet_markdown

        text = sheet_markdown(rows, run, 7)

    fields = set(vars(rows[0]))
    assert fields == {"n", "venue_id", "display_name", "publisher", "oa_model",
                      "apc_usd", "is_decoy"}

    # The judge is told decoys exist — the instructions say so outright, and
    # hiding it would be deceiving someone into doing unpaid work under a false
    # description. What must not be recoverable is **which row is which**, so
    # the test is per row: every line looks the same whatever it is.
    lines = [ln for ln in text.splitlines() if ln.startswith("| ") and ln[2].isdigit()]
    assert len(lines) == len(rows)
    for row, line in zip(sorted(rows, key=lambda r: r.n), lines):
        assert row.display_name in line
        for giveaway in ("decoy", "finalist", "score", "position", "rank"):
            assert giveaway not in line.lower()


def test_decoys_come_from_the_same_field(run_with_field):
    """A decoy from an unrelated discipline is rejected on sight and inflates the
    result. The control has to be plausible and still not what the tool chose."""
    with db.session_scope() as s:
        run = s.get(MatchRun, run_with_field["run"])
        rows = blind_sheet(s, run, seed=11)
    decoys = [r for r in rows if r.is_decoy]
    assert decoys, "no control at all"
    assert {r.venue_id for r in decoys} <= run_with_field["same_field"]
    assert not ({r.venue_id for r in rows} & run_with_field["other_field"])


def test_the_same_seed_rebuilds_the_same_sheet(run_with_field):
    """Scoring recovers which rows were decoys by rebuilding the shuffle, so
    nothing sitting on disk in between can tell the judge the answer."""
    with db.session_scope() as s:
        run = s.get(MatchRun, run_with_field["run"])
        a = blind_sheet(s, run, seed=42)
        b = blind_sheet(s, run, seed=42)
        c = blind_sheet(s, run, seed=43)
    assert [(r.n, r.venue_id) for r in a] == [(r.n, r.venue_id) for r in b]
    assert [(r.n, r.venue_id) for r in a] != [(r.n, r.venue_id) for r in c]


def test_a_judge_who_says_yes_to_everything_scores_zero_lift(run_with_field):
    """The reason the decoy rate is reported and not just the precision. Perfect
    agreement on the finalists is exactly what an indiscriminate judge produces,
    and precision alone cannot tell the two apart."""
    with db.session_scope() as s:
        run = s.get(MatchRun, run_with_field["run"])
        rows = blind_sheet(s, run, seed=5)
    report = score_sheet(rows, {r.n: True for r in rows})
    assert report["precision_at_n"] == 1.0
    assert report["decoy_rate"] == 1.0
    assert report["lift"] == 0.0


def test_a_discriminating_judge_shows_lift(run_with_field):
    with db.session_scope() as s:
        run = s.get(MatchRun, run_with_field["run"])
        rows = blind_sheet(s, run, seed=5)
    report = score_sheet(rows, {r.n: (not r.is_decoy) for r in rows})
    assert report["precision_at_n"] == 1.0
    assert report["decoy_rate"] == 0.0
    assert report["lift"] == 1.0


def test_unmarked_rows_are_reported_rather_than_counted_as_no(run_with_field):
    """A row nobody looked at is not a rejection, and silently treating it as one
    would move the number in the flattering direction."""
    with db.session_scope() as s:
        run = s.get(MatchRun, run_with_field["run"])
        rows = blind_sheet(s, run, seed=5)
    marks = {r.n: True for r in rows[:3]}
    report = score_sheet(rows, marks)
    assert set(report["unmarked"]) == {r.n for r in rows[3:]}
    assert report["finalists_judged"] + report["decoys_judged"] == 3


# --- reading a filled sheet back ------------------------------------------


def test_marks_can_be_written_either_way():
    """A person scoring twenty-four rows will reasonably not want to edit a
    markdown table, so the shorthand is accepted too."""
    table = """
| # | y/n | Journal | Publisher | Access | APC |
|---|-----|---------|-----------|--------|-----|
| 1 | y | A | — | full oa | — |
| 2 | n | B | — | hybrid | 3,200 |
| 3 |  | C | — | — | — |
"""
    assert parse_marks(table) == {1: True, 2: False}
    assert parse_marks("1y 2n 3y") == {1: True, 2: False, 3: True}
    assert parse_marks("1y, 2n,3y") == {1: True, 2: False, 3: True}
