"""Stage 5b: article types read off a journal's author guidelines.

Most of what is tested here is what the stage refuses to pass on. A model asked
for a word limit will produce one, so the interesting property is not that the
extraction works on a clean page but that a number which is not on the page
does not reach the queue — and that a limit never travels without what it
counts, which is the distinction the first real consultation turned on.
"""

from __future__ import annotations

import json
import socket
import types

import httpx
import pytest

from dovetail import db, proposals
from dovetail.matching import criteria, guidelines
from dovetail.models import ArticleType, Proposal, ProposalStatus, Venue
from dovetail.sources import guidelines as pages

# A guidelines page in the shape publishers give them: a table of article types,
# limits stated with and without what they count, one type by invitation.
PAGE_HTML = """
<html><head><title>Submission guidelines</title><script>track()</script></head>
<body><nav>Home</nav>
<h2>Article types</h2>
<table>
<tr><th>Type</th><th>Length</th><th>Abstract</th></tr>
<tr><td>Original Article</td>
    <td>Up to 8,000 words, including references and endnotes but excluding the abstract.</td>
    <td>No more than 250 words, unstructured.</td></tr>
<tr><td>Brief Report</td>
    <td>3000 words excluding references. Up to 2 tables or figures and 20 references.</td>
    <td>150 words.</td></tr>
<tr><td>Commentary</td><td>By invitation only. Commentaries should not exceed 1,500 words.</td><td></td></tr>
<tr><td>Review</td><td>Reviews are 5,000&ndash;10,000 words.</td><td></td></tr>
</table>
</body></html>
"""
PAGE = pages.html_to_text(PAGE_HTML)

ORIGINAL = {
    "name": "Original Article",
    "word_limit": 8000,
    "word_limit_scope": "including references and endnotes, excluding the abstract",
    "abstract_limit": 250,
    "refs_limit": None,
    "figures_limit": None,
    "unsolicited": None,
    "evidence": "Original Article | Up to 8,000 words, including references and endnotes "
                "but excluding the abstract. | No more than 250 words, unstructured.",
}
BRIEF = {
    "name": "Brief Report",
    "word_limit": 3000,
    "word_limit_scope": "excluding references",
    "abstract_limit": 150,
    "refs_limit": 20,
    "figures_limit": 2,
    "unsolicited": None,
    "evidence": "Brief Report | 3000 words excluding references. Up to 2 tables or "
                "figures and 20 references. | 150 words.",
}
COMMENTARY = {
    "name": "Commentary",
    "word_limit": 1500,
    "word_limit_scope": "",
    "abstract_limit": None,
    "refs_limit": None,
    "figures_limit": None,
    "unsolicited": False,
    "evidence": "By invitation only. Commentaries should not exceed 1,500 words.",
}
REVIEW = {
    "name": "Review",
    "word_limit": 10000,
    "word_limit_scope": "not stated",
    "abstract_limit": None,
    "refs_limit": None,
    "figures_limit": None,
    "unsolicited": None,
    "evidence": "Reviews are 5,000–10,000 words.",
}


def fake_client(answer: dict, record: list | None = None, stop_reason: str = "end_turn"):
    def create(**kwargs):
        if record is not None:
            record.append(kwargs)
        content = [] if stop_reason == "refusal" else [
            types.SimpleNamespace(type="text", text=json.dumps(answer))
        ]
        return types.SimpleNamespace(
            content=content,
            stop_reason=stop_reason,
            stop_details=types.SimpleNamespace(category="cyber", explanation="no"),
        )

    return types.SimpleNamespace(messages=types.SimpleNamespace(create=create))


def _answer(*items, is_guidelines=True):
    return {"article_types": list(items), "page_is_guidelines": is_guidelines}


# --- reading the page -----------------------------------------------------


def test_a_table_keeps_its_cells_apart_and_scripts_are_not_text():
    assert "track()" not in PAGE and "Submission guidelines" not in PAGE
    # Run together, «…the abstract.» and «No more than 250 words» read as one
    # sentence about the main text.
    assert "excluding the abstract. | No more than 250 words" in PAGE
    assert "5,000–10,000 words" in PAGE


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture()
def public_dns(monkeypatch):
    def resolve(host, port, *a, **kw):
        ip = "10.0.0.5" if host == "internal.example" else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)


def test_a_publisher_that_refuses_is_a_code_the_caller_can_act_on(public_dns):
    """Wiley, OUP and BMJ answered 403 on 8 Sep 2026. It is not transient and
    not a bug: the page has to be read some other way and handed in."""
    client = _client(lambda request: httpx.Response(403, text="Just a moment..."))
    with pytest.raises(pages.Blocked) as e:
        pages.fetch_text("https://publisher.example/authors", client)
    assert e.value.code == "blocked_by_publisher"
    assert "publisher.example answered 403" in e.value.detail


def test_the_request_says_what_it_is(public_dns):
    seen = {}

    def handler(request):
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200, html=PAGE_HTML)

    assert "Original Article" in pages.fetch_text("https://publisher.example/a", _client(handler))
    assert seen["ua"].startswith("dovetail/")


def test_a_redirect_into_the_private_network_is_not_followed(public_dns):
    """The fetch runs on a box whose other applications listen on loopback, and
    the URL comes from the caller. Checking only the first hop would check
    nothing."""
    def handler(request):
        if request.url.host == "publisher.example":
            return httpx.Response(302, headers={"location": "http://internal.example/admin"})
        raise AssertionError("the private host was requested")

    with pytest.raises(pages.Unsupported) as e:
        pages.fetch_text("https://publisher.example/a", _client(handler))
    assert "not a public address" in e.value.detail


def test_a_pdf_is_declined_rather_than_half_read(public_dns):
    client = _client(lambda r: httpx.Response(
        200, content=b"%PDF-1.7 ...", headers={"content-type": "application/pdf"}))
    with pytest.raises(pages.Unsupported):
        pages.fetch_text("https://publisher.example/guide.pdf", client)


def test_a_page_too_long_to_read_whole_is_refused_and_not_cut():
    with pytest.raises(pages.Unsupported):
        pages.clean_text("word " * 60_000)


# --- the check ------------------------------------------------------------


def test_limits_the_page_supports_are_kept_with_what_they_count():
    kept, dropped = guidelines.check([ORIGINAL, BRIEF], PAGE)
    assert dropped == []
    assert [(t["name"], t["word_limit"], t["word_limit_scope"]) for t in kept] == [
        ("Original Article", 8000, "including references and endnotes, excluding the abstract"),
        ("Brief Report", 3000, "excluding references"),
    ]
    assert kept[1]["refs_limit"] == 20 and kept[1]["figures_limit"] == 2


def test_a_number_that_is_not_in_its_own_evidence_is_dropped():
    """The quote is real and the number is not in it: the commonest way an
    extraction goes wrong, because it looks sourced."""
    invented = {**ORIGINAL, "word_limit": 9000, "refs_limit": 60}
    kept, dropped = guidelines.check([invented], PAGE)
    assert kept[0]["word_limit"] is None and kept[0]["refs_limit"] is None
    assert kept[0]["word_limit_scope"] is None  # no limit, so nothing to scope
    assert kept[0]["abstract_limit"] == 250  # the supported one survives
    assert {d["why"] for d in dropped} == {
        "word_limit 9000 is not in the evidence quoted for it",
        "refs_limit 60 is not in the evidence quoted for it",
    }


def test_evidence_that_is_not_on_the_page_takes_the_whole_type_with_it():
    made_up = {**BRIEF, "name": "Letter", "evidence": "Letters may be up to 3000 words."}
    kept, dropped = guidelines.check([made_up], PAGE)
    assert kept == []
    assert dropped[0]["why"] == "the evidence quoted is not on the page"
    assert dropped[0]["claimed"]["word_limit"] == 3000


def test_a_limit_never_travels_without_what_it_counts():
    """The model left the scope empty. It becomes «not stated», which is a fact
    about the page, rather than staying empty, which reads as nothing to say."""
    kept, _ = guidelines.check([COMMENTARY], PAGE)
    assert kept[0]["word_limit"] == 1500
    assert kept[0]["word_limit_scope"] == "not stated"
    assert kept[0]["unsolicited"] is False


def test_a_range_is_matched_on_its_upper_end_and_8000_is_not_found_in_18000():
    kept, dropped = guidelines.check([REVIEW], PAGE)
    assert kept[0]["word_limit"] == 10000 and dropped == []
    assert guidelines._number_in(8000, "no more than 18,000 words") is False
    assert guidelines._number_in(8000, "8 000 mots") and guidelines._number_in(8000, "8.000 Wörter")


# --- the call -------------------------------------------------------------


def test_one_page_is_one_call_to_the_small_model_with_a_fixed_shape():
    record = []
    found = guidelines.extract(fake_client(_answer(ORIGINAL, BRIEF), record), "A Journal", PAGE)
    assert [t["name"] for t in found.types] == ["Original Article", "Brief Report"]
    (call,) = record
    assert call["model"] == guidelines.MODEL == "claude-haiku-5-5"
    assert call["output_config"]["format"]["type"] == "json_schema"
    # The page goes in whole. Cutting it would drop limits without saying so.
    assert PAGE in call["messages"][0]["content"]
    # Thinking is on by default on this model and shares the allowance.
    assert call["max_tokens"] >= 16000


def test_a_refusal_and_a_truncated_answer_are_reported_not_parsed():
    with pytest.raises(guidelines.ExtractionUnavailable, match="declined"):
        guidelines.extract(fake_client({}, stop_reason="refusal"), "A Journal", PAGE)
    with pytest.raises(guidelines.ExtractionUnavailable, match="cut off"):
        guidelines.extract(fake_client({}, stop_reason="max_tokens"), "A Journal", PAGE)


def test_a_page_that_is_not_guidelines_says_so():
    found = guidelines.extract(fake_client(_answer(is_guidelines=False)), "A Journal", "Log in")
    assert found.types == [] and found.page_is_guidelines is False


# --- from the queue to the table ------------------------------------------


@pytest.fixture()
def venue(tmp_path, monkeypatch):
    monkeypatch.setenv("DOVETAIL_DB", str(tmp_path / "g.db"))
    db.create_all(tmp_path / "g.db")
    with db.session_scope() as s:
        v = Venue(display_name="A Journal")
        s.add(v)
        s.flush()
        return v.id


URL = "https://publisher.example/authors"


def _file(s, venue_id, *items):
    found = guidelines.extract(fake_client(_answer(*items)), "A Journal", PAGE)
    return guidelines.propose(s, s.get(Venue, venue_id), URL, found)


def test_what_was_read_waits_in_the_queue_and_writes_nothing(venue):
    with db.session_scope() as s:
        p = _file(s, venue, ORIGINAL, COMMENTARY)
        assert p.status is ProposalStatus.PENDING and p.kind == "update_venue"
        assert p.source_url == URL
        # One scope is «not stated», so the proposal does not claim to be sure.
        assert p.confidence == "medium"
        assert s.query(ArticleType).count() == 0


def test_approving_writes_the_types_dated_and_sourced(venue):
    """This is the half that did not exist: until 20 Nov 2026 approving a
    proposal that carried article types reported them as ignored and wrote
    nothing, so the table the format criterion reads could not be filled."""
    with db.session_scope() as s:
        p = _file(s, venue, ORIGINAL, BRIEF)
        out = proposals.approve(s, p.id, "spit")
        assert [w["name"] for w in out["article_types"]["written"]] == [
            "Original Article", "Brief Report"]
        assert out["ignored"] == []
        rows = {t.name: t for t in s.query(ArticleType)}
        assert rows["Original Article"].word_limit == 8000
        assert rows["Original Article"].word_limit_scope.startswith("including references")
        assert rows["Brief Report"].refs_limit == 20
        assert all(t.source_url == URL and t.verified_at for t in rows.values())


def test_reading_the_same_page_again_updates_the_row(venue):
    with db.session_scope() as s:
        proposals.approve(s, _file(s, venue, ORIGINAL).id, "spit")
        later = {**ORIGINAL, "name": "original article", "abstract_limit": None}
        proposals.approve(s, _file(s, venue, later).id, "spit")
        (row,) = s.query(ArticleType).all()
        assert row.word_limit == 8000 and row.abstract_limit is None


def test_a_hand_filed_limit_with_no_scope_is_not_written(venue):
    """`propose_update` takes any dictionary. The rule is enforced where a
    suggestion becomes a fact, not only where this module produces one."""
    with db.session_scope() as s:
        p = Proposal(
            kind="update_venue", venue_id=venue, rationale="read by hand",
            fields={
                "apc_usd": 3850,
                "word_count_max": 9000,
                "article_types": [
                    {"name": "Essay", "word_limit": 9000},
                    {"name": "Note", "word_limit": 2000,
                     "word_limit_scope": "excluding references", "colour": "blue"},
                    "Original research 5,000-9,000",
                ],
            },
        )
        s.add(p)
        s.flush()
        out = proposals.approve(s, p.id, "spit")
        assert out["written"] == ["apc_usd"] and out["ignored"] == ["word_count_max"]
        assert out["article_types"]["written"] == [{"name": "Note", "ignored_keys": ["colour"]}]
        assert [k["why"] for k in out["article_types"]["skipped"]] == [
            "word_limit without word_limit_scope", "no name"]
        assert [t.name for t in s.query(ArticleType)] == ["Note"]


# --- what the list says with it -------------------------------------------


class _Type:
    def __init__(self, name, limit, scope):
        self.name, self.word_limit, self.word_limit_scope = name, limit, scope
        self.source_url = URL


class _Venue:
    oa_model = None
    apc_usd = None
    publication_time_weeks = None
    indexed_in = None

    def __init__(self, *article_types):
        self.article_types = list(article_types)


class _Score:
    topic = subfield = field = 0.0
    stage2_reachable = False


def test_the_criterion_says_what_the_limit_counts():
    v = _Venue(_Type("Original Article", 8000, "including references"),
               _Type("Essay", 9000, None))
    fits = [c for c in criteria.build(v, _Score(), [], 7600) if c.label.startswith("format fits")]
    assert fits[0].label == "format fits: Original Article (8000 words; counts: including references)"
    fits = [c for c in criteria.build(v, _Score(), [], 8500) if c.label.startswith("format fits")]
    assert "what it counts is not on record" in fits[0].label


def test_a_manuscript_over_every_limit_is_said_and_not_passed_in_silence():
    """The shape of the first real failure: 9,300 words against seven formats,
    none of which it fitted, and nothing on the row to say so."""
    v = _Venue(_Type("Original Article", 8000, "including references"))
    crits = criteria.build(v, _Score(), [], 9300)
    over = [c for c in crits if c.label.startswith("over every word limit")]
    assert over and over[0].kind == criteria.LOGISTICS and over[0].weight == 0.0
    assert "8000 words; counts: including references, against 9300" in over[0].label
    assert not [c for c in crits if c.label.startswith("format fits")]


# --- the MCP tool ---------------------------------------------------------


def test_the_tool_files_a_proposal_from_text_the_caller_read(venue, monkeypatch):
    from dovetail import mcp_server
    from dovetail.matching import genre

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(genre, "build_client",
                        lambda *a, **kw: fake_client(_answer(ORIGINAL, BRIEF)))
    out = mcp_server.propose_article_types(venue, URL, page_text=PAGE)
    assert out["status"] == "pending" and out["confidence"] == "high"
    assert [t["name"] for t in out["article_types"]] == ["Original Article", "Brief Report"]
    with db.session_scope() as s:
        assert s.get(Proposal, out["proposal_id"]).source_url == URL
        assert s.query(ArticleType).count() == 0


def test_a_blocked_fetch_comes_back_as_its_code_before_anything_is_spent(venue, monkeypatch):
    from dovetail import mcp_server
    from dovetail.matching import genre

    def blocked(url):
        raise pages.Blocked("publisher.example answered 403 to an automated request")

    def no_client(*a, **kw):
        raise AssertionError("a model was reached for a page that was never read")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(pages, "fetch_text", blocked)
    monkeypatch.setattr(genre, "build_client", no_client)
    out = mcp_server.propose_article_types(venue, URL)
    assert out["error"] == "blocked_by_publisher"
    assert mcp_server.propose_article_types(venue, "  ")["error"].startswith("source_url is required")
