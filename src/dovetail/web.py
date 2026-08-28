"""The web UI. SPEC.md §12.

Four screens plus the way in: the queue, past consultations, a venue's record
with a date on every field, and the budget. The queue is the only screen that
turns anything into a fact, and its two buttons sit behind `require_admin`.

**Roles are enforced here, not in the templates.** A template gets `user` and
decides what to draw; it never decides what may happen. Hiding a button while
leaving the route open is a decoration over a permission, not a permission — and
it is the mistake this codebase's older sibling made once and wrote down.
"""

from __future__ import annotations

import contextlib
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from . import auth, config, db
from .auth import COOKIE_NAME, current_user, require_admin
from .models import (
    ArticleType,
    Criterion,
    FieldVerification,
    MatchResult,
    MatchRun,
    OAModel,
    Proposal,
    ProposalStatus,
    Role,
    User,
    Venue,
)
from .proposals import ProposalError, approve, reject

TEMPLATES = Path(__file__).parent / "templates"
STATIC = Path(__file__).parent / "static"
templates = Jinja2Templates(directory=str(TEMPLATES))


def _asset_version() -> str:
    """A stamp for the static URLs, taken from the newest file on disk.

    The zone sits behind Cloudflare, which caches static assets for four hours.
    Without the stamp a deploy fixes the CSS at the origin while everyone keeps
    seeing the old one, and half an hour goes into looking for a bug that is not
    there. StaticFiles sends ETag and Last-Modified but no Cache-Control, and in
    that silence browsers apply heuristic caching and skip revalidating.
    """
    newest = 0.0
    for f in STATIC.rglob("*"):
        if f.is_file():
            newest = max(newest, f.stat().st_mtime)
    return str(int(newest))


templates.env.globals.update(ASSETS=_asset_version())

# The MCP transport is built before the app so its session manager exists: it is
# created lazily by `streamable_http_app()`, and reaching for it earlier raises.
from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402

from . import apikeys, mcp_server  # noqa: E402

PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8021")
LOCAL_PORT = os.environ.get("DOVETAIL_PORT", "8021")


def _allowed_hosts() -> list[str]:
    """Hosts the MCP transport will answer to.

    The check compares the whole `Host` header, **port included**, and a mismatch
    is a 421 whose body says "Invalid Host header" and nothing about which URL is
    wrong. Behind Caddy the header is the bare domain and matches; from the box
    itself it is `localhost:8021`, so both spellings have to be here or nobody
    can test the surface where it actually runs.
    """
    public = PUBLIC_URL.split("//")[-1].rstrip("/")
    hosts = ["localhost", "127.0.0.1", public, f"{public}:{LOCAL_PORT}"]
    hosts += [f"localhost:{LOCAL_PORT}", f"127.0.0.1:{LOCAL_PORT}"]
    return sorted(set(h for h in hosts if h))


_mcp_http = mcp_server.server.streamable_http_app(
    streamable_http_path="/",
    json_response=True,
    stateless_http=True,
    transport_security=TransportSecuritySettings(
        allowed_hosts=_allowed_hosts(),
        allowed_origins=[PUBLIC_URL],
    ),
)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """The MCP session manager has to run for the whole life of the process.

    In a startup event instead of a lifespan the transport answers 500 without
    saying why, which is one of the four ways a borant MCP deploy breaks. The
    other three are the `@pubbliche` routes in Caddy, `PUBLIC_URL`, and the
    trailing slash.
    """
    async with mcp_server.server.session_manager.run():
        yield


app = FastAPI(title="Dovetail", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/mcp", _mcp_http)
# `/static/*` is among Caddy's public paths, and it may be: nothing under it
# ever looks at who is asking, which is the whole test for whether a path
# belongs on that list.
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.middleware("http")
async def mcp_api_key_gate(request: Request, call_next):
    """`/mcp` is outside the Borant ID gate, so it carries its own key.

    Caddy lists `/mcp` among the public paths — a model client has no browser and
    no cookie — which means this middleware is the only thing between the open
    internet and a surface that spends the OpenAlex budget. A missing or unknown
    key is refused here, and the caller's role is what decides, inside the tools,
    whether an expensive call goes through.
    """
    if not request.url.path.startswith("/mcp"):
        return await call_next(request)

    # The trailing slash, which is the fourth way one of these deploys breaks.
    # The transport is mounted at /mcp with its own path of "/", so the real
    # endpoint is "/mcp/" and a POST to "/mcp" earns a 307. A redirect on a POST
    # is a bad thing to hand a client: some drop the body, some drop the
    # Authorization header, and the failure looks like the server being broken.
    # Rewriting here means both spellings work and nobody has to know.
    if request.url.path == "/mcp":
        request.scope["path"] = "/mcp/"
        request.scope["raw_path"] = b"/mcp/"

    key = request.headers.get("X-API-Key", "")
    db.init_engine()
    with db.session_scope() as s:
        user = apikeys.resolve(s, key)
        # Detached on purpose: the session closes with this block, and the tools
        # open their own.
        if user is not None:
            s.expunge(user)
    if user is None:
        return JSONResponse({"error": "unknown or missing X-API-Key"}, status_code=401)

    token = mcp_server.caller.set(user)
    try:
        return await call_next(request)
    finally:
        mcp_server.caller.reset(token)


@app.get("/healthz")
def healthz():
    """Cheap and unauthenticated: it says the process is up, and nothing else."""
    return {"ok": True}


def get_db():
    db.init_engine()
    with db.session_scope() as s:
        yield s


# The auth module declares a placeholder dependency so it does not import the
# app; the app wires the real one here. Without this the door has no lock.
app.dependency_overrides[auth._db_dep] = get_db


def page(
    request: Request,
    name: str,
    user: User | None,
    s: Session | None = None,
    **ctx,
) -> HTMLResponse:
    """Render a page with the context every template expects.

    `pending_count` is computed here rather than in each route so the badge in
    the topbar cannot go stale on the one screen somebody forgot to update. It
    costs one `COUNT` per page view, which is nothing next to what these pages
    already read.
    """
    pending_count = 0
    if user is not None and s is not None:
        pending_count = s.scalar(
            select(func.count()).select_from(Proposal).where(
                Proposal.status == ProposalStatus.PENDING
            )
        )
    return templates.TemplateResponse(
        request=request,
        name=name,
        context={
            "user": user,
            "gateway": auth.gateway_mode(),
            "pending_count": pending_count,
            "nav": ctx.pop("nav", None),
            **ctx,
        },
    )


# --- the way in -----------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def landing(request: Request):
    """The public front, and it **never looks at who is reading it**.

    That is the whole rule, and it is not a style choice. On the public branch
    Caddy strips the identity headers by construction, so a `{% if user %}` here
    would be *always false* behind the gate and *sometimes true* standalone: one
    page with two behaviours, and the difference invisible in every test that
    runs locally.

    Not looking makes the page identical in both modes, which is what lets a
    single button cover all four cases — gated or standalone, already signed in
    or not. The button points at `/app`, a **gated** path: that is what makes
    `forward_auth` run. Pointing it at `/login` would be a page that cannot
    recognise anyone offering a way back to itself, and pointing it at the
    gateway's own URL would wire Borant ID into an app that has to work without
    it.

    There is no `s: Session` parameter on purpose. A route with no database and
    no user cannot leak either.
    """
    return templates.TemplateResponse(request=request, name="landing.html", context={})


@app.exception_handler(status.HTTP_401_UNAUTHORIZED)
async def _needs_signin(request: Request, exc: HTTPException):
    """Locally, an unauthenticated page view is someone who has not signed in.

    This cannot loop: it only fires outside gateway mode — under the gate
    `current_user` raises 503 and never 401 — and `/login` requires no identity,
    so the redirect lands somewhere that renders. And it does not test `Accept`,
    which is the version of this handler that passes its tests and still leaves
    the loop in place, because httpx does not send that header.
    """
    if auth.gateway_mode():
        return JSONResponse({"error": "unauthenticated"}, status_code=401)
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, s: Session = Depends(get_db)):
    # In gateway mode this page must **render**, not bounce.
    #
    # It sits on the public branch, where the gate never fires, so it can never
    # learn who is asking. It draws a way *into* the gate instead — a link to a
    # path that is not public, which is what makes forward_auth run.
    return page(request, "login.html", None, error=None)


@app.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    s: Session = Depends(get_db),
):
    user = s.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None or not user.is_active or not auth.verify_password(password, user.hashed_password):
        # One message for both cases on purpose: saying "no such user" tells an
        # attacker which addresses exist here.
        return page(request, "login.html", None, error="Wrong email or password.")
    response = RedirectResponse("/app", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        COOKIE_NAME,
        auth.create_token(user.id),
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=auth.EXPIRE_DAYS * 86400,
    )
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(COOKIE_NAME)
    return response


# --- reading --------------------------------------------------------------


def _count(s: Session, *where) -> int:
    stmt = select(func.count()).select_from(Venue)
    for w in where:
        stmt = stmt.where(w)
    return s.scalar(stmt) or 0


# A venue declared by hand and then profiled against a sample that classified
# into nothing gets an empty list, not a NULL. Both are "cannot be scored", so
# the test has to cover both or the count quietly overstates what is usable.
HAS_PROFILE = and_(Venue.topics.isnot(None), func.json_array_length(Venue.topics) > 0)
NO_PROFILE = or_(Venue.topics.is_(None), func.json_array_length(Venue.topics) == 0)


def _inventory(s: Session) -> dict:
    """What this instance knows, and how much of it can actually be used.

    The second number is the one worth having: a journal with no topic profile
    cannot be scored at all, and its zero means «I don't know» rather than «out
    of scope». Counting the two together would hide exactly the distinction the
    rest of the tool is built to keep.
    """
    total = _count(s)
    profiled = _count(s, HAS_PROFILE)
    return {
        "total": total,
        "profiled": profiled,
        "unprofiled": total - profiled,
        "core": _count(s, Venue.is_core.is_(True)),
        "doaj": _count(s, Venue.is_in_doaj.is_(True)),
        "apc_known": _count(s, Venue.apc_usd.isnot(None)),
        "with_guidelines": s.scalar(
            select(func.count(func.distinct(ArticleType.venue_id)))
        ) or 0,
        "last_touched": s.scalar(select(func.max(FieldVerification.verified_at))),
    }


@app.get("/app", response_class=HTMLResponse)
def dashboard(request: Request, user: User = Depends(current_user),
              s: Session = Depends(get_db)):
    runs = list(s.scalars(select(MatchRun).order_by(MatchRun.id.desc()).limit(6)))
    result_counts = dict(
        s.execute(
            select(MatchResult.run_id, func.count())
            .where(MatchResult.run_id.in_([r.id for r in runs] or [0]))
            .group_by(MatchResult.run_id)
        ).all()
    )
    return page(
        request,
        "dashboard.html",
        user,
        s,
        nav="overview",
        inv=_inventory(s),
        runs_total=s.scalar(select(func.count()).select_from(MatchRun)),
        pending=s.scalar(
            select(func.count()).select_from(Proposal).where(
                Proposal.status == ProposalStatus.PENDING
            )
        ),
        budget={
            "total": config.daily_budget(),
            "spent": db.credits_spent(s),
            "remaining": db.credits_remaining(s),
            "classifications": db.credits_remaining(s) // config.COST_TEXT,
            "keyed": bool(config.openalex_api_key()),
        },
        recent=runs,
        result_counts=result_counts,
    )


PER_PAGE = 50

SORTS = {
    "works": Venue.works_count.desc().nullslast(),
    "hindex": Venue.h_index.desc().nullslast(),
    "name": Venue.display_name.asc(),
}


@app.get("/app/venues", response_class=HTMLResponse)
def venues(
    request: Request,
    q: str = "",
    oa: str = "",
    doaj: str = "",
    profile: str = "",
    core: str = "",
    risk: str = "",
    sort: str = "works",
    p: int = 1,
    user: User = Depends(current_user),
    s: Session = Depends(get_db),
):
    """The inventory, filtered and paged.

    It used to be the first 200 rows in alphabetical order out of sixteen
    thousand, which reads as a random slab and hides the two things worth
    knowing: how many there are, and how many can be scored. The default sort is
    by size **and says so** — it is the opposite of the bias stage 2 has to fight,
    and leaving it unlabelled would be the same mistake in the browsing surface.
    """
    filters = []
    if q:
        like = f"%{q}%"
        filters.append(or_(Venue.display_name.ilike(like), Venue.issn_l.ilike(like)))
    if oa in {m.value for m in OAModel}:
        filters.append(Venue.oa_model == OAModel(oa))
    if doaj == "yes":
        filters.append(Venue.is_in_doaj.is_(True))
    elif doaj == "no":
        filters.append(or_(Venue.is_in_doaj.is_(False), Venue.is_in_doaj.is_(None)))
    if profile == "yes":
        filters.append(HAS_PROFILE)
    elif profile == "no":
        filters.append(NO_PROFILE)
    if core == "yes":
        filters.append(Venue.is_core.is_(True))
    elif core == "no":
        # `type:journal` includes news bulletins, which is how FOX6 News
        # Milwaukee once came tenth in a shortlist. This is the switch that
        # shows what the `is_core` exclusion is actually removing.
        filters.append(or_(Venue.is_core.is_(False), Venue.is_core.is_(None)))
    if risk in {"high", "low"}:
        filters.append(Venue.predatory_risk["level"].as_string() == risk)

    total = _count(s, *filters)
    pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    p = min(max(1, p), pages)
    stmt = select(Venue)
    for f in filters:
        stmt = stmt.where(f)
    stmt = stmt.order_by(SORTS.get(sort, SORTS["works"])).offset((p - 1) * PER_PAGE).limit(PER_PAGE)

    return page(
        request, "venues.html", user, s,
        nav="venues",
        venues=list(s.scalars(stmt)),
        total=total, page_no=p, pages=pages, per_page=PER_PAGE,
        grand_total=_count(s),
        args={"q": q, "oa": oa, "doaj": doaj, "profile": profile,
              "core": core, "risk": risk, "sort": sort},
        oa_models=[m.value for m in OAModel],
    )


@app.get("/app/venues/new", response_class=HTMLResponse)
def new_venue_form(request: Request, user: User = Depends(require_admin),
                   s: Session = Depends(get_db)):
    # Before `/app/venues/{venue_id}` for the same reason as the run form: an
    # `int` path parameter registered first would swallow "new".
    return page(request, "venue_new.html", user, s, nav="venues", form={}, error=None)


@app.post("/app/venues/new")
def new_venue(
    request: Request,
    display_name: str = Form(...),
    issn_l: str = Form(""),
    homepage_url: str = Form(""),
    host_organization_name: str = Form(""),
    apc_usd: str = Form(""),
    anvur_class: str = Form(""),
    note_url: str = Form(""),
    is_in_doaj: str = Form(""),
    is_oa: str = Form(""),
    user: User = Depends(require_admin),
    s: Session = Depends(get_db),
):
    """Declare a journal no index knows about.

    This writes straight to the table instead of going through the queue, and
    the difference is who is claiming. The queue exists for what an *agent*
    infers — a guideline read by a model, a name resolved lexically — and the
    person approving is not the person who proposed. Here the person filling the
    form is the person who would approve it, so the extra step would be theatre.
    The stamp says `manual`, which is a dated and attributed claim like any
    other.
    """
    from . import manual

    name = display_name.strip()
    form = {"display_name": name, "issn_l": issn_l, "homepage_url": homepage_url,
            "host_organization_name": host_organization_name, "apc_usd": apc_usd,
            "anvur_class": anvur_class, "note_url": note_url,
            "is_in_doaj": is_in_doaj, "is_oa": is_oa}
    if not name:
        return page(request, "venue_new.html", user, s, nav="venues", form=form,
                    error="A journal needs a name.")

    def tri(v: str) -> bool | None:
        # Three states and not two. "Unknown" has to survive the form, because a
        # constraint never excludes on a missing field — it marks — and turning
        # every unticked box into a False would quietly convert "nobody checked"
        # into "no", which is the one substitution this codebase refuses.
        return {"yes": True, "no": False}.get(v)

    venue = manual.add_venue(
        s,
        display_name=name,
        issn_l=issn_l.strip() or None,
        homepage_url=homepage_url.strip() or None,
        host_organization_name=host_organization_name.strip() or None,
        is_in_doaj=tri(is_in_doaj),
        is_oa=tri(is_oa),
        apc_usd=int(apc_usd) if apc_usd.strip().isdigit() else None,
        anvur_class=anvur_class.strip() or None,
        note_url=note_url.strip() or None,
    )
    s.flush()
    return RedirectResponse(f"/app/venues/{venue.id}?declared=1",
                            status_code=status.HTTP_303_SEE_OTHER)


@app.get("/app/venues/{venue_id}", response_class=HTMLResponse)
def venue_detail(request: Request, venue_id: int, declared: str = "",
                 user: User = Depends(current_user), s: Session = Depends(get_db)):
    venue = s.get(Venue, venue_id)
    if venue is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such venue")
    # Every consultation this journal has ever been shortlisted in. Reading a
    # venue backwards — which papers it came up for, and where it placed — is
    # something the record could always answer and never did.
    appearances = list(
        s.execute(
            select(MatchResult, MatchRun)
            .join(MatchRun, MatchResult.run_id == MatchRun.id)
            .where(MatchResult.venue_id == venue_id)
            .order_by(MatchResult.run_id.desc())
            .limit(20)
        ).all()
    )
    return page(
        request,
        "venue.html",
        user,
        s,
        nav="venues",
        venue=venue,
        article_types=list(s.scalars(select(ArticleType).where(ArticleType.venue_id == venue_id))),
        stale_days=config.STALE_DAYS,
        appearances=appearances,
        topic_total=sum(t.get("count", 0) for t in (venue.topics or [])) or 1,
        declared=declared == "1",
    )


@app.get("/app/runs", response_class=HTMLResponse)
def runs(request: Request, user: User = Depends(current_user), s: Session = Depends(get_db)):
    rows = list(s.scalars(select(MatchRun).order_by(MatchRun.id.desc()).limit(100)))
    counts = dict(
        s.execute(
            select(MatchResult.run_id, func.count())
            .where(MatchResult.run_id.in_([r.id for r in rows] or [0]))
            .group_by(MatchResult.run_id)
        ).all()
    )
    return page(request, "runs.html", user, s, nav="runs", runs=rows, counts=counts)


def estimate_run(s: Session, discover: bool) -> dict:
    """What a consultation will cost, before it is started. Free, and no calls.

    The house rule for a tool that spends: next to the thing that spends there
    has to be a thing that estimates, and it has to be free — a cost seen
    afterwards is not a decision. Every number here comes from the rate-limit
    headers OpenAlex returns, read on 27 Aug 2026, so this is arithmetic and not
    a guess.
    """
    classify = config.COST_TEXT
    sweep = config.MAX_CANDIDATE_PAGES * config.COST_SOURCES if discover else 0
    remaining = db.credits_remaining(s)
    return {
        "classify": classify,
        "sweep": sweep,
        "sweep_pages": config.MAX_CANDIDATE_PAGES if discover else 0,
        "total": classify + sweep,
        "remaining": remaining,
        "budget": config.daily_budget(),
        # The sweep is a ceiling, not a bill: it stops when the pool runs out.
        # Stage 1 is the one that cannot be skipped, so it is the one the check
        # below is about.
        "affordable": remaining >= classify,
    }


@app.get("/app/runs/new", response_class=HTMLResponse)
def new_run_form(request: Request, user: User = Depends(require_admin),
                 s: Session = Depends(get_db)):
    # Declared **before** `/app/runs/{run_id}`, and that ordering is load
    # bearing: routes match in registration order, so the other way round
    # "new" would be handed to a path parameter typed `int` and answered with
    # a 422 that mentions parsing.
    return page(request, "run_new.html", user, s, nav="runs",
                estimate=estimate_run(s, discover=True),
                min_words=config.MIN_ABSTRACT_WORDS, form={}, error=None)


def _execute_run(run_id: int, discover: bool, use_doaj: bool) -> None:
    """The sweep, off the request.

    A hundred-odd calls take the better part of a minute, which is too long to
    hold a browser and far too long to hold a SQLite write session while other
    pages are trying to read. So the row is committed as `running` first, the
    browser is sent to it, and this fills it in afterwards.

    Nothing here raises into the caller: there is no caller left. Every way this
    can end is written onto the row, because a run that stops leaving no trace
    is exactly the state the `status` column exists to prevent.
    """
    from .matching.pipeline import Refusal, run_match
    from .sources.doaj import DoajClient
    from .sources.openalex import (
        EndpointBroken,
        OpenAlexClient,
        OpenAlexError,
        RemoteBudgetExhausted,
    )

    with db.session_scope() as s:
        run = s.get(MatchRun, run_id)
        if run is None:  # pragma: no cover - only if the row was deleted meanwhile
            return
        constraints = {k: v for k, v in (run.constraints or {}).items()
                       if not k.startswith("_")}
        try:
            run_match(
                s,
                OpenAlexClient(),
                run.title,
                run.abstract,
                run.word_count,
                constraints,
                discover=discover,
                doaj=DoajClient() if use_doaj else None,
                run=run,
            )
            run.status = "done"
        except Refusal as e:
            # Not a failure: the matcher declining to guess is an answer, and
            # `run_match` has already written the reason onto the row.
            run.status = "refused"
        except (db.BudgetExhausted, RemoteBudgetExhausted) as e:
            run.status, run.error_code, run.error_detail = "failed", "budget", str(e)
        except EndpointBroken as e:
            run.status, run.error_code, run.error_detail = "failed", "endpoint_broken", str(e)
        except OpenAlexError as e:
            run.status, run.error_code, run.error_detail = "failed", "openalex", str(e)
        except Exception as e:  # noqa: BLE001 - the row must not be left saying "running"
            run.status = "failed"
            run.error_code, run.error_detail = "unexpected", f"{type(e).__name__}: {e}"
        run.finished_at = datetime.now(timezone.utc)


@app.post("/app/runs/new")
def new_run(
    request: Request,
    background: BackgroundTasks,
    title: str = Form(...),
    abstract: str = Form(...),
    word_count: str = Form(""),
    funder: str = Form(""),
    max_apc: str = Form(""),
    discover: str = Form(""),
    doaj: str = Form(""),
    user: User = Depends(require_admin),
    s: Session = Depends(get_db),
):
    from .matching.pipeline import Refusal, guard_rail

    discover_on, doaj_on = discover == "on", doaj == "on"
    form = {"title": title, "abstract": abstract, "word_count": word_count,
            "funder": funder, "max_apc": max_apc,
            "discover": discover_on, "doaj": doaj_on}

    def refuse(msg: str):
        return page(request, "run_new.html", user, s, nav="runs",
                    estimate=estimate_run(s, discover_on),
                    min_words=config.MIN_ABSTRACT_WORDS, form=form, error=msg)

    # Both checks are free and both are done *before* anything is written. The
    # short-abstract guard rail costs nothing to evaluate, so failing it should
    # not leave a refused run in the table; the deeper refusal — a classification
    # too uncertain to mean anything — can only be known after spending, and that
    # one is recorded.
    try:
        guard_rail(abstract)
    except Refusal as e:
        return refuse(str(e))
    est = estimate_run(s, discover_on)
    if not est["affordable"]:
        return refuse(
            f"Not enough budget: classifying costs {est['classify']} credits and "
            f"{est['remaining']} are left of {est['budget']} for today. It resets "
            f"at midnight UTC."
        )

    constraints = {}
    if funder.strip():
        constraints["funder"] = funder.strip()
    if max_apc.strip().isdigit():
        constraints["max_apc"] = int(max_apc)

    run = MatchRun(
        title=title.strip(),
        abstract=abstract.strip(),
        word_count=int(word_count) if word_count.strip().isdigit() else None,
        constraints=constraints,
        scoring_config_version=config.SCORING_CONFIG_VERSION,
        status="running",
    )
    s.add(run)
    s.flush()
    run_id = run.id
    # Committed here and not left to the dependency: the background task opens
    # its own session and has to be able to find the row.
    s.commit()

    background.add_task(_execute_run, run_id, discover_on, doaj_on)
    return RedirectResponse(f"/app/runs/{run_id}", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/app/runs/{run_id}", response_class=HTMLResponse)
def run_detail(request: Request, run_id: int, user: User = Depends(current_user),
               s: Session = Depends(get_db)):
    run = s.get(MatchRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such run")
    rows = []
    for res in s.scalars(
        select(MatchResult).where(MatchResult.run_id == run_id).order_by(MatchResult.position)
    ):
        crits = list(s.scalars(select(Criterion).where(Criterion.result_id == res.id)))
        venue = s.get(Venue, res.venue_id)
        rows.append(
            {
                "result": res,
                "venue": venue,
                "merit": [c for c in crits if c.kind.value == "merit"],
                "logistics": [c for c in crits if c.kind.value == "logistics"],
                # A venue sharing no topic with the text is one stage 2 would
                # never have produced. On the validation case that is the only
                # signal the retrodiction actually rests on, and it was computed
                # and then thrown away instead of being shown.
                #
                # `score_topic > 0` is that test exactly, not an approximation
                # of it: both profiles carry only positive weights, so their
                # cosine is above zero precisely when they share a topic id.
                "reachable": res.score_topic > 0,
            }
        )
    return page(
        request, "run.html", user, s,
        nav="runs",
        run=run,
        rows=rows,
        # The manuscript's own profile: the input every score below is measured
        # against, and until now the one thing the page did not show.
        text_topics=(run.text_profile or {}).get("topics") or [],
    )


@app.get("/app/proposals", response_class=HTMLResponse)
def proposals(request: Request, status_filter: str = "pending",
              user: User = Depends(current_user), s: Session = Depends(get_db)):
    rows = list(
        s.scalars(
            select(Proposal)
            .where(Proposal.status == ProposalStatus(status_filter))
            .order_by(Proposal.id.desc())
            .limit(200)
        )
    )
    # Only the venues these proposals point at: loading all sixteen thousand to
    # resolve at most two hundred names was a full table scan per page view.
    venue_ids = {p.venue_id for p in rows if p.venue_id}
    venues_by_id = (
        {v.id: v for v in s.scalars(select(Venue).where(Venue.id.in_(venue_ids)))}
        if venue_ids
        else {}
    )
    counts = dict(
        s.execute(
            select(Proposal.status, func.count()).group_by(Proposal.status)
        ).all()
    )
    return page(request, "proposals.html", user, s,
                nav="proposals", proposals=rows,
                venues=venues_by_id, status_filter=status_filter,
                counts={k.value: v for k, v in counts.items()})


# --- the two buttons that change something --------------------------------


@app.post("/app/proposals/{proposal_id}/approve")
def approve_proposal(proposal_id: int, user: User = Depends(require_admin),
                     s: Session = Depends(get_db)):
    try:
        approve(s, proposal_id, user.email)
    except ProposalError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return RedirectResponse("/app/proposals", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/app/proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: int, user: User = Depends(require_admin),
                    s: Session = Depends(get_db)):
    try:
        reject(s, proposal_id, user.email)
    except ProposalError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return RedirectResponse("/app/proposals", status_code=status.HTTP_303_SEE_OTHER)


# --- users ----------------------------------------------------------------


@app.get("/app/admin/users", response_class=HTMLResponse)
def users(request: Request, user: User = Depends(require_admin), s: Session = Depends(get_db)):
    return page(request, "users.html", user, s, nav="users",
                users=list(s.scalars(select(User).order_by(User.email))))


@app.post("/app/admin/users/{user_id}/role")
def set_role(user_id: int, role: str = Form(...), user: User = Depends(require_admin),
             s: Session = Depends(get_db)):
    target = s.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such user")
    if target.id == user.id and role != Role.ADMIN.value:
        # Locking yourself out is not a permission question, it is a mistake the
        # app can simply decline to make on your behalf.
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "You cannot remove your own admin role.")
    target.role = Role(role)
    s.flush()
    return RedirectResponse("/app/admin/users", status_code=status.HTTP_303_SEE_OTHER)
