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
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import auth, config, db
from .auth import COOKIE_NAME, current_user, require_admin
from .models import (
    ArticleType,
    Criterion,
    MatchResult,
    MatchRun,
    Proposal,
    ProposalStatus,
    Role,
    User,
    Venue,
)
from .proposals import ProposalError, approve, reject

TEMPLATES = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES))

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


def page(request: Request, name: str, user: User | None, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name=name,
        context={"user": user, "gateway": auth.gateway_mode(), **ctx},
    )


def _maybe_user(request: Request, s: Session) -> User | None:
    return auth.user_or_none(request, s, request.cookies.get(COOKIE_NAME))


# --- the way in -----------------------------------------------------------


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, s: Session = Depends(get_db)):
    # In gateway mode this page must **render**, not redirect to "/".
    #
    # "/" is among Caddy's public paths, so the gate never fires there: the app
    # sees no identity, sends the visitor to /login, and /login used to send them
    # back to "/". That is an infinite loop, and it is what anyone opening the
    # bare domain got. So here the page draws a way *into* the gate instead —
    # a link to a path that is not public, which is what makes forward_auth run.
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
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
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


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, s: Session = Depends(get_db)):
    user = _maybe_user(request, s)
    if user is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    return page(
        request,
        "dashboard.html",
        user,
        venues=s.scalar(select(func.count()).select_from(Venue)),
        runs=s.scalar(select(func.count()).select_from(MatchRun)),
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
        recent=list(s.scalars(select(MatchRun).order_by(MatchRun.id.desc()).limit(5))),
    )


@app.get("/venues", response_class=HTMLResponse)
def venues(request: Request, q: str = "", user: User = Depends(current_user),
           s: Session = Depends(get_db)):
    stmt = select(Venue).order_by(Venue.display_name)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Venue.display_name.ilike(like), Venue.issn_l.ilike(like)))
    return page(request, "venues.html", user, venues=list(s.scalars(stmt.limit(200))), q=q)


@app.get("/venues/{venue_id}", response_class=HTMLResponse)
def venue_detail(request: Request, venue_id: int, user: User = Depends(current_user),
                 s: Session = Depends(get_db)):
    venue = s.get(Venue, venue_id)
    if venue is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such venue")
    return page(
        request,
        "venue.html",
        user,
        venue=venue,
        article_types=list(s.scalars(select(ArticleType).where(ArticleType.venue_id == venue_id))),
        stale_days=config.STALE_DAYS,
    )


@app.get("/runs", response_class=HTMLResponse)
def runs(request: Request, user: User = Depends(current_user), s: Session = Depends(get_db)):
    return page(
        request, "runs.html", user,
        runs=list(s.scalars(select(MatchRun).order_by(MatchRun.id.desc()).limit(100))),
    )


@app.get("/runs/{run_id}", response_class=HTMLResponse)
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
        rows.append(
            {
                "result": res,
                "venue": s.get(Venue, res.venue_id),
                "merit": [c for c in crits if c.kind.value == "merit"],
                "logistics": [c for c in crits if c.kind.value == "logistics"],
            }
        )
    return page(request, "run.html", user, run=run, rows=rows)


@app.get("/proposals", response_class=HTMLResponse)
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
    venues_by_id = {v.id: v for v in s.scalars(select(Venue))} if rows else {}
    return page(request, "proposals.html", user, proposals=rows,
                venues=venues_by_id, status_filter=status_filter)


# --- the two buttons that change something --------------------------------


@app.post("/proposals/{proposal_id}/approve")
def approve_proposal(proposal_id: int, user: User = Depends(require_admin),
                     s: Session = Depends(get_db)):
    try:
        approve(s, proposal_id, user.email)
    except ProposalError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return RedirectResponse("/proposals", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: int, user: User = Depends(require_admin),
                    s: Session = Depends(get_db)):
    try:
        reject(s, proposal_id, user.email)
    except ProposalError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    return RedirectResponse("/proposals", status_code=status.HTTP_303_SEE_OTHER)


# --- users ----------------------------------------------------------------


@app.get("/admin/users", response_class=HTMLResponse)
def users(request: Request, user: User = Depends(require_admin), s: Session = Depends(get_db)):
    return page(request, "users.html", user,
                users=list(s.scalars(select(User).order_by(User.email))))


@app.post("/admin/users/{user_id}/role")
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
    return RedirectResponse("/admin/users", status_code=status.HTTP_303_SEE_OTHER)
