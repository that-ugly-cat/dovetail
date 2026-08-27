"""OpenAlex client, with budget accounting attached.

Every method that spends declares its cost and records it **before** the call.
The 429 is still handled, because the local counter knows nothing about calls
made by other processes from the same IP.

A distinction that matters, and that cost a wrong diagnosis in v0.1 of the spec:
**500 is a broken endpoint, 429 is an empty till.** On 27 Aug 2026
`/text/keywords` answered 500 while `/text/topics` and `/text/concepts` answered
200 in the same sequence.
"""

from __future__ import annotations

import httpx
from sqlalchemy.orm import Session

from .. import config
from ..db import mark_exhausted, spend


class OpenAlexError(RuntimeError):
    pass


class EndpointBroken(OpenAlexError):
    """5xx: the endpoint does not work. Different from an exhausted budget."""


class RemoteBudgetExhausted(OpenAlexError):
    """429: the server-side budget is gone, whatever the local counter says."""


class InsufficientProfile(OpenAlexError):
    """OpenAlex does not know enough about this journal to build a profile.

    A third, explicit exit is needed because otherwise "no data" and "out of
    scope" are the same number: zero. SPEC.md §14.4.
    """


class OpenAlexClient:
    def __init__(self, client: httpx.Client | None = None):
        self._client = client or httpx.Client(timeout=30.0)

    # -- plumbing ----------------------------------------------------------

    def _params(self, extra: dict) -> dict:
        return {"mailto": config.openalex_mailto(), **extra}

    def _headers(self) -> dict:
        """The key goes in the header, not in the query string.

        OpenAlex accepts both — verified 27 Aug 2026, `?api_key=` and
        `Authorization: Bearer` each raise `X-RateLimit-Limit-USD` to 1 — but a
        secret in a URL ends up in server logs, proxies and Referer headers. A
        header does not.
        """
        key = config.openalex_api_key()
        return {"Authorization": f"Bearer {key}"} if key else {}

    def _get(self, path: str, params: dict) -> dict:
        r = self._client.get(
            f"{config.OPENALEX_BASE}{path}",
            params=self._params(params),
            headers=self._headers(),
        )
        if r.status_code == 429:
            raise RemoteBudgetExhausted(
                f"OpenAlex refused on budget (429) at {path}. "
                f"Retry-After: {r.headers.get('Retry-After', '?')}s."
            )
        if r.status_code >= 500:
            raise EndpointBroken(f"OpenAlex {r.status_code} at {path}: broken endpoint.")
        if r.status_code >= 400:
            raise OpenAlexError(f"OpenAlex {r.status_code} at {path}: {r.text[:200]}")
        return r.json()

    def _post(self, session: Session, path: str, body: dict) -> dict:
        """POST, for the calls whose payload does not fit in a URL."""
        try:
            r = self._client.post(
                f"{config.OPENALEX_BASE}{path}",
                params=self._params({}),
                headers=self._headers(),
                json=body,
            )
            if r.status_code == 429:
                raise RemoteBudgetExhausted(
                    f"OpenAlex refused on budget (429) at {path}. "
                    f"Retry-After: {r.headers.get('Retry-After', '?')}s."
                )
            if r.status_code >= 500:
                raise EndpointBroken(f"OpenAlex {r.status_code} at {path}: broken endpoint.")
            if r.status_code >= 400:
                raise OpenAlexError(f"OpenAlex {r.status_code} at {path}: {r.text[:200]}")
            return r.json()
        except RemoteBudgetExhausted:
            mark_exhausted(session)
            raise

    def _call(self, session: Session, path: str, params: dict) -> dict:
        """Every call goes through here: if the server answers 429 the local
        counter is aligned before re-raising, otherwise it would keep promising
        credits that do not exist."""
        try:
            return self._get(path, params)
        except RemoteBudgetExhausted:
            mark_exhausted(session)
            raise

    # -- calls -------------------------------------------------------------

    def classify_text(self, session: Session, title: str, abstract: str) -> dict:
        """Stage 1. **A hundred credits**, i.e. a hundred /sources calls.

        Sent as POST: as a GET the query string carried the whole abstract and a
        long one blew past the 8KB URL limit, which is a second failure mode on
        top of the 2000-character one. The payload also reports how much text was
        dropped, under a key of ours, so the run can record it.
        """
        title, abstract, dropped = fit_text(title, abstract)
        spend(session, config.COST_TEXT)
        payload = self._post(session, "/text/topics", {"title": title, "abstract": abstract})
        payload["_dovetail_chars_dropped"] = dropped
        return payload

    def source_by_issn(self, session: Session, issn: str) -> dict:
        spend(session, config.COST_SOURCES)
        return self._call(session, f"/sources/issn:{issn}", {})

    def search_sources(self, session: Session, query: str, per_page: int = 5) -> dict:
        """Lexical search over journal names. Used to resolve PaperTrail's free
        strings, and like any lexical search **a miss means "not with these
        words"**, never "does not exist"."""
        spend(session, config.COST_SOURCES)
        return self._call(
            session, "/sources", {"search": query, "filter": "type:journal", "per-page": per_page}
        )

    def work_by_doi(self, session: Session, doi: str) -> dict:
        """One published work, to validate against its real venue."""
        spend(session, config.COST_SOURCES)
        return self._call(session, f"/works/doi:{doi}", {})

    def journals_publishing_on(
        self, session: Session, topic_ids: list[str], per_page: int = 200
    ) -> list[dict]:
        """Journals that have actually published on these topics, by volume.

        A second, independent way of reaching a candidate, and it exists because
        the first one cannot reach everything. `/sources?filter=topics.id:` only
        matches a journal whose **top 25** topics include yours — that list is
        truncated — so a journal that publishes on your subject as a sideline is
        invisible to it however many pages you fetch. Measured: the
        International Journal of Public Health has 76 works on one paper's three
        topics and the sources filter does not return it.

        Grouping works by their source finds those journals, and orders them by
        relevance to the topic rather than by sheer size, which is the better
        ordering of the two.

        It is not a cure. Group results cap at 200 and the giants take the top,
        so a marginal publisher on a broad topic stays out either way — which
        may well be the right answer, since a journal with 76 works on a subject
        is not a topically obvious home for it.
        """
        if not topic_ids:
            return []
        spend(session, config.COST_WORKS)
        payload = self._call(
            session,
            "/works",
            {
                "filter": f"topics.id:{'|'.join(topic_ids)}",
                "group_by": "primary_location.source.id",
                "per-page": per_page,
            },
        )
        return [
            {"openalex_id": short_id(g.get("key", "")), "works_on_topic": g.get("count", 0)}
            for g in (payload.get("group_by") or [])
            if g.get("key") and g.get("key") != "unknown"
        ]

    def sources_by_ids(self, session: Session, ids: list[str]) -> list[dict]:
        """Fetch full records for a batch of OpenAlex source ids.

        Two hundred per call for one credit, which is what makes the
        works-grouping mechanism affordable: the group gives ids and counts, and
        the profiles have to come from somewhere.
        """
        out: list[dict] = []
        for start in range(0, len(ids), 100):
            batch = [i for i in ids[start : start + 100] if i]
            if not batch:
                continue
            spend(session, config.COST_SOURCES)
            payload = self._call(
                session,
                "/sources",
                {"filter": f"ids.openalex:{'|'.join(batch)}", "per-page": 200},
            )
            out.extend(payload.get("results") or [])
        return out

    def sources_by_topics(
        self,
        session: Session,
        topic_ids: list[str],
        max_pages: int = config.MAX_CANDIDATE_PAGES,
    ) -> dict:
        """Stage 2. Topics go in one filter joined by `|`, which is an OR:
        v0.1 of the spec planned one call per topic, i.e. three times the cost
        for the same result.

        **Paginated, and that is not an optimisation.** Taking a single page of
        200 meant taking 200 of 4228 candidates — 4.7% — and OpenAlex orders
        `/sources` by `works_count` descending, so the slice was the largest
        journals and nothing else. Every specialist venue was excluded by
        construction. It showed up as five of seven published papers whose real
        journal never even entered the candidate pool.

        Paging costs 1 credit per page, so covering the whole pool costs about
        as much as a rounding error. The cost was never the reason not to.

        No constraints here, on purpose. If the hard filters lived in this query,
        an excluded venue would never enter the list and could not be flagged
        "needs check" — which is the non-negotiable rule of SPEC.md §6.
        """
        if not topic_ids:
            return {"meta": {"count": 0}, "results": [], "truncated": False}

        results: list[dict] = []
        cursor = "*"
        total = 0
        pages = 0

        while cursor and pages < max_pages:
            spend(session, config.COST_SOURCES)
            payload = self._call(
                session,
                "/sources",
                {
                    "filter": f"type:journal,topics.id:{'|'.join(topic_ids)}",
                    "per-page": 200,
                    "cursor": cursor,
                },
            )
            batch = payload.get("results") or []
            results.extend(batch)
            total = (payload.get("meta") or {}).get("count", total)
            cursor = (payload.get("meta") or {}).get("next_cursor")
            pages += 1
            if not batch:
                break

        return {
            "meta": {"count": total},
            "results": results,
            # Said out loud rather than left implicit: a bounded sweep that does
            # not admit it was bounded reads as "we looked at everything".
            "truncated": bool(cursor) and len(results) < total,
            "pages": pages,
        }


# --- derivations ----------------------------------------------------------


def short_id(url_or_id: str) -> str:
    return (url_or_id or "").replace("https://openalex.org/", "")


def derive_oa_model(src: dict) -> str:
    """Four values. SPEC.md §8.

    The `closed_or_unknown` branch is not called `closed` on purpose: with
    `apc_usd` null on 92.7% of the corpus, the absence of an APC does not prove
    a journal is closed. Calling it `closed` would pass off a gap as a fact.
    """
    if src.get("is_in_doaj"):
        return "full_oa"
    if src.get("is_oa"):
        return "oa_outside_doaj"
    if src.get("apc_usd"):
        return "hybrid"
    return "closed_or_unknown"


def topics_coverage(src: dict) -> float | None:
    """How much of the journal's output the 25 returned topics cover.

    `count` is multi-label, so the value can exceed 1 (BMC Medical Ethics: 1.74).
    Read it as an indicator, not as a fraction.
    """
    works = src.get("works_count") or 0
    if not works:
        return None
    total = sum(t.get("count", 0) for t in (src.get("topics") or []))
    return total / works


def normalize_source(src: dict) -> dict:
    """From OpenAlex JSON to `Venue` fields. Note: `publisher` **does not exist**
    on /sources — there is only `host_organization_name`."""
    stats = src.get("summary_stats") or {}
    return {
        "openalex_id": short_id(src.get("id", "")),
        "issn_l": src.get("issn_l"),
        "issns": src.get("issn"),
        "display_name": src.get("display_name") or "(unnamed)",
        "host_organization_name": src.get("host_organization_name"),
        "homepage_url": src.get("homepage_url"),
        "country_code": src.get("country_code"),
        "venue_type": src.get("type"),
        "is_oa": src.get("is_oa"),
        "is_in_doaj": src.get("is_in_doaj"),
        "apc_usd": src.get("apc_usd"),
        "apc_prices": src.get("apc_prices"),
        "oa_flip_year": src.get("oa_flip_year"),
        "oa_model": derive_oa_model(src),
        "is_core": src.get("is_core"),
        "works_count": src.get("works_count"),
        "h_index": stats.get("h_index"),
        "two_yr_mean_citedness": stats.get("2yr_mean_citedness"),
        "topics": [
            {
                "id": short_id(t.get("id", "")),
                "display_name": t.get("display_name"),
                "count": t.get("count", 0),
                "subfield": (t.get("subfield") or {}).get("display_name"),
                "field": (t.get("field") or {}).get("display_name"),
            }
            for t in (src.get("topics") or [])
        ],
        "topics_coverage": topics_coverage(src),
    }


def reconstruct_abstract(inverted_index: dict | None) -> str:
    """OpenAlex stores abstracts as an inverted index — word to positions —
    because storing running text would be redistributing the publisher's
    copyrighted abstract. Rebuilding it locally is the documented way to read it.
    """
    if not inverted_index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, spots in inverted_index.items():
        for spot in spots:
            positions.append((spot, word))
    positions.sort()
    return " ".join(word for _, word in positions)


def fit_text(title: str, abstract: str) -> tuple[str, str, int]:
    """Cut title and abstract down to what `/text/*` accepts.

    OpenAlex rejects anything over 2000 characters combined, and it is a hard
    limit on both GET and POST. A real abstract can exceed it: this was found by
    a validation run failing on four papers out of seven, not by reading the
    documentation.

    The abstract is cut at a word boundary, never the title, and the number of
    characters dropped is returned so the caller can record that the
    classification was made on a shortened text. 2000 characters is around 300
    words, comfortably above the guard rail, so a cut here is far less dangerous
    than the short-abstract case — but it is still not the text the author wrote.
    """
    title = (title or "").strip()
    abstract = (abstract or "").strip()
    room = config.MAX_TEXT_CHARS - len(title) - 1
    if len(abstract) <= room:
        return title, abstract, 0

    dropped = len(abstract) - max(room, 0)
    if room <= 0:
        # A title alone over the limit is pathological; cut it and say so.
        return title[: config.MAX_TEXT_CHARS], "", len(abstract)
    cut = abstract[:room]
    space = cut.rfind(" ")
    if space > room * 0.8:
        cut = cut[:space]
        dropped = len(abstract) - len(cut)
    return title, cut, dropped
