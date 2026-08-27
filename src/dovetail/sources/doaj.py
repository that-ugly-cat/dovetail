"""DOAJ client. Free, no key, and it covers **fully open access journals only** —
which under the SNSF constraint is exactly the set that matters.

Careful with `publication_time_weeks`: it is the self-declared time **to
publication**, not the latency of the editorial decision. BMC Medical Ethics
declares 25 weeks, but the desk rejects that motivated this tool came back in
one, five and six days. Two different quantities, and the tool must not merge
them. SPEC.md §8.
"""

from __future__ import annotations

import httpx

from .. import config


class DoajError(RuntimeError):
    pass


class DoajClient:
    def __init__(self, client: httpx.Client | None = None):
        self._client = client or httpx.Client(timeout=30.0)

    def journal_by_issn(self, issn: str) -> dict | None:
        r = self._client.get(f"{config.DOAJ_BASE}/search/journals/issn%3A{issn}")
        if r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise DoajError(f"DOAJ {r.status_code} for ISSN {issn}: {r.text[:200]}")
        payload = r.json()
        results = payload.get("results") or []
        return results[0] if results else None


def normalize_journal(record: dict) -> dict:
    """From a DOAJ record to `Venue` fields.

    DOAJ's APC and OpenAlex's **disagree** (BMC Medical Ethics, same day:
    OpenAlex 2290 USD, DOAJ 2390 USD). The rule in §8 is that DOAJ wins when
    present, because it is self-declared by the publisher and kept current
    through delisting; here the raw record is kept and reconciliation happens
    downstream.
    """
    b = record.get("bibjson") or {}
    editorial = b.get("editorial") or {}
    return {
        "licenses": b.get("license"),
        "review_process": editorial.get("review_process"),
        "publication_time_weeks": b.get("publication_time_weeks"),
        "has_waiver": (b.get("waiver") or {}).get("has_waiver"),
        "doaj_apc": b.get("apc"),
    }


def reconcile_apc(openalex_apc: int | None, doaj_apc: dict | None) -> dict:
    """Return the value to use and, when the sources differ by more than 10%,
    both — because on either side of a threshold that difference decides the
    shortlist, and hiding it behind a single number would be a choice made on
    the reader's behalf."""
    doaj_usd = None
    for price in (doaj_apc or {}).get("max") or []:
        if price.get("currency") == "USD":
            doaj_usd = price.get("price")
            break

    if doaj_usd is None:
        return {"usd": openalex_apc, "source": "openalex", "disagreement": None}
    if openalex_apc is None:
        return {"usd": doaj_usd, "source": "doaj", "disagreement": None}

    gap = abs(doaj_usd - openalex_apc) / max(doaj_usd, openalex_apc)
    return {
        "usd": doaj_usd,
        "source": "doaj",
        "disagreement": (
            {"openalex": openalex_apc, "doaj": doaj_usd, "gap": round(gap, 3)}
            if gap > 0.10
            else None
        ),
    }
