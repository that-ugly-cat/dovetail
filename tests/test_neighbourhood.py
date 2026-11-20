"""The citation neighbourhood: a second road to a candidate. SPEC.md §6b.

The fake client answers in the shape OpenAlex answers — copied key by key from
real responses of 20 Nov 2026, with the identities replaced. What the numbers
are arranged to show is the three things the measure has to get right, each of
which it got wrong in a first version run against a real bibliography:

- a megajournal that cites everything must not lead the list;
- a journal reached through one heavily cited reference must not be in it;
- the journals of a minority field must surface somewhere, although the
  majority field outvotes them in the overall count.
"""

from __future__ import annotations

import ast
import inspect
from datetime import date

import pytest

from dovetail import config, db
from dovetail.matching import neighbourhood as nb
from dovetail.matching import pipeline
from dovetail.models import Criterion, MatchResult, MatchRun, Venue

OA = "https://openalex.org/"
TODAY = date(2026, 11, 20)


# --- the bibliography -----------------------------------------------------

# Twelve references: eight in Medicine, four in Arts and Humanities. That is a
# bibliography two-thirds about the paper's subject and one third about its
# argument, which is the shape that hides the right journals.
MEDICINE = [f"W10{i}" for i in range(8)]
HUMANITIES = [f"W20{i}" for i in range(4)]


def _work(wid: str, field: str, source: str) -> dict:
    return {
        "id": OA + wid,
        "doi": f"https://doi.org/10.1000/{wid.lower()}",
        "title": f"Reference {wid}",
        "publication_year": 2021,
        "primary_location": {
            "source": {"id": OA + source, "display_name": source, "type": "journal"}
        },
        "primary_topic": {
            "id": OA + "T10001",
            "display_name": "A topic",
            "field": {"id": "https://openalex.org/fields/27", "display_name": field},
        },
    }


WORKS = [_work(w, "Medicine", "S_CLIN" if i < 2 else "S_ELSEWHERE") for i, w in enumerate(MEDICINE)] + [
    _work(w, "Arts and Humanities", "S_ELSEWHERE") for w in HUMANITIES
]

# Who cites each reference, as (source, citing works).
CITERS: dict[str, list[tuple[str, int]]] = {}
for w in MEDICINE:
    CITERS[w] = [("S_MEGA", 5), ("S_CLIN", 6), ("S_REPO", 9)]
for w in HUMANITIES:
    CITERS[w] = [("S_MEGA", 5), ("S_ETH", 3), ("S_REPO", 9)]
CITERS["W100"] += [("S_ETH", 1), ("S_ONE", 30)]
CITERS["W200"] += [("S_BARE", 1), ("S_TINY", 2)]
CITERS["W201"] += [("S_BARE", 1), ("S_TINY", 2)]


def _source(sid: str, recent: int, **extra) -> dict:
    return {
        "id": OA + sid,
        "display_name": sid,
        "type": "journal",
        "is_core": True,
        "issn_l": None,
        "works_count": recent * 3,
        "counts_by_year": [
            {"year": 2026, "works_count": recent // 2, "cited_by_count": 0},
            {"year": 2023, "works_count": recent - recent // 2, "cited_by_count": 0},
            # Before the window: must not be counted as recent output.
            {"year": 2015, "works_count": 10_000, "cited_by_count": 0},
        ],
        "topics": [],
        **extra,
    }


def _topic(tid: str, sub: str, fld: str) -> dict:
    return {
        "id": OA + tid, "display_name": tid, "count": 100,
        "subfield": {"display_name": sub}, "field": {"display_name": fld},
    }


CLINICAL = [_topic("T1", "Pulmonary and Respiratory Medicine", "Medicine")]
SOURCES = {
    "S_MEGA": _source("S_MEGA", 100_000, topics=CLINICAL),
    "S_CLIN": _source("S_CLIN", 400, topics=CLINICAL),
    "S_ETH": _source("S_ETH", 300, topics=[_topic("T9", "Philosophy", "Arts and Humanities")]),
    # Reached by two references and with no topics at all: the unclassifiable case.
    "S_BARE": _source("S_BARE", 200),
    "S_ONE": _source("S_ONE", 100, topics=CLINICAL),
    "S_TINY": _source("S_TINY", 10),
    "S_REPO": _source("S_REPO", 50_000, type="repository"),
}


class FakeOpenAlex:
    def __init__(self):
        self.calls: list[str] = []

    # stage 1 and 2: one topic, one journal found by it
    def classify_text(self, session, title, abstract):
        self.calls.append("classify_text")
        return {"topics": [{
            "id": OA + "T1", "display_name": "T1", "score": 0.9,
            "subfield": {"display_name": "Pulmonary and Respiratory Medicine"},
            "field": {"display_name": "Medicine"},
        }]}

    def sources_by_topics(self, session, topic_ids, max_pages=25):
        self.calls.append("sources_by_topics")
        return {"meta": {"count": 1}, "results": [SOURCES["S_CLIN"]],
                "truncated": False, "pages": 1}

    def journals_publishing_on(self, session, topic_ids, per_page=200):
        self.calls.append("journals_publishing_on")
        return []

    # the neighbourhood
    def works_by_dois(self, session, dois):
        self.calls.append("works_by_dois")
        known = {w["doi"].replace("https://doi.org/", ""): w for w in WORKS}
        return [known[d] for d in dois if d in known]

    def sources_citing(self, session, work_id, since):
        self.calls.append("sources_citing")
        self.since = since
        groups = CITERS[work_id]
        return {"count": sum(n for _, n in groups), "groups": list(groups)}

    def sources_by_ids(self, session, ids):
        self.calls.append("sources_by_ids")
        return [SOURCES[i] for i in ids if i in SOURCES]


DOIS = [f"10.1000/{w.lower()}" for w in MEDICINE + HUMANITIES]


@pytest.fixture()
def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DOVETAIL_DB", str(tmp_path / "n.db"))
    db.create_all(tmp_path / "n.db")
    with db.session_scope() as s:
        yield s


def _build(session):
    return nb.build(session, FakeOpenAlex(), DOIS, pipeline.upsert_venue, today=TODAY)


# --- reading a reference list ---------------------------------------------


def test_dois_are_found_wherever_a_reference_list_puts_them():
    text = """
    Fricker M. Epistemic injustice. Oxford: OUP; 2007.
    Pope CJ, et al. J Obstet Gynaecol Can. 2015. doi:10.1016/S1701-2163(16)30050-0.
    Seear K. (2009). https://doi.org/10.1080/09581590802011625
    Again, in brackets (https://doi.org/10.1080/09581590802011625).
    Old Wiley form: 10.1002/(SICI)1097-0258(19980815)17:15<1661::AID-SIM968>3.0.CO;2-2
    """
    assert nb.extract_dois(text) == [
        "10.1016/s1701-2163(16)30050-0",
        "10.1080/09581590802011625",
        # A known loss, pinned so it is not mistaken for a success: the SICI
        # form carries angle brackets, the pattern stops at them so as not to
        # swallow markup, and what is left does not resolve. It is then counted
        # among the unresolved references, which the report shows.
        "10.1002/(sici)1097-0258(19980815)17:15",
    ]


def test_a_reference_with_no_doi_is_not_seen_and_the_report_counts_it(session):
    """A bibliography of monographs must show up as a thin one, not pass."""
    found = nb.build(session, FakeOpenAlex(), DOIS[:3] + ["10.9999/unknown"],
                     pipeline.upsert_venue, today=TODAY)
    assert found.report["references_given"] == 4
    assert found.report["references_resolved"] == 3
    assert found.report["skipped"] == "too_few_references"
    assert found.overall == [] and found.by_field == {}


# --- what the measure has to get right ------------------------------------


def test_a_megajournal_that_cites_everything_does_not_lead(session):
    """Counted raw, the journal that publishes a hundred thousand papers is
    reached through every reference and wins. Per thousand of what it publishes,
    it is the least engaged journal on the list."""
    found = _build(session)
    order = [e.openalex_id for e in found.overall]
    mega = next(e for e in found.overall if e.openalex_id == "S_MEGA")
    assert mega.refs_reached == 12 and mega.citing_links == 60
    assert order[0] == "S_CLIN"
    assert order.index("S_ETH") < order.index("S_MEGA")
    assert mega.per_1000 < 1


def test_one_heavily_cited_reference_does_not_put_its_readership_in_the_list(session):
    found = _build(session)
    assert found.report["breadth_gate"] == 2
    assert "S_ONE" not in [e.openalex_id for e in found.overall]


def test_a_journal_too_small_to_have_a_density_is_not_given_one(session):
    """Four citations over ten recent works is the highest density here, and it
    is two papers."""
    found = _build(session)
    assert "S_TINY" not in [e.openalex_id for e in found.overall]


def test_recent_output_is_counted_over_the_citation_window_only(session):
    found = _build(session)
    assert found.report["window_since"] == "2021-01-01"
    clin = found.find("S_CLIN")
    assert clin.recent_works == 400  # and not 400 plus the ten thousand of 2015
    assert clin.per_1000 == 120.0
    assert clin.cited_here == 2


def test_a_minority_field_gets_its_own_reading(session):
    """Two thirds of the references are clinical. Read together, the clinical
    journal leads; read inside the humanities references alone, the ethics
    journal does — and that is the list the paper's argument belongs to."""
    found = _build(session)
    assert found.report["fields"] == {"Medicine": 8, "Arts and Humanities": 4}
    # Medicine holds 67% of the references: it is the majority, and is not
    # repeated as a conversation of its own.
    assert found.report["minority_fields"] == ["Arts and Humanities"]
    inside = found.by_field["Arts and Humanities"]
    assert inside[0].openalex_id == "S_ETH"
    assert inside[0].via_field == "Arts and Humanities"
    assert inside[0].refs_reached == 4 and inside[0].refs_total == 4
    assert "S_CLIN" not in [e.openalex_id for e in inside]


# --- in a consultation ----------------------------------------------------


def _run(session, references=DOIS, **kw):
    return pipeline.run_match_full(
        session, FakeOpenAlex(), "A paper", "word " * 100,
        references=references, **kw,
    )


def test_a_venue_both_roads_reach_stays_in_the_shortlist_and_says_so(session):
    out = _run(session)
    assert [r.venue.display_name for r in out.shortlist][0] == "S_CLIN"
    row = out.shortlist[0]
    assert row.neighbourhood is not None
    labels = [c.label for c in row.criteria]
    assert "its recent papers cite 8 of your 12 references" in labels
    assert "published 2 of the works you cite" in labels
    assert "S_CLIN" not in [r.venue.display_name for r in out.neighbourhood]


def test_the_journals_the_topics_cannot_reach_come_out_in_their_own_basket(session):
    """S_ETH shares no topic with the text: stage 2 orders every reachable
    venue before it and it can never be shortlisted. The references reach it."""
    out = _run(session)
    near = [r.venue.display_name for r in out.neighbourhood]
    assert "S_ETH" in near
    eth = next(r for r in out.neighbourhood if r.venue.display_name == "S_ETH")
    assert eth.score.stage2_reachable is False
    stored = session.query(MatchResult).filter_by(
        run_id=out.run.id, venue_id=eth.venue.id).one()
    assert stored.bucket == "neighbourhood"
    assert stored.neighbourhood["refs_reached"] == 5
    assert stored.neighbourhood["per_1000"] == pytest.approx(43.33)
    crits = session.query(Criterion).filter_by(result_id=stored.id).all()
    assert any("cite 5 of your 12 references" in c.label and c.kind.value == "merit"
               for c in crits)


def test_a_repository_the_references_lead_to_is_still_not_a_candidate(session):
    """Preprint servers cite everything. The neighbourhood finds them and the
    same constraint that removes them from the topic channel removes them here."""
    out = _run(session)
    names = {r.venue.display_name for r in out.shortlist + out.neighbourhood + out.conversations}
    assert "S_REPO" not in names


def test_a_venue_with_no_topic_profile_is_no_longer_unmeasurable(session):
    """«Unclassifiable» means there is nothing to measure a venue with. For one
    the references reach, that stopped being true."""
    out = _run(session)
    assert "S_BARE" in [r.venue.display_name for r in out.neighbourhood]
    assert "S_BARE" not in [r.venue.display_name for r in out.unclassifiable]


def test_positions_are_numbered_inside_each_basket(session):
    out = _run(session)
    rows = session.query(MatchResult).filter_by(run_id=out.run.id).all()
    for bucket in ("shortlist", "neighbourhood"):
        positions = sorted(r.position for r in rows if r.bucket == bucket)
        assert positions == list(range(1, len(positions) + 1))


def test_the_smaller_conversations_take_what_the_overall_list_left(session, monkeypatch):
    """With the overall basket full, the minority field still gets its journals."""
    found = _build(session)
    rows = pipeline.evaluate_all(
        session, [], list(session.query(Venue)), {}, None)
    monkeypatch.setattr(config, "MAX_SHORTLIST", 1)
    near, conversations, _ = pipeline.place_neighbourhood(rows, [], [], found)
    assert [r.venue.display_name for r in near] == ["S_CLIN"]
    assert [r.venue.display_name for r in conversations][0] == "S_ETH"
    assert conversations[0].neighbourhood.via_field == "Arts and Humanities"


def test_the_run_keeps_what_it_was_computed_from(session):
    out = _run(session)
    assert out.run.reference_dois == DOIS
    report = out.run.constraints["_neighbourhood"]
    assert report["references_resolved"] == 12
    assert report == out.neighbourhood_report


def test_without_references_nothing_changes(session):
    client = FakeOpenAlex()
    out = pipeline.run_match_full(session, client, "A paper", "word " * 100)
    assert out.neighbourhood == [] and out.conversations == []
    assert out.neighbourhood_report is None
    assert "works_by_dois" not in client.calls
    assert "_neighbourhood" not in (out.run.constraints or {})


def test_the_references_are_read_even_when_the_topic_sweep_is_off(session):
    """`discover` is about the topic sweep. Nothing in the table remembers where
    a reference list is cited, so a run that re-reads the table still has to ask."""
    client = FakeOpenAlex()
    out = pipeline.run_match_full(
        session, client, "A paper", "word " * 100, references=DOIS, discover=False)
    assert "sources_by_topics" not in client.calls
    assert client.calls.count("sources_citing") == 12
    assert out.neighbourhood


# --- what it costs --------------------------------------------------------


def test_the_estimate_names_every_call_the_neighbourhood_makes():
    """The same guard stage 2 has, for the same reason: an estimate written away
    from the calls it prices drifts under them, and is believed anyway."""
    tree = ast.parse(inspect.getsource(nb.build))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "client"
    }
    priced = {t["call"] for t in pipeline.cost_terms(discover=False, references=12)}
    assert called and called <= priced


def test_the_ceiling_is_above_what_two_real_runs_spent():
    """20 Nov 2026, measured from `X-RateLimit-Credits-Used`: 89 references cost
    144 credits and 55 cost 105, fetching every source the groups named. The
    fetch is now bounded by the breadth gate, so a run spends less than that —
    but the ceiling must hold against the unbounded figure too."""
    without = pipeline.cost_ceiling(discover=False)
    assert pipeline.cost_ceiling(discover=False, references=89) - without >= 89 + 3
    assert pipeline.cost_ceiling(discover=False, references=55) - without >= 55 + 2
    # A longer list than the cap costs what the cap costs.
    assert pipeline.cost_ceiling(references=10_000) == pipeline.cost_ceiling(
        references=config.MAX_REFERENCES)
