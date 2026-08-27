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

# How much the topic cosine weighs against the subfield cosine when ordering.
# Not validated: see the docstring of `Score.combined`.
TOPIC_WEIGHT = 2.0

SCORING_CONFIG_VERSION = "topics-cosine-v2-weight2"


# --- Storage --------------------------------------------------------------


def db_path() -> Path:
    raw = os.environ.get("DOVETAIL_DB")
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[2] / "dovetail.db"
