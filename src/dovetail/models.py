"""Dovetail's schema. See SPEC.md §4.

Two choices that are not cosmetic, and that the rest of the code takes for
granted:

1. Freshness is tracked **per field**, not per record (`FieldVerification`). A
   journal can have yesterday's topics and an eight-month-old word limit, and
   those are different things. See SPEC.md §10.
2. `MatchRun` keeps the text profile **and** a snapshot of the venue profiles.
   Without it, going back after an outcome would compare yesterday's outcome
   against today's data, which is exactly what the table exists to avoid.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class OAModel(str, enum.Enum):
    """Four values, not three. v0.1 of the spec had three and left 41,521
    journals uncovered — the ones that are `is_oa` but outside DOAJ, which are
    also the predatory-risk quadrant. See SPEC.md §8."""

    FULL_OA = "full_oa"
    HYBRID = "hybrid"
    OA_OUTSIDE_DOAJ = "oa_outside_doaj"
    CLOSED_OR_UNKNOWN = "closed_or_unknown"


class SourceKind(str, enum.Enum):
    API = "api"
    GUIDELINES = "guidelines"


class ProposalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class CriterionKind(str, enum.Enum):
    MERIT = "merit"
    LOGISTICS = "logistics"


class Role(str, enum.Enum):
    """Two levels, and the split is by what they cost, not by seniority.

    A reader looks: venues, past consultations, the queue, the reasoning behind a
    list. Nothing a reader does spends OpenAlex credits or changes a fact.

    An admin runs consultations (which spend from a shared daily budget),
    declares venues by hand, and approves what is in the queue. Approving is the
    one that matters: it is the point where a suggestion becomes something the
    tool will repeat as true.
    """

    READER = "reader"
    ADMIN = "admin"


class Venue(Base):
    __tablename__ = "venue"

    id: Mapped[int] = mapped_column(primary_key=True)
    openalex_id: Mapped[str | None] = mapped_column(String(64), index=True)
    issn_l: Mapped[str | None] = mapped_column(String(16), unique=True, index=True)
    issns: Mapped[list | None] = mapped_column(JSON)
    display_name: Mapped[str] = mapped_column(String(512), index=True)

    host_organization_name: Mapped[str | None] = mapped_column(String(512))
    homepage_url: Mapped[str | None] = mapped_column(Text)
    country_code: Mapped[str | None] = mapped_column(String(8))
    venue_type: Mapped[str | None] = mapped_column(String(64))

    is_oa: Mapped[bool | None] = mapped_column(Boolean)
    is_in_doaj: Mapped[bool | None] = mapped_column(Boolean)
    apc_usd: Mapped[int | None] = mapped_column(Integer)
    apc_prices: Mapped[list | None] = mapped_column(JSON)
    oa_flip_year: Mapped[int | None] = mapped_column(Integer)
    oa_model: Mapped[OAModel | None] = mapped_column(Enum(OAModel))

    is_core: Mapped[bool | None] = mapped_column(Boolean)
    works_count: Mapped[int | None] = mapped_column(Integer)
    h_index: Mapped[int | None] = mapped_column(Integer)
    two_yr_mean_citedness: Mapped[float | None] = mapped_column(Float)

    # [{id, display_name, count, subfield, field}]. Truncated to 25 by OpenAlex:
    # on a broad generalist it covers a fraction of the output (SPEC.md §6).
    topics: Mapped[list | None] = mapped_column(JSON)
    topics_coverage: Mapped[float | None] = mapped_column(Float)

    # DOAJ, so present only on fully open access journals.
    licenses: Mapped[list | None] = mapped_column(JSON)
    review_process: Mapped[list | None] = mapped_column(JSON)
    publication_time_weeks: Mapped[int | None] = mapped_column(Integer)
    has_waiver: Mapped[bool | None] = mapped_column(Boolean)
    doaj_apc: Mapped[dict | None] = mapped_column(JSON)

    # Italian habilitation ranking, as `sector:band` (e.g. "11/C3:A"), comma
    # separated for more than one. A band with no sector means nothing.
    anvur_class: Mapped[str | None] = mapped_column(String(64))
    indexed_in: Mapped[list | None] = mapped_column(JSON)
    predatory_risk: Mapped[dict | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    verifications: Mapped[list[FieldVerification]] = relationship(
        back_populates="venue", cascade="all, delete-orphan"
    )
    aliases: Mapped[list[VenueAlias]] = relationship(
        back_populates="venue", cascade="all, delete-orphan"
    )
    article_types: Mapped[list[ArticleType]] = relationship(
        back_populates="venue", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - diagnostics
        return f"<Venue {self.issn_l} {self.display_name!r}>"


class FieldVerification(Base):
    """When **one field** was verified, and from where. SPEC.md §10."""

    __tablename__ = "field_verification"
    __table_args__ = (UniqueConstraint("venue_id", "field_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venue.id"), index=True)
    field_name: Mapped[str] = mapped_column(String(64))
    verified_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    source: Mapped[str] = mapped_column(String(32))
    source_url: Mapped[str | None] = mapped_column(Text)

    venue: Mapped[Venue] = relationship(back_populates="verifications")


class VenueAlias(Base):
    """PaperTrail stores venues as free strings, with typos and inconsistent
    casing (`Medicine health care and philosopy`). With nowhere to persist a
    confirmed resolution, every consultation would redo the same fuzzy match and
    get it wrong the same way. SPEC.md §4.

    A resolution **not confirmed by a human is not an alias**: it is a pending
    proposal.
    """

    __tablename__ = "venue_alias"
    __table_args__ = (UniqueConstraint("alias_string", "source_system"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    alias_string: Mapped[str] = mapped_column(String(512), index=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venue.id"), index=True)
    source_system: Mapped[str] = mapped_column(String(32))
    confirmed_by: Mapped[str | None] = mapped_column(String(128))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime)

    venue: Mapped[Venue] = relationship(back_populates="aliases")


class ArticleType(Base):
    """The field no API carries. It comes from author guidelines, one at a time,
    and in Phase 1 this table is nearly always empty."""

    __tablename__ = "article_type"

    id: Mapped[int] = mapped_column(primary_key=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venue.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    word_limit: Mapped[int | None] = mapped_column(Integer)
    # Whether the count includes or excludes abstract, references, captions.
    # This is the most common way to misread a set of author guidelines.
    word_limit_scope: Mapped[str | None] = mapped_column(Text)
    abstract_limit: Mapped[int | None] = mapped_column(Integer)
    refs_limit: Mapped[int | None] = mapped_column(Integer)
    figures_limit: Mapped[int | None] = mapped_column(Integer)
    unsolicited: Mapped[bool | None] = mapped_column(Boolean)
    source_url: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime)

    venue: Mapped[Venue] = relationship(back_populates="article_types")


class MatchRun(Base):
    __tablename__ = "match_run"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    abstract: Mapped[str] = mapped_column(Text)
    word_count: Mapped[int | None] = mapped_column(Integer)
    anatomy: Mapped[dict | None] = mapped_column(JSON)
    constraints: Mapped[dict | None] = mapped_column(JSON)

    # Persisted: redoing it costs 100 credits and can be blocked by the budget.
    text_profile: Mapped[dict | None] = mapped_column(JSON)
    scoring_config_version: Mapped[str | None] = mapped_column(String(32))
    refused_reason: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    results: Mapped[list[MatchResult]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class MatchResult(Base):
    __tablename__ = "match_result"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("match_run.id"), index=True)
    venue_id: Mapped[int] = mapped_column(ForeignKey("venue.id"), index=True)

    score_topic: Mapped[float] = mapped_column(Float, default=0.0)
    score_subfield: Mapped[float] = mapped_column(Float, default=0.0)
    score_field: Mapped[float] = mapped_column(Float, default=0.0)
    # Position in the list this run produced, not a rank: the score is
    # computed against this one paper and the set is whatever stage 2 reached
    # that day. See SPEC.md §0.
    position: Mapped[int | None] = mapped_column(Integer)

    # Venue profiles change at every refresh: without a snapshot, hindsight
    # would compare yesterday's outcome against today's data.
    venue_snapshot: Mapped[dict | None] = mapped_column(JSON)
    # Constraints that would have excluded it, and flags ("needs check").
    excluded_by: Mapped[list | None] = mapped_column(JSON)
    flags: Mapped[list | None] = mapped_column(JSON)

    run: Mapped[MatchRun] = relationship(back_populates="results")
    venue: Mapped[Venue] = relationship()
    criteria: Mapped[list[Criterion]] = relationship(
        back_populates="result", cascade="all, delete-orphan"
    )


class Criterion(Base):
    """The tool's original contribution: every venue declares the criteria that
    hold it up, split between merit and logistics. Fewer than two merit criteria
    and the venue shows in red. SPEC.md §9."""

    __tablename__ = "criterion"

    id: Mapped[int] = mapped_column(primary_key=True)
    result_id: Mapped[int] = mapped_column(ForeignKey("match_result.id"), index=True)
    kind: Mapped[CriterionKind] = mapped_column(Enum(CriterionKind))
    label: Mapped[str] = mapped_column(Text)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    evidence: Mapped[str | None] = mapped_column(Text)

    result: Mapped[MatchResult] = relationship(back_populates="criteria")


class Source(Base):
    __tablename__ = "source"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(256), unique=True)
    url: Mapped[str | None] = mapped_column(Text)
    hints: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[SourceKind] = mapped_column(Enum(SourceKind), default=SourceKind.API)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Proposal(Base):
    """Nothing writes to venues directly: it proposes, and a human approves in
    the UI. That holds when the proposer is a model too. SPEC.md §11."""

    __tablename__ = "proposal"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("source.id"))
    venue_id: Mapped[int | None] = mapped_column(ForeignKey("venue.id"))
    fields: Mapped[dict] = mapped_column(JSON)
    rationale: Mapped[str] = mapped_column(Text)
    confidence: Mapped[str] = mapped_column(String(16), default="medium")
    source_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ProposalStatus] = mapped_column(
        Enum(ProposalStatus), default=ProposalStatus.PENDING, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BudgetLedger(Base):
    """The OpenAlex budget is daily, resets at midnight UTC, and ran out while
    the spec was being written. Counting it here lets a call be refused *before*
    it is made, instead of coming back as a 429. SPEC.md §5."""

    __tablename__ = "budget_ledger"
    __table_args__ = (UniqueConstraint("day", "provider"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    day: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD UTC
    provider: Mapped[str] = mapped_column(String(32), default="openalex")
    credits_used: Mapped[int] = mapped_column(Integer, default=0)
    calls: Mapped[int] = mapped_column(Integer, default=0)


class User(Base):
    """A person who can open the web UI.

    `borant_sub` is the subject the gate vouches for, and it is the only key the
    gateway path looks users up by. Never email: a typo in someone else's admin
    panel must not be able to hand one person another person's account.

    Users created through the gate still get a local password — a random one
    nobody knows — because `AUTH_MODE=local` has to stay a working way back in,
    and a row with no password is not a way back.
    """

    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(256), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(256))
    hashed_password: Mapped[str] = mapped_column(String(256))
    borant_sub: Mapped[str | None] = mapped_column(String(128), unique=True, index=True)
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.READER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    def is_admin(self) -> bool:
        return self.role is Role.ADMIN
