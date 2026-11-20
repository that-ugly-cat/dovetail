"""Stage 5b — what a journal's own guidelines say about length and form.

The first consultation made for a real decision, on 8 Sep 2026, fell on this and
not on scope: the manuscript fitted none of one journal's seven formats, and the
tool had nothing to say about it because no API carries article types. The table
has to be filled from the author guidelines, one journal at a time.

Three rules, each of which is a failure this module was written after.

**A limit travels with what it counts.** *Bioethics* allows 8,000 words
including references and *Medical Humanities* 9,000 excluding them, and on a
paper with a long bibliography the two verdicts are opposite. A `word_limit`
without its `word_limit_scope` is a number that misleads, so one is never
written without the other: where the page does not say, the scope says that.

**Every number is checked against the page.** The model returns, with each
limit, the sentence it read it from. If that sentence is not on the page, or the
number is not in the sentence, the limit is dropped and reported. A model asked
for a word limit will produce one.

**It proposes.** What comes out is an entry in the approval queue, with the page
it came from. Nothing here writes to a journal's record.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..models import Proposal, ProposalStatus, Venue

# The mechanical extraction, as SPEC.md §5 assigns it: a small model reading one
# page into a fixed shape. The judgement call of this stage is not the model's —
# it is the check below.
MODEL = "claude-haiku-5-5"

_NULLABLE_INT = {"anyOf": [{"type": "integer"}, {"type": "null"}]}

SCHEMA = {
    "type": "object",
    "properties": {
        "article_types": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The article type as the journal names it.",
                    },
                    "word_limit": {
                        **_NULLABLE_INT,
                        "description": (
                            "Maximum words for the main text, as an integer. null "
                            "when the page gives no limit for this type. If the "
                            "page gives a range, the upper end."
                        ),
                    },
                    "word_limit_scope": {
                        "type": "string",
                        "description": (
                            "What the limit counts, in the page's own terms: whether "
                            "abstract, references, notes, tables and captions are "
                            "included or excluded. Exactly 'not stated' when the page "
                            "does not say. Empty when word_limit is null."
                        ),
                    },
                    "abstract_limit": {
                        **_NULLABLE_INT,
                        "description": "Maximum words for the abstract, or null.",
                    },
                    "refs_limit": {
                        **_NULLABLE_INT,
                        "description": "Maximum number of references, or null.",
                    },
                    "figures_limit": {
                        **_NULLABLE_INT,
                        "description": "Maximum figures and tables together, or null.",
                    },
                    "unsolicited": {
                        "anyOf": [{"type": "boolean"}, {"type": "null"}],
                        "description": (
                            "false if this type is by invitation or commissioned "
                            "only, true if the page says unsolicited submissions are "
                            "welcome, null if it does not say."
                        ),
                    },
                    "evidence": {
                        "type": "string",
                        "description": (
                            "The sentence or table row the limits were read from, "
                            "copied character for character from the page. It will "
                            "be searched for in the page."
                        ),
                    },
                },
                "required": [
                    "name", "word_limit", "word_limit_scope", "abstract_limit",
                    "refs_limit", "figures_limit", "unsolicited", "evidence",
                ],
                "additionalProperties": False,
            },
        },
        "page_is_guidelines": {
            "type": "boolean",
            "description": (
                "false if this page is not author guidelines at all — a cookie "
                "wall, a login page, a landing page that only links to them."
            ),
        },
    },
    "required": ["article_types", "page_is_guidelines"],
    "additionalProperties": False,
}

SYSTEM = """\
You read one page of a journal's author guidelines and list the article types it \
accepts, with the limits it states for each.

Report only what this page says. A limit you remember for this journal, or one \
that is usual for journals like it, is not on the page and must not be reported: \
leave the field null. Every limit you report will be checked against the page \
text, and one that cannot be found there is discarded.

For each limit, say what it counts. Guidelines differ on whether the word limit \
includes the abstract, the references, notes, tables and captions, and the same \
number means different things under different rules. Use the page's own terms. \
If the page gives a number and does not say what it counts, write "not stated" \
— do not infer it from what is common.

Copy the evidence exactly as it appears, including its punctuation and number \
formatting. Do not tidy it.

If the page lists no article types, return an empty list. If it is not a \
guidelines page, say so."""


@dataclass
class Extraction:
    types: list[dict] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    """What the model returned and the check removed, with the reason. Kept and
    shown: a limit that was claimed and not found is information about how far
    to trust the rest."""
    page_is_guidelines: bool = True
    model: str = MODEL


class ExtractionUnavailable(RuntimeError):
    code = "extraction_unavailable"


def _squash(text: str) -> str:
    """Whitespace and typographic quotes aside, is this on the page?"""
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"').replace("–", "-")
    return re.sub(r"\s+", " ", text).strip().lower()


def _number_in(n: int, text: str) -> bool:
    """8000 on the page as 8000, 8,000, 8 000 or 8.000 — and not inside 18000."""
    digits = str(n)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        pattern = rf"(?<!\d){head}[,.   ]?{tail}(?!\d)"
    else:
        pattern = rf"(?<!\d){digits}(?!\d)"
    return re.search(pattern, text) is not None


_LIMITS = ("word_limit", "abstract_limit", "refs_limit", "figures_limit")


def check(raw: list[dict], page_text: str) -> tuple[list[dict], list[dict]]:
    """Keep what the page supports. Return (kept, dropped).

    A type is kept even with every limit removed, as long as its evidence is on
    the page: that the journal has a «Brief Report» is itself worth knowing.
    """
    page = _squash(page_text)
    kept, dropped = [], []
    for item in raw:
        item = dict(item)
        name = (item.get("name") or "").strip()
        evidence = item.get("evidence") or ""
        quote = _squash(evidence)
        if not name:
            dropped.append({"name": "(unnamed)", "why": "no name"})
            continue
        if not quote or quote not in page:
            dropped.append({
                "name": name,
                "why": "the evidence quoted is not on the page",
                "claimed": {k: item.get(k) for k in _LIMITS if item.get(k) is not None},
            })
            continue
        for key in _LIMITS:
            value = item.get(key)
            if value is None:
                continue
            if not isinstance(value, int) or value <= 0 or not _number_in(value, evidence):
                dropped.append({
                    "name": name,
                    "why": f"{key} {value!r} is not in the evidence quoted for it",
                })
                item[key] = None
        # The rule the module exists for: never a limit without what it counts.
        if item.get("word_limit") is None:
            item["word_limit_scope"] = None
        elif not (item.get("word_limit_scope") or "").strip():
            item["word_limit_scope"] = "not stated"
        item["name"] = name
        kept.append(item)
    return kept, dropped


def extract(client, venue_name: str, page_text: str) -> Extraction:
    """One page, one call."""
    response = client.messages.create(
        model=MODEL,
        # Thinking is on by default on this model and is spent out of the same
        # allowance as the answer: a ceiling sized for the JSON alone truncates it.
        max_tokens=16000,
        system=SYSTEM,
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[
            {
                "role": "user",
                "content": f"Journal: {venue_name}\n\nThe page:\n\n{page_text}",
            }
        ],
    )
    stop = getattr(response, "stop_reason", None)
    if stop == "refusal":
        details = getattr(response, "stop_details", None)
        raise ExtractionUnavailable(
            f"the model declined to read this page ({getattr(details, 'category', 'no category')})"
        )
    if stop == "max_tokens":
        raise ExtractionUnavailable(
            "the answer was cut off at the token limit, so it is not valid JSON: "
            "pass a shorter section of the page"
        )
    payload = None
    for block in response.content:
        if block.type == "text":
            payload = json.loads(block.text)
            break
    if payload is None:
        raise ExtractionUnavailable("the model returned no text block")

    kept, dropped = check(payload.get("article_types") or [], page_text)
    return Extraction(
        types=kept,
        dropped=dropped,
        page_is_guidelines=bool(payload.get("page_is_guidelines", True)),
    )


# What an approved proposal may write onto an article type. `name` identifies
# the row and `evidence` is carried so the approver can see it.
ARTICLE_TYPE_FIELDS = (
    "word_limit", "word_limit_scope", "abstract_limit", "refs_limit",
    "figures_limit", "unsolicited",
)


def confidence_of(extraction: Extraction) -> str:
    if extraction.dropped:
        return "low"
    if any(t.get("word_limit_scope") == "not stated" for t in extraction.types):
        return "medium"
    return "high"


def propose(session, venue: Venue, source_url: str, extraction: Extraction) -> Proposal:
    """File what was read, for a person to approve."""
    lines = [
        f"Read from the author guidelines by {extraction.model}; every limit below "
        f"was found in the sentence quoted for it, on the page.",
    ]
    for d in extraction.dropped:
        lines.append(f"Dropped — {d['name']}: {d['why']}.")
    if any(t.get("word_limit_scope") == "not stated" for t in extraction.types):
        lines.append(
            "At least one word limit comes without what it counts: the page does "
            "not say. Check before relying on it."
        )
    p = Proposal(
        kind="update_venue",
        venue_id=venue.id,
        fields={"article_types": extraction.types},
        rationale="\n".join(lines),
        confidence=confidence_of(extraction),
        source_url=source_url,
        status=ProposalStatus.PENDING,
    )
    session.add(p)
    session.flush()
    return p
