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

from . import config, db
from .db import BudgetExhausted
from .matching import criteria as criteria_mod
from .matching.pipeline import Refusal, run_match
from .models import (
    ArticleType,
    Criterion,
    MatchResult,
    MatchRun,
    Proposal,
    ProposalStatus,
    Source,
    Venue,
)
from .sources.doaj import DoajClient
from .sources.openalex import OpenAlexClient, OpenAlexError

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
                "this call either spends the shared OpenAlex budget or writes "
                "into the approval queue, and the key you used belongs to a reader"
            )
        }
    return None


def _venue_brief(v: Venue) -> dict[str, Any]:
    return {
        "venue_id": v.id,
        "name": v.display_name,
        "issn_l": v.issn_l,
        "publisher": v.host_organization_name,
        "oa_model": getattr(v.oa_model, "value", v.oa_model),
        "apc_usd": v.apc_usd,
        "anvur": v.anvur_class,
        "predatory_risk": (v.predatory_risk or {}).get("level"),
    }


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
    }


# --- reads ----------------------------------------------------------------


@server.tool()
def budget_status() -> dict:
    """How many OpenAlex credits are left today, and what they buy.

    Worth checking before `match_venues`, which is the only expensive call.
    """
    db.init_engine()
    with db.session_scope() as s:
        spent, remaining = db.credits_spent(s), db.credits_remaining(s)
    return {
        "daily_budget": config.daily_budget(),
        "spent": spent,
        "remaining": remaining,
        "classifications_left": remaining // config.COST_TEXT,
        "has_api_key": bool(config.openalex_api_key()),
        "note": "resets at midnight UTC; a free account key multiplies the budget by ten",
    }


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
    asked before spending credits asking it again. A run with `refused` set
    produced no list at all — usually an abstract too short to classify — and
    the reason is worth reading before retrying with the same text.
    """
    db.init_engine()
    with db.session_scope() as s:
        runs = list(s.scalars(select(MatchRun).order_by(MatchRun.id.desc()).limit(limit)))
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
        res = s.scalar(
            select(MatchResult).where(
                MatchResult.run_id == run_id, MatchResult.venue_id == venue_id
            )
        )
        if res is None:
            return {"error": f"venue {venue_id} was not in run {run_id}"}
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
            "evidence": {c.label: c.evidence for c in crits},
            "constraints": res.excluded_by,
            "flags": res.flags,
            "snapshot_works_count": (res.venue_snapshot or {}).get("works_count"),
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


# --- the expensive one ----------------------------------------------------


@server.tool()
def match_venues(
    title: str,
    abstract: str,
    word_count: int | None = None,
    funder: str | None = None,
    max_apc: int | None = None,
    discover: bool = True,
) -> dict:
    """Candidate journals for a manuscript. **Slow and expensive: read this.**

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
    denied = _needs_admin()
    if denied:
        return denied
    db.init_engine()
    with db.session_scope() as s:
        constraints = {k: v for k, v in {"funder": funder, "max_apc": max_apc}.items() if v}
        try:
            run, shortlist, excluded_shown, unclassifiable = run_match(
                s,
                OpenAlexClient(),
                title,
                abstract,
                word_count,
                constraints,
                discover=discover,
                doaj=DoajClient(),
            )
        except Refusal as e:
            return {"refused": str(e)}
        except BudgetExhausted as e:
            return {"budget_exhausted": str(e)}
        except OpenAlexError as e:
            return {"error": str(e)}

        sweep = (run.constraints or {}).get("_sweep") or {}
        return {
            "run_id": run.id,
            "shortlist": [_row_out(r) for r in shortlist],
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
    `is_in_doaj`, `apc_usd`, `anvur_class` (as `sector:band`, e.g. `11/C3:A` —
    a band with no sector means nothing).

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


def main() -> None:  # pragma: no cover - entry point
    server.run(transport="stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
