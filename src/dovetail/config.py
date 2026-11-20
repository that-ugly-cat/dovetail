"""Configuration, and the numbers verified at source on 27 Aug 2026.

The costs are not estimates: they come from the `X-RateLimit-*` headers OpenAlex
returns on every call. See SPEC.md §5.
"""

from __future__ import annotations

import os
from pathlib import Path


def _load_env() -> None:
    """Read a `.env` next to the repo, with no dependencies.

    The file is gitignored and holds the OpenAlex key. Variables already present
    in the environment win: whoever exports by hand knows what they are doing.
    """
    path = Path(__file__).resolve().parents[2] / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_env()

# --- OpenAlex -------------------------------------------------------------

OPENALEX_BASE = "https://api.openalex.org"

# Credits per call, read from the response headers on 27 Aug 2026.
COST_SOURCES = 1  # /sources          X-RateLimit-Cost-USD 0.0001
COST_TEXT = 100  # /text/*           X-RateLimit-Cost-Required-USD 0.01
COST_WORKS = 10  # /works?search=    X-RateLimit-Cost-USD 0.001
# A /works call that only filters — by DOI, or by `cites:` with a group_by — is
# charged like /sources and not like a search. Read from
# `X-RateLimit-Credits-Used` on 20 Nov 2026, on both shapes the citation
# neighbourhood uses. The grouped call in `journals_publishing_on` was priced at
# COST_WORKS before this was measured and has not been re-measured since.
COST_WORKS_FILTER = 1

# Daily budget, in credits (1 credit = $0.0001).
BUDGET_ANONYMOUS = 1_000  # $0.10/day with no account: ten classifications
BUDGET_WITH_KEY = 10_000  # $1/day with a free account

# Below this, the matcher stops spending on optional refreshes and keeps the
# credits for classification, which is the call that cannot be skipped. Declared
# degradation instead of a 429 in the user's face.
CREDIT_RESERVE = COST_TEXT * 2


def openalex_mailto() -> str:
    return os.environ.get("DOVETAIL_MAILTO", "ono@borant.eu")


def openalex_api_key() -> str | None:
    """A free account key multiplies the budget by ten. It has to be created by
    hand: this tool does not register accounts."""
    return os.environ.get("OPENALEX_API_KEY") or None


def daily_budget() -> int:
    return BUDGET_WITH_KEY if openalex_api_key() else BUDGET_ANONYMOUS


# --- DOAJ -----------------------------------------------------------------

DOAJ_BASE = "https://doaj.org/api"


# --- Matcher --------------------------------------------------------------

# Hard API limit on /text/*: title and abstract together. Found by a real
# abstract failing, not by reading the docs — the validation case fitted by
# luck at roughly 1200 characters. A normal long abstract does not.
MAX_TEXT_CHARS = 2000

# Below this length classification is unstable: the same paper cut down to one
# sentence comes back with topics **disjoint** from those of the full abstract.
# Verified 27 Aug 2026. SPEC.md §6, stage 1.
MIN_ABSTRACT_WORDS = 60
MIN_PRIMARY_TOPIC_SCORE = 0.55

# How many pages of 200 candidates stage 2 sweeps. The real pool for one paper
# was 4228; a single page took 4.7% of it, ordered by works_count descending,
# so the slice was the biggest journals and every specialist was excluded by
# construction. A page costs 1 credit.
MAX_CANDIDATE_PAGES = 25

# Shortlist cut. Without it one consultation produces around 259 rows.
MAX_SHORTLIST = 12
MIN_SHORTLIST = 3

# Above this `works_count` the 25-topic profile covers a fraction of what the
# journal publishes and the score must be flagged unreliable (PLoS ONE: 19.7%).
GENERALIST_WORKS_THRESHOLD = 50_000

STALE_DAYS = 180

# --- Citation neighbourhood (SPEC.md §6b) ---------------------------------

# One grouped call per reference, so this is also the ceiling on what the
# second channel can spend. A long bibliography is cut, in the order given, and
# the report says how many were dropped.
MAX_REFERENCES = 150

# Below this many resolved references the neighbourhood is three papers'
# readership and not a conversation. It declines instead of answering.
MIN_REFERENCES = 5

# Citing works are counted from 1 January of (this year - N), and a journal's
# output is counted over the same window, so the density compares like with like.
NEIGHBOURHOOD_YEARS_BACK = 5

# A journal has to be reached through this share of the references — and never
# fewer than two — before its density is looked at. Without the gate one heavily
# cited reference puts its whole readership in the list.
NEIGHBOURHOOD_MIN_BREADTH_SHARE = 0.05

# A journal with a handful of recent works turns six citations into the highest
# density in the list. Below this it is not measured.
NEIGHBOURHOOD_MIN_RECENT_WORKS = 30

# Source records fetched per run to turn citation counts into densities, in
# batches of 100 for one credit each. Taken in order of breadth when cut.
NEIGHBOURHOOD_MAX_SOURCES = 1500

# The references are also read field by field. A field holding at least this
# many of them, and less than the share below, is a minority conversation: its
# own journals are listed, a few each, because in the overall count the majority
# field buries them. Measured 20 Nov 2026 on a paper with 73 of its 89 references
# in one field: the journals of the other fields were all there, between the
# twentieth and the hundredth place.
MIN_FIELD_REFERENCES = 4
MAJORITY_FIELD_SHARE = 0.6
MAX_CONVERSATION_PER_FIELD = 3

# How much the topic cosine weighs against the subfield cosine when ordering.
# Not validated: see the docstring of `Score.combined`.
TOPIC_WEIGHT = 2.0

SCORING_CONFIG_VERSION = "topics-cosine-v2-weight2"


# --- Languages ------------------------------------------------------------
#
# OpenAlex has no language on a journal. It has one on every work, so a
# journal's languages are read from its articles: one grouped /works call, one
# credit, kept on the journal afterwards. Measured 20 Nov 2026 on nine journals:
# a German journal comes back 100% German, a bilingual Italian one 68% Italian
# and 30% English, an English one 98% English.

# A journal "publishes in" a language when at least this share of its articles
# is in it. A fifth keeps the bilingual journals and drops the ones whose only
# English is the odd guest paper (15% on one Dutch journal measured).
LANGUAGE_MIN_SHARE = 0.20

# Shares below this are noise from language detection and are not stored.
LANGUAGE_KEEP_SHARE = 0.05

# The most a single consultation spends looking languages up. Journals already
# looked up cost nothing, so this is reached on the first consultations only.
LANGUAGE_LOOKUP_MAX = 60

LANGUAGE_NAMES = {
    "en": "English", "de": "German", "fr": "French", "es": "Spanish",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "ru": "Russian",
    "zh": "Chinese", "ja": "Japanese", "ko": "Korean", "pl": "Polish",
    "cs": "Czech", "tr": "Turkish", "sv": "Swedish", "da": "Danish",
    "no": "Norwegian", "fi": "Finnish", "hu": "Hungarian", "ro": "Romanian",
    "el": "Greek", "uk": "Ukrainian", "hr": "Croatian", "sk": "Slovak",
    "sl": "Slovenian", "id": "Indonesian", "ar": "Arabic", "fa": "Persian",
    "he": "Hebrew", "ca": "Catalan",
}


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code, code)


# --- Storage --------------------------------------------------------------


def db_path() -> Path:
    raw = os.environ.get("DOVETAIL_DB")
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[2] / "dovetail.db"
