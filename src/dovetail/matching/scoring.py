"""Stage 3: the scope score. Pure functions, no I/O.

Three levels — topic, subfield, field — **reported side by side and never merged
into one number**. The topic level is precise and sparse; subfield is the most
robust; field separates families. A venue with a high field score and a zero
topic score is a journal in the right discipline that does not publish that
particular thing, and an average would erase exactly that.

Cosine, not dot product: the text profile sums raw scores while the venue
profile sums to 1, so the dot product produced field scores above 1 that were
not comparable across levels.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field as dc_field

from .. import config

INSUFFICIENT_PROFILE = "insufficient profile"


@dataclass
class Profile:
    """Three sparse vectors, one per level."""

    topic: dict[str, float] = dc_field(default_factory=dict)
    subfield: dict[str, float] = dc_field(default_factory=dict)
    field: dict[str, float] = dc_field(default_factory=dict)

    def is_empty(self) -> bool:
        return not (self.topic or self.subfield or self.field)


def text_profile(topics: list[dict]) -> Profile:
    """From the result of `/text/topics`. The scores are already a confidence."""
    p = Profile()
    for t in topics:
        tid = (t.get("id") or "").replace("https://openalex.org/", "")
        score = float(t.get("score") or 0.0)
        sub = (t.get("subfield") or {}).get("display_name")
        fld = (t.get("field") or {}).get("display_name")
        if tid:
            p.topic[tid] = p.topic.get(tid, 0.0) + score
        if sub:
            p.subfield[sub] = p.subfield.get(sub, 0.0) + score
        if fld:
            p.field[fld] = p.field.get(fld, 0.0) + score
    return p


def venue_profile(topics: list[dict]) -> Profile:
    """Share of works per topic, not a declared score.

    `topics` is already normalized (see `openalex.normalize_source`) and
    **truncated to 25 by OpenAlex**: on a broad generalist this profile ignores
    most of what the journal publishes.
    """
    p = Profile()
    total = sum(t.get("count", 0) for t in topics) or 1
    for t in topics:
        weight = t.get("count", 0) / total
        tid = t.get("id")
        if tid:
            p.topic[tid] = p.topic.get(tid, 0.0) + weight
        if t.get("subfield"):
            p.subfield[t["subfield"]] = p.subfield.get(t["subfield"], 0.0) + weight
        if t.get("field"):
            p.field[t["field"]] = p.field.get(t["field"], 0.0) + weight
    return p


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0.0) for k, v in a.items())
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


@dataclass
class Score:
    topic: float
    subfield: float
    field: float
    stage2_reachable: bool
    """Whether the venue shares at least one topic with the text, i.e. whether
    stage 2 would have produced it at all. This is the signal the retrodictive
    validation actually rests on: on the validation case the two venues that turned
    it down are the only two that are unreachable."""

    reliable: bool = True
    notes: tuple[str, ...] = ()

    def combined(self) -> float:
        """`TOPIC_WEIGHT * topic + subfield`.

        **Not validated**, and chosen for a negative reason: ordering by subfield
        alone put *Journal of Cognitive Neuroscience* (topic 0.017) above
        *Philosophical Psychology* (topic 0.326) in the first live run. Subfield
        rewards how specialised a journal is, not how close it is to this text,
        and on its own it produces indefensible rankings.

        Which weight is right is for Phase 1b to say with numbers. Until it does,
        this is a declared default and not a measurement.
        """
        return config.TOPIC_WEIGHT * self.topic + self.subfield

    def sort_key(self) -> tuple:
        return (self.stage2_reachable, self.combined())


def score_venue(
    text: Profile,
    venue: Profile,
    works_count: int | None = None,
    topics_coverage: float | None = None,
) -> Score:
    notes: list[str] = []
    reliable = True

    if venue.is_empty():
        # Third explicit exit: "no data" must not look like "out of scope",
        # which is the same zero. SPEC.md §14.4.
        return Score(0.0, 0.0, 0.0, False, False, (INSUFFICIENT_PROFILE,))

    if works_count and works_count > config.GENERALIST_WORKS_THRESHOLD:
        reliable = False
        notes.append(
            f"broad generalist ({works_count} works): the 25 topics returned "
            f"cover a fraction of what it publishes, a zero may be false"
        )
    if topics_coverage is not None and topics_coverage < 0.5:
        reliable = False
        notes.append(f"profile coverage {topics_coverage:.1%}")

    return Score(
        topic=cosine(text.topic, venue.topic),
        subfield=cosine(text.subfield, venue.subfield),
        field=cosine(text.field, venue.field),
        stage2_reachable=bool(set(text.topic) & set(venue.topic)),
        reliable=reliable,
        notes=tuple(notes),
    )
