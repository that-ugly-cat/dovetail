"""Stage 5a — does this journal publish things made *like this one*?

The question stages 3 and 4 cannot ask. Scope says what a paper is **about**;
genre says what **shape** it is. An empirical study and a conceptual essay on the
same subject score identically on the cosine and belong in different journals —
and getting that wrong is what two desk rejects in 2026 were, one of them
returned with the words «out of scope» attached to a paper whose scope was fine.

Three rules hold this module in place.

**It runs on the finalists only.** Twelve journals, not the 4,250 the sweep
scored. It is the expensive call and the last one, so it reads a list that
constraints and scope have already cut down.

**It never reorders.** The verdict lands beside the scores and a positive one
becomes a criterion of merit; nothing moves in the list. The judgement is not
reproducible — a second run may word it differently — and a list ordered on
something unreproducible cannot be explained, which is what `explain_match`
promises to do.

**It is told what it is not being asked.** The prompt says explicitly that
subject overlap has already been measured and is not the question, because a
model handed a manuscript and a journal will otherwise answer the easier
question about topic and sound confident doing it.
"""

from __future__ import annotations

from dataclasses import dataclass

MODEL = "claude-opus-5"

# Constrained rather than parsed: the shape is small and fixed, so `strict`
# removes a whole class of failure from the caller.
VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "publishes_this_kind": {
            "type": "boolean",
            "description": (
                "True if this journal prints work of the same KIND as the "
                "manuscript — the same form, method and register — regardless of "
                "whether the subject matches."
            ),
        },
        "confidence": {
            "type": "string",
            "enum": ["low", "medium", "high"],
            "description": (
                "low when the recent index is too short or too mixed to tell. "
                "Say low rather than guessing: the caller shows it."
            ),
        },
        "manuscript_kind": {
            "type": "string",
            "description": "The manuscript's form, in a few words.",
        },
        "journal_kind": {
            "type": "string",
            "description": "What the journal's recent index is made of, in a few words.",
        },
        "sentence": {
            "type": "string",
            "description": (
                "One sentence a person can act on, naming what in the recent "
                "index supports the verdict. No hedging, no restating the inputs."
            ),
        },
    },
    "required": [
        "publishes_this_kind",
        "confidence",
        "manuscript_kind",
        "journal_kind",
        "sentence",
    ],
    "additionalProperties": False,
}

SYSTEM = """\
You judge whether an academic journal publishes work of the same KIND as a given \
manuscript.

KIND means form, method and register: an empirical study, a conceptual or \
normative essay, a systematic review, a case report, a position piece, a \
methods paper, a commentary. Two texts on the same subject can be different \
kinds, and that is the distinction you are here for.

You are NOT being asked whether the subject matches. Subject overlap has already \
been measured, by cosine, at three levels, and passing that measurement is why \
this journal reached you. Answering the subject question instead is the failure \
mode of this task: it is easier, it sounds confident, and it is not what is \
missing.

Judge from the recent index you are shown and nothing else. If those titles are \
too few, or too mixed, to support a judgement, say so with confidence "low" \
rather than inferring from the journal's name — a name is not evidence about \
form.

Be willing to answer false. A journal that publishes only empirical work is a \
bad home for a conceptual essay however well the subject fits, and saying so is \
the whole value here."""


@dataclass
class Verdict:
    venue_id: int
    fits: bool
    confidence: str
    manuscript_kind: str
    journal_kind: str
    sentence: str
    model: str

    def to_json(self) -> dict:
        return {
            "fits": self.fits,
            "confidence": self.confidence,
            "manuscript_kind": self.manuscript_kind,
            "journal_kind": self.journal_kind,
            "sentence": self.sentence,
            "model": self.model,
        }


class GenreUnavailable(RuntimeError):
    """Raised before spending: no key, or nothing to read the journal against."""


def _manuscript_block(title: str, abstract: str, word_count: int | None) -> str:
    words = f"\nLength: about {word_count:,} words." if word_count else ""
    return f"Title: {title}\n\nAbstract: {abstract}{words}"


def _index_block(venue_name: str, titles: list[dict]) -> str:
    lines = []
    for t in titles:
        year = f" ({t['year']})" if t.get("year") else ""
        kind = f" [{t['type']}]" if t.get("type") else ""
        lines.append(f"- {t['title']}{year}{kind}")
    return f"Journal: {venue_name}\n\nIts most recent articles:\n" + "\n".join(lines)


def judge(
    client,
    title: str,
    abstract: str,
    word_count: int | None,
    venue_id: int,
    venue_name: str,
    recent: list[dict],
) -> Verdict:
    """One judgement, one call.

    The manuscript goes in the **system** prompt and the journal in the user
    message, which is not a style choice: rendering order is tools, system,
    messages, so the manuscript is a stable prefix across all twelve calls and
    caches, while the part that changes sits after the breakpoint. Twelve
    journals means eleven cache hits.
    """
    if not recent:
        raise GenreUnavailable(
            f"no recent index for {venue_name}: there is nothing to judge the "
            f"manuscript against, and a journal's name is not evidence about form"
        )

    response = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=[
            {"type": "text", "text": SYSTEM},
            {
                "type": "text",
                "text": "The manuscript:\n\n" + _manuscript_block(title, abstract, word_count),
                "cache_control": {"type": "ephemeral"},
            },
        ],
        output_config={
            "format": {
                "type": "json_schema",
                "schema": VERDICT_SCHEMA,
            },
            # The call is short and the question is narrow. Effort buys nothing
            # here that the prompt does not already fix.
            "effort": "medium",
        },
        messages=[{"role": "user", "content": _index_block(venue_name, recent)}],
    )

    payload = _parsed(response)
    return Verdict(
        venue_id=venue_id,
        fits=bool(payload["publishes_this_kind"]),
        confidence=payload["confidence"],
        manuscript_kind=payload["manuscript_kind"],
        journal_kind=payload["journal_kind"],
        sentence=payload["sentence"],
        model=MODEL,
    )


# --- orchestration --------------------------------------------------------

GENRE_LABEL = "publishes work of this kind"
GENRE_LABEL_NEGATIVE = "publishes work of a different kind"


def cost_estimate(session, results) -> dict:
    """What reading the finalists will cost, before the button. Free.

    Two currencies, and they are reported apart because they come out of
    different pockets: OpenAlex credits from the shared daily budget, and model
    tokens from the key of whoever presses it. Adding them would produce a
    number that means nothing.
    """
    from .. import config

    need_index = [r for r in results if _needs_index(r.venue, session)]
    n = len(results)
    return {
        "venues": n,
        "index_fetches": len(need_index),
        "openalex_credits": len(need_index) * config.COST_WORKS,
        "model": MODEL,
        # One call per journal. The manuscript is a cached prefix, so all but the
        # first read it at a tenth of the price. Rounded up, generously.
        "model_calls": n,
        "usd_estimate": round(n * 0.012, 2),
    }


def _needs_index(venue, session) -> bool:
    from ..provenance import is_stale

    return not venue.recent_titles or is_stale(session, venue, "recent_titles")


def read_finalists(session, client, openalex, run, results, on_progress=None):
    """Fetch what is missing, judge each finalist, write the verdicts.

    Returns (verdicts, failures). Nothing here raises for one journal's sake: a
    model that declines, or a journal with no index to read, costs that row its
    verdict and not the other eleven theirs.
    """
    from sqlalchemy import select

    from ..models import Criterion, CriterionKind
    from ..provenance import stamp

    verdicts, failures = [], []
    for res in results:
        venue = res.venue
        try:
            if _needs_index(venue, session):
                if not venue.openalex_id:
                    raise GenreUnavailable(
                        "not in OpenAlex, so there is no recent index to read. "
                        "A hand-declared journal can be scored but not yet judged."
                    )
                venue.recent_titles = openalex.recent_titles(session, venue.openalex_id)
                session.flush()
                stamp(session, venue, ["recent_titles"], "openalex")

            verdict = judge(
                client,
                run.title,
                run.abstract,
                run.word_count,
                venue.id,
                venue.display_name,
                venue.recent_titles or [],
            )
        except GenreUnavailable as e:
            failures.append({"venue": venue.display_name, "reason": str(e)})
            continue

        res.genre_verdict = verdict.to_json()

        # Replace rather than accumulate: reading the same run twice must not
        # leave two contradictory criteria side by side.
        for old in session.scalars(
            select(Criterion).where(
                Criterion.result_id == res.id,
                Criterion.label.in_([GENRE_LABEL, GENRE_LABEL_NEGATIVE]),
            )
        ):
            session.delete(old)

        # A positive verdict is a criterion of **merit** — it is the one this
        # column was short of. A negative one is not a logistical criterion and
        # not a constraint: it is a flag, and it lives in the verdict itself
        # rather than being dressed up as something the tool measured.
        if verdict.fits:
            session.add(
                Criterion(
                    result_id=res.id,
                    kind=CriterionKind.MERIT,
                    label=GENRE_LABEL,
                    weight=1.0,
                    evidence=f"{verdict.sentence} [{MODEL}, confidence {verdict.confidence}]",
                )
            )
        session.flush()
        verdicts.append(verdict)
        if on_progress:
            on_progress(verdict)

    return verdicts, failures


def _parsed(response) -> dict:
    """The structured answer, or a refusal reported as one.

    `stop_reason` is checked before `content` because a refusal comes back as a
    200 with no usable body, and reading content first turns that into an
    IndexError three frames away from the cause.
    """
    import json

    if getattr(response, "stop_reason", None) == "refusal":
        details = getattr(response, "stop_details", None)
        raise GenreUnavailable(
            f"the model declined this judgement ({getattr(details, 'category', 'no category')})"
        )
    for block in response.content:
        if block.type == "text":
            return json.loads(block.text)
    raise GenreUnavailable("the model returned no text block")
