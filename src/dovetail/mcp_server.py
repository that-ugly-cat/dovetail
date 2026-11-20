"""The MCP surface. SPEC.md §11.

**It cannot approve anything.** The spec's rule is that approval lives in the UI,
and proposing is not approving: this server reads freely, runs consultations, and
files proposals into the queue, but nothing here can turn a proposal into a fact.
`approve-alias` stays on the CLI until the UI exists. That is not an oversight to
be tidied away later — it is the one guarantee that makes an agent safe to point
at this database.

Two things a caller has to know before using `match_venues`, and they are in its
description rather than buried here: it costs around 120 OpenAlex credits, and it
takes a couple of minutes because it sweeps the whole candidate pool. Both are
deliberate. The fast version of stage 2 sampled 4.7% of the candidates ordered by
size and excluded every specialist journal by construction (SPEC.md §16c), so
slow and complete beats quick and biased.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from mcp.server.mcpserver import MCPServer
from sqlalchemy import or_, select

from . import config, db, ownership
from .db import BudgetExhausted
from .matching import criteria as criteria_mod
from .matching.neighbourhood import extract_dois
from .matching.pipeline import Refusal, run_match_full
from .matching.scoring import score_venue, text_profile, venue_profile
from .models import (
    FINALIST_BUCKETS,
    ArticleType,
    Criterion,
    MatchResult,
    MatchRun,
    Proposal,
    ProposalStatus,
    Source,
    User,
    Venue,
)
from .sources.doaj import DoajClient
from .sources.openalex import OpenAlexClient, OpenAlexError, client_for, ledger_for

server = MCPServer(
    "dovetail",
    instructions=(
        "Where to send a paper. Given a manuscript it returns a list of candidate "
        "journals, each with the criteria that hold it up labelled merit or "
        "logistics.\n\n"
        "This is a list, not a ranking: the score is computed against the one "
        "paper you pass in, and the set is whatever the sweep reached, so a "
        "position carries no meaning outside its own run.\n\n"
        "Reads are free. Writes are proposals only — nothing here approves "
        "anything, by design."
    ),
)


# Who is calling, set by the HTTP gate before the request reaches a tool. None
# means stdio, which is a local process the operator started themselves.
caller: ContextVar[Any] = ContextVar("dovetail_mcp_caller", default=None)


def _needs_admin() -> dict | None:
    """Guard for the two things that cost something.

    `/mcp` sits among Caddy's public routes, outside the Borant ID gate, so this
    is the only place these calls are checked. Returning an error rather than
    raising is deliberate: a tool that throws hands the model a stack trace to
    invent around, while a sentence it can read lets it correct course.
    """
    who = caller.get()
    if who is None:
        return None  # stdio: a local process, started by whoever owns the box
    if not who.is_admin():
        return {
            "denied": (
                "this call writes into the approval queue, which everybody "
                "shares, and the key you used does not belong to an admin"
            )
        }
    return None


def _venue_brief(v: Venue) -> dict[str, Any]:
    brief = {
        "venue_id": v.id,
        "name": v.display_name,
        "issn_l": v.issn_l,
        # Not every row is a journal. The sweep drags in repositories because
        # Zenodo carries 12.2 million works and therefore touches every topic
        # there is, and a model reading a name like "PubMed" with no type beside
        # it has nothing to go on. They are never shortlisted; they are visible.
        "venue_type": v.venue_type,
        "publisher": v.host_organization_name,
        "oa_model": getattr(v.oa_model, "value", v.oa_model),
        "apc_usd": v.apc_usd,
        "predatory_risk": (v.predatory_risk or {}).get("level"),
    }
    if v.venue_type and v.venue_type != "journal":
        brief["not_a_journal"] = (
            f"a {v.venue_type}: papers are deposited there, not submitted to it "
            f"for review. Stage 4 excludes it."
        )
    return brief


def _row_out(r) -> dict[str, Any]:
    merit = [c for c in r.criteria if c.kind == criteria_mod.MERIT]
    logistics = [c for c in r.criteria if c.kind == criteria_mod.LOGISTICS]
    return {
        **_venue_brief(r.venue),
        "scope": {
            "topic": round(r.score.topic, 4),
            "subfield": round(r.score.subfield, 4),
            "field": round(r.score.field, 4),
            "reachable_at_stage_2": r.score.stage2_reachable,
            "reliable": r.score.reliable,
        },
        "merit": [c.label for c in merit],
        "logistics": [c.label for c in logistics],
        # The rule from §9, and the reason this tool exists at all.
        "red": criteria_mod.is_red(r.criteria),
        "constraints": [
            {"constraint": o.constraint, "outcome": o.outcome, "reason": o.reason}
            for o in r.outcomes
        ],
        "flags": list(r.score.notes) + r.predatory["flags"],
        # Present when the reference list leads here. On a shortlisted row it
        # means two independent roads reach this journal.
        "cited_by_your_literature": (
            r.neighbourhood.to_json() if r.neighbourhood is not None else None
        ),
    }


# --- reads ----------------------------------------------------------------


@server.tool()
def budget_status() -> dict:
    """How many OpenAlex credits are left today, and what they buy.

    Worth checking before `match_venues`, which is the only expensive call.
    """
    db.init_engine()
    with db.session_scope() as s:
        who = _caller_user(s)
        ledger = ledger_for(who)
        if ledger is None:
            return {
                "daily_budget": 0,
                "remaining": 0,
                "has_api_key": False,
                "note": (
                    "you have no OpenAlex key of your own, so there is nothing "
                    "to spend: everything here is paid for by the person who "
                    "starts it. Store a free key in the web app, under Settings"
                ),
            }
        spent, remaining = db.credits_spent(s, ledger), db.credits_remaining(s, ledger)
    return {
        "daily_budget": db.budget_of(ledger),
        "spent": spent,
        "remaining": remaining,
        "classifications_left": remaining // config.COST_TEXT,
        "spending_from": (
            "your own OpenAlex key" if who is not None
            else "the key in the server's environment (command line)"
        ),
        "has_api_key": who is not None or bool(config.openalex_api_key()),
        "note": "resets at midnight UTC",
    }


def _may_consult(s) -> dict | None:
    """Guard for what spends OpenAlex credits on the caller's own manuscript.

    An admin, or anyone whose MCP key belongs to an account with its own
    OpenAlex key. The proposals stay behind `_needs_admin`: they are about the
    journals everybody shares.
    """
    who = _caller_user(s)
    if ownership.may_consult(who):
        return None
    return {"error": "needs_your_own_openalex_key", "detail": ownership.WHY_NOT}


def _own_run(s, run_id: int):
    """The consultation, if it is the caller's to read. See `ownership.run_for`."""
    return ownership.run_for(s, run_id, _caller_user(s))


def _caller_user(s):
    """The caller as a row of this session, or None over stdio.

    `caller` holds a detached copy made by the HTTP layer; the key is read from
    a fresh row so that one stored a minute ago is the one that gets used.
    """
    who = caller.get()
    return s.get(User, who.id) if who is not None else None


def _callers_openalex(s) -> OpenAlexClient:
    return client_for(_caller_user(s))


@server.tool()
def search_venues(query: str = "", limit: int = 25) -> dict:
    """Journals already in the table, by name or ISSN.

    Lexical, not semantic: a miss means "not with these words", never "does not
    exist". The table holds whatever past sweeps and hand-declared venues have
    put there, so it is not a census of journals.
    """
    db.init_engine()
    with db.session_scope() as s:
        stmt = select(Venue)
        if query:
            like = f"%{query}%"
            stmt = stmt.where(or_(Venue.display_name.ilike(like), Venue.issn_l.ilike(like)))
        rows = list(s.scalars(stmt.limit(limit)))
        return {"count": len(rows), "venues": [_venue_brief(v) for v in rows]}


@server.tool()
def get_venue(venue_id: int) -> dict:
    """One journal in full, **with a verification date per field**.

    The dates are the point: a journal can carry topics from yesterday and a word
    limit from eight months ago, and those age differently. `source` says what
    kind of claim each value is — `openalex`, `doaj`, `manual`, or derived from a
    sample of texts.
    """
    db.init_engine()
    with db.session_scope() as s:
        v = s.get(Venue, venue_id)
        if v is None:
            return {"error": f"no venue {venue_id}"}
        return {
            **_venue_brief(v),
            "homepage": v.homepage_url,
            "works_count": v.works_count,
            "h_index": v.h_index,
            "is_core": v.is_core,
            "in_doaj": v.is_in_doaj,
            "licenses": v.licenses,
            "review_process": v.review_process,
            "publication_time_weeks": v.publication_time_weeks,
            "publication_time_note": (
                "self-declared time to publication, NOT the latency of the "
                "editorial decision — a desk reject arrives in days"
            ),
            "topics": [
                {"name": t["display_name"], "count": t["count"], "subfield": t["subfield"]}
                for t in (v.topics or [])[:10]
            ],
            "topics_note": "truncated to 25 by OpenAlex; on a broad journal that is a fraction",
            "predatory_risk": v.predatory_risk,
            "verified_at": {
                fv.field_name: {
                    "when": fv.verified_at.isoformat(),
                    "source": fv.source,
                    "url": fv.source_url,
                }
                for fv in v.verifications
            },
        }


@server.tool()
def list_article_types(venue_id: int) -> dict:
    """Article types and word limits for one journal.

    Usually empty: no API carries this, it comes from author guidelines one
    journal at a time. `word_limit_scope` says whether the count includes the
    abstract, the references and the captions, which is the most common way to
    misread a set of guidelines.
    """
    db.init_engine()
    with db.session_scope() as s:
        rows = list(s.scalars(select(ArticleType).where(ArticleType.venue_id == venue_id)))
        return {
            "count": len(rows),
            "article_types": [
                {
                    "name": a.name,
                    "word_limit": a.word_limit,
                    "word_limit_scope": a.word_limit_scope,
                    "unsolicited": a.unsolicited,
                    "source_url": a.source_url,
                    "verified_at": a.verified_at.isoformat() if a.verified_at else None,
                }
                for a in rows
            ],
            "note": "empty means nobody has read this journal's guidelines yet, not that it has no limits",
        }


@server.tool()
def list_runs(limit: int = 10) -> dict:
    """Past consultations, newest first, with the constraints each ran under.

    Use it to find a `run_id` for `explain_match`, and to see what was already
    asked before spending credits asking it again.

    **Read `status` before reading `results`.** A consultation started from the
    web answers before it has finished, so a row can exist with nothing in it
    yet: `running` means wait, `failed` means look at `error_code`, `refused`
    means the matcher declined to guess — usually an abstract too short to
    classify — and that reason is worth reading before retrying with the same
    text. Without this field a run still going and a run whose process died look
    identical, and they want opposite reactions.
    """
    db.init_engine()
    with db.session_scope() as s:
        runs = list(
            s.scalars(
                select(MatchRun).where(ownership.visible_to(_caller_user(s)))
                .order_by(MatchRun.id.desc()).limit(limit)
            )
        )
        return {
            "runs": [
                {
                    "run_id": r.id,
                    "title": r.title,
                    "word_count": r.word_count,
                    "constraints": {
                        k: v for k, v in (r.constraints or {}).items() if not k.startswith("_")
                    },
                    "created_at": r.created_at.isoformat(),
                    "status": r.status,
                    "error_code": r.error_code,
                    "error_detail": r.error_detail,
                    "refused": r.refused_reason,
                    "results": len(r.results),
                }
                for r in runs
            ]
        }


@server.tool()
def explain_match(run_id: int, venue_id: int) -> dict:
    """Why one journal came out where it did in one consultation.

    Reads the snapshot taken at the time, not today's record: venue profiles
    change at every refresh, and comparing yesterday's outcome against today's
    data is exactly what the snapshot exists to prevent.
    """
    db.init_engine()
    with db.session_scope() as s:
        if _own_run(s, run_id) is None:
            return {"error": f"no consultation {run_id}"}
        res = s.scalar(
            select(MatchResult).where(
                MatchResult.run_id == run_id, MatchResult.venue_id == venue_id
            )
        )
        if res is None:
            return _explain_unlisted(s, run_id, venue_id)
        crits = list(s.scalars(select(Criterion).where(Criterion.result_id == res.id)))
        venue = s.get(Venue, venue_id)
        return {
            "run_id": run_id,
            "venue": venue.display_name if venue else venue_id,
            "bucket": res.bucket,
            "bucket_note": {
                "shortlist": "scored, passed the constraints, inside the cut",
                "excluded": (
                    "a constraint removed it; it is here because fewer than three "
                    "venues passed and an empty list is not an answer"
                ),
                "unclassifiable": (
                    "no profile, so no score applies: its zero means «I don't know», "
                    "NOT «out of scope». Do not compare it with a scored venue"
                ),
                "neighbourhood": (
                    "reached through the reference list, not through the topics: "
                    "its recent papers cite the works this manuscript cites. "
                    "Ordered by citation density, which is a different axis from "
                    "the shortlist's"
                ),
                "conversation": (
                    "reached through the references of one minority field of the "
                    "bibliography, read on its own so the majority field does not "
                    "bury it. See `cited_by_your_literature.via_field`"
                ),
            }.get(res.bucket, res.bucket),
            "position": res.position,
            "position_note": (
                "position inside its own bucket. Not a rank even there: the score is "
                "computed against this one paper and the set is whatever the sweep "
                "reached. Positions from different buckets are not comparable"
            ),
            "scope": {
                "topic": round(res.score_topic, 4),
                "subfield": round(res.score_subfield, 4),
                "field": round(res.score_field, 4),
            },
            "merit": [c.label for c in crits if c.kind.value == "merit"],
            "logistics": [c.label for c in crits if c.kind.value == "logistics"],
            # Stage 5a, when it has run. Beside the scores and never merged into
            # them: the judgement is not reproducible, so it may inform a
            # reading and must never order one. `fits: false` is not a
            # constraint and did not remove anything.
            "genre_verdict": res.genre_verdict,
            "genre_note": (
                "whether the journal publishes work of this KIND — form and "
                "method, not subject. Not reproducible, so it orders nothing. "
                "Absent means stage 5a has not been run on this consultation."
            ),
            "evidence": {c.label: c.evidence for c in crits},
            "cited_by_your_literature": res.neighbourhood,
            "constraints": res.excluded_by,
            "flags": res.flags,
            "snapshot_works_count": (res.venue_snapshot or {}).get("works_count"),
        }


def _explain_unlisted(s, run_id: int, venue_id: int) -> dict:
    """A venue with no row in this run: say which of three things that means.

    Only the baskets are stored, so a journal that was scored and fell below the
    cut leaves no row — and this used to answer «was not in run», which reads as
    «never considered». It sent a diagnosis the wrong way on 18 Sep 2026: four
    journals were reported as never having entered a consultation they
    had in fact been scored in. Every venue in the table is scored in every run,
    so the score can be recomputed from what the run kept, at no cost.

    It is recomputed against **today's** record of the venue, not a snapshot —
    there is none for a row that was never written — and says so.
    """
    run = _own_run(s, run_id)
    venue = s.get(Venue, venue_id)
    if run is None:
        return {"error": f"no consultation {run_id}"}
    if venue is None:
        return {"error": f"no venue {venue_id}"}
    topics = (run.text_profile or {}).get("topics") or []
    if not topics:
        return {
            "run_id": run_id,
            "venue": venue.display_name,
            "listed": False,
            "why": "this consultation has no text profile, so nothing was scored in it",
        }
    if venue.created_at and run.created_at and venue.created_at > run.created_at:
        note = (
            "this venue entered the table after the consultation ran, so it was "
            "not among its candidates; the figures below are what it would score now"
        )
    else:
        note = (
            "scored in this consultation and not kept: only the baskets are "
            "stored. Recomputed from the run's text profile against today's "
            "record of the venue, which may have changed since"
        )
    score = score_venue(
        text_profile(topics),
        venue_profile(venue.topics or []),
        venue.works_count,
        venue.topics_coverage,
    )
    return {
        "run_id": run_id,
        "venue": venue.display_name,
        "listed": False,
        "why": note,
        "scope": {
            "topic": round(score.topic, 4),
            "subfield": round(score.subfield, 4),
            "field": round(score.field, 4),
            "reachable_at_stage_2": score.stage2_reachable,
            "reliable": score.reliable,
        },
        "reading": (
            "not reachable at stage 2 means the venue shares no topic with the "
            "text: every reachable venue is ordered before it, so it cannot enter "
            "the shortlist whatever its subfield score. That is a statement about "
            "OpenAlex's topics, not about the journal — a reference list gives "
            "the run a second road (`references`)."
            if not score.stage2_reachable
            else "reachable, and below the cut of this run's shortlist"
        ),
        "flags": list(score.notes),
    }


@server.tool()
def list_sources() -> dict:
    """Configured ingestion sources, with the hints that tell an agent what to
    look for at each."""
    db.init_engine()
    with db.session_scope() as s:
        rows = list(s.scalars(select(Source)))
        return {
            "sources": [
                {
                    "id": src.id,
                    "name": src.name,
                    "url": src.url,
                    "kind": getattr(src.kind, "value", src.kind),
                    "enabled": src.enabled,
                    "hints": src.hints,
                }
                for src in rows
            ]
        }


@server.tool()
def list_proposals(status: str = "pending", limit: int = 50) -> dict:
    """The approval queue.

    Nothing in this MCP can approve an entry. That is the guarantee that makes an
    agent safe to point at this database: it can suggest, and a person decides.
    """
    db.init_engine()
    with db.session_scope() as s:
        rows = list(
            s.scalars(
                select(Proposal).where(Proposal.status == ProposalStatus(status)).limit(limit)
            )
        )
        return {
            "count": len(rows),
            "proposals": [
                {
                    "id": p.id,
                    "kind": p.kind,
                    "venue_id": p.venue_id,
                    "fields": p.fields,
                    "rationale": p.rationale,
                    "confidence": p.confidence,
                    "source_url": p.source_url,
                }
                for p in rows
            ],
            "note": "approval happens outside this surface, by a person",
        }


# --- stage 5a: the one that spends the caller's own money -----------------


def _callers_key(s):
    """The caller's own Anthropic credential, or a sentence saying why not.

    Returns `(client, None)` or `(None, error_dict)`.

    **Bound to the person, not to the machine.** The MCP key carries an
    identity, and this call spends against the credential of whoever owns that
    identity — a judgement started from a chat bills its own account and nobody
    else's. This box holds no Anthropic key of its own, which is the same rule
    that keeps `venue_history` open and is settled the other way here because
    there is a per-user place to put one.

    Over stdio there is no caller — a local process started by whoever owns the
    box — so `ANTHROPIC_API_KEY` from the environment is honoured there and
    nowhere else.
    """
    import os

    from . import crypto
    from .matching import genre

    who = caller.get()
    if who is None:
        raw = os.environ.get("ANTHROPIC_API_KEY")
        if not raw:
            return None, {
                "denied": (
                    "no Anthropic key: over stdio this reads ANTHROPIC_API_KEY, "
                    "and it is not set"
                )
            }
        return genre.build_client(raw, os.environ.get("ANTHROPIC_WORKSPACE_ID")), None

    user = s.get(User, who.id)
    if user is None or not user.anthropic_key_encrypted:
        return None, {
            "denied": (
                "you have no Anthropic key stored. This call is charged to the "
                "person who makes it, so it needs your own: store one at "
                "/app/settings."
            )
        }
    if not crypto.available():
        return None, {
            "denied": (
                "FERNET_KEY is not set on this instance, so stored keys cannot "
                "be read. That is an operator's fix, not yours."
            )
        }
    return (
        genre.build_client(
            crypto.decrypt_api_key(user.anthropic_key_encrypted),
            user.anthropic_workspace_id,
        ),
        None,
    )


@server.tool()
def estimate_genre_judgement(run_id: int) -> dict:
    """What judging a run's finalists would cost. **Free, and makes no calls.**

    Next to the thing that spends there has to be a thing that estimates, and it
    has to cost nothing: a cost seen afterwards is not a decision.

    Two currencies, reported apart because they come out of different pockets —
    model calls against your Anthropic key, OpenAlex credits against your
    OpenAlex key. Adding them would produce a number that means nothing.
    """
    from .matching import genre

    db.init_engine()
    with db.session_scope() as s:
        run = _own_run(s, run_id)
        if run is None:
            return {"error": f"no consultation {run_id}"}
        results = list(
            s.scalars(
                select(MatchResult).where(
                    MatchResult.run_id == run_id,
                    MatchResult.bucket.in_(FINALIST_BUCKETS),
                )
            )
        )
        if not results:
            return {"error": f"consultation {run_id} has no shortlist to judge"}
        est = genre.cost_estimate(s, results)
        already = sum(1 for r in results if r.genre_verdict)
        return {
            **est,
            "already_judged": already,
            "note": (
                "the model calls are charged to the key of whoever makes the "
                "call, and so are the OpenAlex credits, to their own OpenAlex key. "
                "Judging again replaces the previous verdicts rather than adding to them."
            ),
        }


@server.tool()
def judge_finalists(run_id: int) -> dict:
    """Stage 5a: does each shortlisted journal publish work of this **kind**?

    Form and method, not subject. Stages 3 and 4 already measured subject, and
    an empirical study and a conceptual essay on the same topic score the same
    there and belong in different journals.

    **It orders nothing.** A positive verdict becomes a criterion of merit and a
    negative one stays a flag; no position and no score moves. The judgement is
    not reproducible — ask twice and the sentence differs — and a list ordered on
    something unreproducible cannot be explained, which is what `explain_match`
    promises. Read the verdicts as informing a reading, never as ranking one.

    **It spends your own money.** The model calls are charged to the Anthropic
    key stored by whoever owns the MCP key being used.
    Call `estimate_genre_judgement` first: it is free.

    Fetching a journal's recent index, where it is missing, spends OpenAlex
    credits from the same person's OpenAlex key — which is why it needs one.

    A journal that cannot be judged costs that row its verdict and not the
    others theirs: a hand-declared venue has no OpenAlex index to read, and a
    model may decline. Both come back under `could_not_judge`.
    """
    from anthropic import APIError

    from .matching import genre

    db.init_engine()
    with db.session_scope() as s:
        denied = _may_consult(s)
        if denied:
            return denied
        run = _own_run(s, run_id)
        if run is None:
            return {"error": f"no consultation {run_id}"}
        results = list(
            s.scalars(
                select(MatchResult)
                .where(
                    MatchResult.run_id == run_id,
                    MatchResult.bucket.in_(FINALIST_BUCKETS),
                )
                .order_by(MatchResult.bucket, MatchResult.position)
            )
        )
        if not results:
            return {"error": f"consultation {run_id} has no shortlist to judge"}

        client, problem = _callers_key(s)
        if problem:
            return problem

        try:
            verdicts, failures = genre.read_finalists(
                s, client, _callers_openalex(s), run, results
            )
        except APIError as e:
            # Three different fixes, and they used to be one class name: a key
            # that needs a workspace is a form to fill in, a rejected key is a
            # credential to check, an empty balance is a top-up.
            code, detail = genre.classify_api_error(e)
            return {"error": code, "detail": detail, "spent": "nothing was left half-written"}
        except OpenAlexError as e:
            return {"error": "openalex", "detail": str(e)}

        return {
            "run_id": run_id,
            "judged": len(verdicts),
            "same_kind": sum(1 for v in verdicts if v.fits),
            "different_kind": sum(1 for v in verdicts if not v.fits),
            "model": genre.MODEL,
            "verdicts": [
                {
                    "venue_id": v.venue_id,
                    "fits": v.fits,
                    "confidence": v.confidence,
                    "manuscript_kind": v.manuscript_kind,
                    "journal_kind": v.journal_kind,
                    "sentence": v.sentence,
                }
                for v in verdicts
            ],
            "could_not_judge": failures,
            "note": (
                "nothing moved. A verdict sits beside the scores and never "
                "inside them; `fits: false` is a flag and not a constraint, and "
                "it removed nothing from the list."
            ),
        }


# --- the expensive one ----------------------------------------------------


@server.tool()
def match_venues(
    title: str,
    abstract: str,
    word_count: int | None = None,
    funder: str | None = None,
    max_apc: int | None = None,
    discover: bool = True,
    references: str = "",
) -> dict:
    """Candidate journals for a manuscript. **Slow and expensive: read this.**

    **Pass the reference list whenever you have it** (`references`: the pasted
    list in any format, or just the DOIs — only DOIs are read, up to 150). The
    topics classify a text by its *object*, so a normative or conceptual paper
    about a technology or a disease comes back with the journals that build the
    technology or treat the disease. With references the run also asks where the
    works the manuscript cites are themselves cited, and returns those journals
    in `neighbourhood` and `conversations`. One more credit per DOI.

    Costs roughly 120 OpenAlex credits (a classification is 100) and takes a
    couple of minutes with `discover=True`, because it sweeps the entire
    candidate pool — some thousands of journals. Check `budget_status` first.

    Passing `discover=False` uses only journals already in the table: seconds and
    almost free, but the answer is limited to what past sweeps happened to fetch.

    `funder="snsf"` excludes hybrid journals, which the SNSF does not pay APCs
    for. Note that `apc_usd` is missing on most journals, so that constraint
    flags far more than it excludes — deliberately: discarding a journal for
    lack of data would throw away the corpus rather than the expensive ones.

    What comes back is a **list, not a ranking**, and three lists at that:
    the shortlist; venues a constraint removed, shown when fewer than three
    survive; and venues that cannot be profiled at all, where a zero means "no
    data" and not "out of scope".
    """
    db.init_engine()
    with db.session_scope() as s:
        denied = _may_consult(s)
        if denied:
            return denied
        who = _caller_user(s)
        constraints = {k: v for k, v in {"funder": funder, "max_apc": max_apc}.items() if v}
        try:
            openalex = _callers_openalex(s)
            if discover and not openalex.keyed:
                return {
                    "error": "no_openalex_key",
                    "detail": (
                        "the sweep pages through OpenAlex with a cursor, which is "
                        "refused without a key, and neither you nor the instance "
                        "has one. Store a free key in the web app under Settings, "
                        "or pass discover=False."
                    ),
                }
            out = run_match_full(
                s,
                openalex,
                title,
                abstract,
                word_count,
                constraints,
                discover=discover,
                doaj=DoajClient(),
                references=extract_dois(references),
                owner_id=who.id if who is not None else None,
            )
        except Refusal as e:
            return {"refused": str(e)}
        except BudgetExhausted as e:
            return {"budget_exhausted": str(e)}
        except OpenAlexError as e:
            return {"error": str(e)}

        run, shortlist = out.run, out.shortlist
        excluded_shown, unclassifiable = out.excluded_shown, out.unclassifiable
        sweep = (run.constraints or {}).get("_sweep") or {}
        return {
            "run_id": run.id,
            "shortlist": [_row_out(r) for r in shortlist],
            # The second road. Its order is citation density, not the
            # shortlist's cosine: never merge the two lists into one.
            "neighbourhood": [_row_out(r) for r in out.neighbourhood],
            "conversations": [_row_out(r) for r in out.conversations],
            "neighbourhood_report": out.neighbourhood_report
            or {
                "skipped": "no_references",
                "note": (
                    "no reference list was passed, so only the topic channel ran. "
                    "For a conceptual or normative paper check whether the obvious "
                    "journals are in the shortlist before trusting it."
                ),
            },
            "excluded_shown": [_row_out(r) for r in excluded_shown],
            "unclassifiable": [
                {**_venue_brief(r.venue), "why": list(r.score.notes)} for r in unclassifiable
            ],
            "sweep": sweep,
            "caveats": [
                "a list, not a ranking: positions mean nothing outside this run",
                "the score measures how concentrated a journal is on your subject, "
                "not how suitable it is — those are different things",
                "the score itself is unvalidated; see SPEC.md §2 and §16c",
                "the neighbourhood describes the bibliography, not the argument: "
                "references that are mostly about the subject give the subject's journals",
            ],
        }


# --- writes, which are proposals and nothing else -------------------------


@server.tool()
def propose_venue(
    fields: dict, rationale: str, confidence: str = "medium", source_url: str = ""
) -> dict:
    """File a new journal into the queue for a person to approve.

    Use for journals no index knows about. `fields` takes `display_name` and
    optionally `issn_l`, `homepage_url`, `host_organization_name`, `is_oa`,
    `is_in_doaj`, `apc_usd`.

    `rationale` should say where the claim comes from. It is read by whoever
    approves, and "it looked right" is not a reason a person can check.
    """
    denied = _needs_admin()
    if denied:
        return denied
    if not fields.get("display_name"):
        return {"error": "display_name is required"}
    db.init_engine()
    with db.session_scope() as s:
        p = Proposal(
            kind="new_venue",
            fields=fields,
            rationale=rationale,
            confidence=confidence,
            source_url=source_url or None,
            status=ProposalStatus.PENDING,
        )
        s.add(p)
        s.flush()
        return {"proposal_id": p.id, "status": "pending", "note": "a person approves, not this tool"}


@server.tool()
def propose_update(
    venue_id: int, fields: dict, rationale: str, confidence: str = "medium", source_url: str = ""
) -> dict:
    """File a correction to a journal already in the table, for approval.

    The obvious use is what no API carries: article types and word limits read
    off the author guidelines. Give `source_url`, because a word limit without
    the page it came from cannot be rechecked when it changes.
    """
    denied = _needs_admin()
    if denied:
        return denied
    db.init_engine()
    with db.session_scope() as s:
        if s.get(Venue, venue_id) is None:
            return {"error": f"no venue {venue_id}"}
        p = Proposal(
            kind="update_venue",
            venue_id=venue_id,
            fields=fields,
            rationale=rationale,
            confidence=confidence,
            source_url=source_url or None,
            status=ProposalStatus.PENDING,
        )
        s.add(p)
        s.flush()
        return {"proposal_id": p.id, "status": "pending"}


@server.tool()
def propose_article_types(venue_id: int, source_url: str, page_text: str = "") -> dict:
    """Stage 5b: read a journal's author guidelines and file its article types.

    Give the URL of the page that lists the article types and their limits. The
    server fetches it, a model reads it into article types with word, abstract,
    reference and figure limits, and **every number is checked against the page**:
    a limit that is not in the sentence quoted for it is dropped and reported.
    What survives goes into the approval queue. Nothing is written to the
    journal's record here, and nothing on this surface can approve it.

    **A word limit never comes without what it counts** — whether the abstract,
    the references and the notes are included. Where the page does not say, the
    scope reads «not stated» and the proposal's confidence drops.

    **Some publishers refuse automated requests** (Wiley, OUP and BMJ did on
    8 Sep 2026; Springer did not). The answer is then `blocked_by_publisher`:
    read the page yourself and call again with its text in `page_text`. The same
    goes for a PDF. `source_url` is still required then — it is recorded as
    where the limits came from.

    **It spends your own money**: one small model call, charged to the
    Anthropic key stored by whoever owns the MCP key in use.
    """
    from anthropic import APIError

    from .matching import genre, guidelines
    from .sources import guidelines as pages

    denied = _needs_admin()
    if denied:
        return denied
    if not source_url.strip():
        return {"error": "source_url is required: a limit without its page cannot be rechecked"}

    db.init_engine()
    with db.session_scope() as s:
        venue = s.get(Venue, venue_id)
        if venue is None:
            return {"error": f"no venue {venue_id}"}
        try:
            text = (
                pages.clean_text(page_text) if page_text.strip()
                else pages.fetch_text(source_url.strip())
            )
        except pages.GuidelinesError as e:
            return {"error": e.code, "detail": e.detail}
        if not text:
            return {"error": "unsupported_page", "detail": "the page has no readable text"}

        client, problem = _callers_key(s)
        if problem:
            return problem
        try:
            found = guidelines.extract(client, venue.display_name, text)
        except guidelines.ExtractionUnavailable as e:
            return {"error": e.code, "detail": str(e)}
        except APIError as e:
            code, detail = genre.classify_api_error(e)
            return {"error": code, "detail": detail}

        if not found.types:
            return {
                "proposal_id": None,
                "article_types": [],
                "dropped": found.dropped,
                "page_is_guidelines": found.page_is_guidelines,
                "note": "nothing on this page could be filed, so nothing was",
            }
        p = guidelines.propose(s, venue, source_url.strip(), found)
        return {
            "proposal_id": p.id,
            "status": "pending",
            "venue": venue.display_name,
            "confidence": p.confidence,
            "article_types": found.types,
            "dropped": found.dropped,
            "model": found.model,
            "note": "filed for approval. A person approves it in the web app.",
        }


def main() -> None:  # pragma: no cover - entry point
    server.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
