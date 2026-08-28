"""Stage 5a, and the promises it must not break.

The judgement is the one part of Dovetail that is **not reproducible**: ask twice
and the sentence differs. Everything here defends the consequences of that — it
may inform a list, it may never order one, and it may never be mistaken for a
measurement.
"""

from __future__ import annotations

import json
import types

import pytest

from dovetail import crypto, db
from dovetail.matching import genre
from dovetail.models import Criterion, MatchResult, MatchRun, Venue


# --- a client that answers without a network ------------------------------


def fake_client(answer: dict, record: list | None = None):
    """The Anthropic client's shape, as far as this module touches it."""

    def create(**kwargs):
        if record is not None:
            record.append(kwargs)
        block = types.SimpleNamespace(type="text", text=json.dumps(answer))
        return types.SimpleNamespace(
            content=[block], stop_reason="end_turn", stop_details=None
        )

    return types.SimpleNamespace(messages=types.SimpleNamespace(create=create))


def refusing_client():
    def create(**kwargs):
        return types.SimpleNamespace(
            content=[],
            stop_reason="refusal",
            stop_details=types.SimpleNamespace(category="cyber", explanation="no"),
        )

    return types.SimpleNamespace(messages=types.SimpleNamespace(create=create))


FITS = {
    "publishes_this_kind": True,
    "confidence": "high",
    "manuscript_kind": "a preregistered vignette experiment",
    "journal_kind": "empirical studies of online behaviour",
    "sentence": "Its last twenty-five articles are all empirical, most of them survey experiments.",
}
MISFITS = {
    **FITS,
    "publishes_this_kind": False,
    "journal_kind": "conceptual essays in moral philosophy",
    "sentence": "Nothing in its recent index reports data; the register is entirely argumentative.",
}

RECENT = [{"title": "A survey experiment on trust", "year": 2026, "type": "article"}]


class FakeOpenAlex:
    """Records what it was asked for, so 'did it fetch again' is observable."""

    def __init__(self, titles=RECENT):
        self.titles = titles
        self.calls = []

    def recent_titles(self, session, openalex_id, per_page=25):
        self.calls.append(openalex_id)
        return self.titles


# --- fixtures -------------------------------------------------------------


@pytest.fixture()
def run_with_shortlist(tmp_path, monkeypatch):
    monkeypatch.setenv("DOVETAIL_DB", str(tmp_path / "genre.db"))
    db.create_all(tmp_path / "genre.db")
    with db.session_scope() as s:
        run = MatchRun(
            title="Transparency before accuracy",
            abstract="word " * 120,
            word_count=7400,
            status="done",
        )
        s.add(run)
        s.flush()
        ids = []
        for i in range(2):
            v = Venue(display_name=f"Journal {i}", openalex_id=f"S{i}", recent_titles=None)
            s.add(v)
            s.flush()
            s.add(
                MatchResult(
                    run_id=run.id, venue_id=v.id, bucket="shortlist",
                    position=i + 1, score_topic=0.4, score_subfield=0.5,
                )
            )
            ids.append(v.id)
        return run.id, ids


def _results(s, run_id):
    from sqlalchemy import select

    return list(
        s.scalars(
            select(MatchResult)
            .where(MatchResult.run_id == run_id, MatchResult.bucket == "shortlist")
            .order_by(MatchResult.position)
        )
    )


# --- the promise that matters ---------------------------------------------


def test_a_judgement_never_reorders_the_list(run_with_shortlist):
    """The rule the whole design rests on.

    A verdict is not reproducible, and a list ordered on something
    unreproducible cannot be explained — which is what `explain_match` promises
    it will be. So the verdict lands beside the scores and moves nothing, even
    when it disagrees with them.
    """
    run_id, _ = run_with_shortlist
    with db.session_scope() as s:
        run = s.get(MatchRun, run_id)
        before = [(r.venue_id, r.position, r.score_topic) for r in _results(s, run_id)]
        # The first journal fits, the second does not: maximum pressure to reorder.
        answers = iter([FITS, MISFITS])
        client = types.SimpleNamespace(
            messages=types.SimpleNamespace(
                create=lambda **kw: types.SimpleNamespace(
                    content=[types.SimpleNamespace(type="text", text=json.dumps(next(answers)))],
                    stop_reason="end_turn", stop_details=None,
                )
            )
        )
        genre.read_finalists(s, client, FakeOpenAlex(), run, _results(s, run_id))
        after = [(r.venue_id, r.position, r.score_topic) for r in _results(s, run_id)]
    assert before == after, "the judgement moved something"


def test_a_positive_verdict_becomes_a_criterion_of_merit(run_with_shortlist):
    """It is the column the two desk rejects were short of, so that is where it
    goes — and only when it is positive."""
    from sqlalchemy import select

    run_id, _ = run_with_shortlist
    with db.session_scope() as s:
        run = s.get(MatchRun, run_id)
        rows = _results(s, run_id)
        genre.read_finalists(s, fake_client(FITS), FakeOpenAlex(), run, rows[:1])
        genre.read_finalists(s, fake_client(MISFITS), FakeOpenAlex(), run, rows[1:])

        crits_fit = list(s.scalars(select(Criterion).where(Criterion.result_id == rows[0].id)))
        crits_miss = list(s.scalars(select(Criterion).where(Criterion.result_id == rows[1].id)))

    assert [c.label for c in crits_fit] == [genre.GENRE_LABEL]
    assert crits_fit[0].kind.value == "merit"
    assert genre.MODEL in crits_fit[0].evidence
    # A negative verdict is not a logistical criterion and not a constraint. It
    # is a flag, and dressing it as either would make it look measured.
    assert crits_miss == []


def test_reading_the_same_run_twice_replaces_the_criterion(run_with_shortlist):
    """Otherwise two contradictory verdicts sit side by side and the second is
    indistinguishable from a second journal agreeing."""
    from sqlalchemy import select

    run_id, _ = run_with_shortlist
    with db.session_scope() as s:
        run = s.get(MatchRun, run_id)
        rows = _results(s, run_id)[:1]
        genre.read_finalists(s, fake_client(FITS), FakeOpenAlex(), run, rows)
        genre.read_finalists(s, fake_client(FITS), FakeOpenAlex(), run, rows)
        labels = [
            c.label
            for c in s.scalars(select(Criterion).where(Criterion.result_id == rows[0].id))
        ]
    assert labels == [genre.GENRE_LABEL]

    # And a verdict that flips removes the old claim rather than joining it.
    with db.session_scope() as s:
        run = s.get(MatchRun, run_id)
        rows = _results(s, run_id)[:1]
        genre.read_finalists(s, fake_client(MISFITS), FakeOpenAlex(), run, rows)
        labels = [
            c.label
            for c in s.scalars(select(Criterion).where(Criterion.result_id == rows[0].id))
        ]
    assert labels == []


def test_one_journal_failing_does_not_cost_the_others_their_verdict(run_with_shortlist):
    """A hand-declared journal has no OpenAlex index to read, and a model may
    decline. Neither is a reason for the other eleven to go unjudged."""
    run_id, venue_ids = run_with_shortlist
    with db.session_scope() as s:
        # The second journal is not in OpenAlex at all.
        s.get(Venue, venue_ids[1]).openalex_id = None
        run = s.get(MatchRun, run_id)
        verdicts, failures = genre.read_finalists(
            s, fake_client(FITS), FakeOpenAlex(), run, _results(s, run_id)
        )
    assert len(verdicts) == 1
    assert len(failures) == 1
    assert "not in OpenAlex" in failures[0]["reason"]


def test_a_refusal_is_a_failure_and_not_a_crash(run_with_shortlist):
    """A refusal comes back as a 200 with no usable body. Reading `content`
    first turns that into an IndexError three frames from the cause."""
    run_id, _ = run_with_shortlist
    with db.session_scope() as s:
        run = s.get(MatchRun, run_id)
        verdicts, failures = genre.read_finalists(
            s, refusing_client(), FakeOpenAlex(), run, _results(s, run_id)
        )
    assert verdicts == []
    assert len(failures) == 2 and "declined" in failures[0]["reason"]


def test_the_recent_index_is_fetched_once_and_then_cached(run_with_shortlist):
    """It is inventory: 10 credits a journal, and it changes on the timescale of
    an issue rather than of a consultation."""
    run_id, _ = run_with_shortlist
    oa = FakeOpenAlex()
    with db.session_scope() as s:
        run = s.get(MatchRun, run_id)
        genre.read_finalists(s, fake_client(FITS), oa, run, _results(s, run_id))
        genre.read_finalists(s, fake_client(FITS), oa, run, _results(s, run_id))
    assert len(oa.calls) == 2, "the second pass re-fetched an index it already had"


def test_the_estimate_reports_the_two_currencies_apart(run_with_shortlist):
    """They come out of different pockets — OpenAlex credits from a shared daily
    budget, model tokens from one person's key — so a single total would mean
    nothing."""
    run_id, _ = run_with_shortlist
    with db.session_scope() as s:
        est = genre.cost_estimate(s, _results(s, run_id))
    assert est["venues"] == 2
    assert est["index_fetches"] == 2 and est["openalex_credits"] == 20
    assert est["model_calls"] == 2 and est["usd_estimate"] > 0
    assert "usd" not in str(est["openalex_credits"])


# --- what the model is told -----------------------------------------------


def test_the_prompt_says_which_question_is_not_being_asked():
    """Handed a manuscript and a journal, a model answers the subject question
    unless told not to: it is easier, it sounds confident, and stages 3 and 4
    already answered it."""
    assert "NOT being asked whether the subject matches" in genre.SYSTEM
    assert "KIND" in genre.SYSTEM
    # And it must be allowed to say no, or the whole pass is a rubber stamp.
    assert "willing to answer false" in genre.SYSTEM


def test_the_manuscript_is_the_cached_prefix(run_with_shortlist):
    """Rendering order is tools, system, messages. With the manuscript in the
    system prompt it is a stable prefix across all twelve calls; with the journal
    in the user message the varying half sits after the breakpoint."""
    seen = []
    genre.judge(
        fake_client(FITS, record=seen),
        "A title", "an abstract", 7000, 1, "Some Journal", RECENT,
    )
    kwargs = seen[0]
    assert kwargs["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "A title" in kwargs["system"][-1]["text"]
    assert "Some Journal" in kwargs["messages"][0]["content"]
    assert "A title" not in kwargs["messages"][0]["content"]
    assert kwargs["output_config"]["format"]["schema"]["additionalProperties"] is False


def test_a_venue_with_no_index_is_refused_before_the_call():
    """A journal's name is not evidence about the shape of what it prints, so
    there is nothing to judge and the call is not worth making."""
    with pytest.raises(genre.GenreUnavailable, match="no recent index"):
        genre.judge(fake_client(FITS), "T", "a", None, 1, "Nameless Journal", [])


# --- the key ---------------------------------------------------------------


def test_a_key_round_trips_and_is_never_shown_whole(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("FERNET_KEY", Fernet.generate_key().decode())
    crypto._fernet.cache_clear()

    plain = "sk-ant-api03-SECRETSECRETSECRET-9xyz"
    stored = crypto.encrypt_api_key(plain)
    assert plain not in stored
    assert crypto.decrypt_api_key(stored) == plain

    masked = crypto.mask_api_key(plain)
    assert masked.endswith("9xyz") and "SECRET" not in masked


def test_without_a_fernet_key_storing_one_is_refused_not_improvised(monkeypatch):
    """A key kept in plain text because the encryption was unavailable is worse
    than a feature that says it is off."""
    monkeypatch.delenv("FERNET_KEY", raising=False)
    crypto._fernet.cache_clear()
    assert crypto.available() is False
    with pytest.raises(crypto.CryptoUnavailable, match="FERNET_KEY"):
        crypto.encrypt_api_key("sk-ant-whatever")
