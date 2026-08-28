"""Phase 1 CLI. There is no UI yet: this is where you look."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from sqlalchemy import select

from . import config, db, seed as seed_mod
from .db import BudgetExhausted
from .matching import criteria as criteria_mod
from .matching.pipeline import Refusal, run_match
from .models import MatchResult, MatchRun, Proposal, ProposalStatus, Venue
from .sources.doaj import DoajClient
from .sources.enrich import enrich_from_doaj
from .sources.openalex import (
    EndpointBroken,
    OpenAlexClient,
    OpenAlexError,
    RemoteBudgetExhausted,
)

app = typer.Typer(add_completion=False, help="Dovetail — where to send a paper.")


@app.command("init-db")
def init_db(path: str = typer.Option(None, help="Path to the SQLite file.")):
    """Create or upgrade the schema, through Alembic.

    This runs `alembic upgrade head`, so it is both "create it" and "bring it up
    to date": running it on an existing database applies whatever migrations are
    missing and leaves the data alone.

    It deliberately does **not** call `create_all`. Dropping the file was fine
    while the schema changed twice in an afternoon; on a deployment holding
    hand-curated venues and recorded runs it is not, and a database built by
    `create_all` carries no version stamp, so the first real migration would find
    the tables already there and fail.
    """
    from alembic import command
    from alembic.config import Config

    if path:
        import os

        os.environ["DOVETAIL_DB"] = path

    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(cfg, "head")
    typer.echo(f"schema up to date at {config.db_path()}")


@app.command()
def budget():
    """How many OpenAlex credits are left today."""
    db.init_engine()
    with db.session_scope() as s:
        spent = db.credits_spent(s)
        remaining = db.credits_remaining(s)
    total = config.daily_budget()
    key = "yes" if config.openalex_api_key() else "no (anonymous account)"
    typer.echo(f"OPENALEX_API_KEY: {key}")
    typer.echo(f"daily budget: {total} credits — {spent} spent, {remaining} left")
    typer.echo(
        f"in practice: {remaining // config.COST_TEXT} classifications "
        f"or {remaining // config.COST_SOURCES} /sources calls"
    )
    if not config.openalex_api_key():
        typer.echo(
            "\nA free OpenAlex account raises the budget from "
            f"{config.BUDGET_ANONYMOUS} to {config.BUDGET_WITH_KEY} credits a day. "
            "The account has to be created by hand."
        )


@app.command()
def seed(only: str = typer.Option(None, help="A single string, to try it out.")):
    """Resolve PaperTrail's venue vocabulary and file alias proposals."""
    db.init_engine()
    client = OpenAlexClient()
    with db.session_scope() as s:
        try:
            report = seed_mod.seed_venues(s, client, (only,) if only else None)
        except BudgetExhausted as e:
            typer.secho(f"budget: {e}", fg=typer.colors.YELLOW)
            raise typer.Exit(code=2)
    typer.echo(json.dumps(report, indent=2, ensure_ascii=False))


@app.command()
def proposals(status: str = typer.Option("pending")):
    """The queue. Approval, once there is a UI, will live there."""
    db.init_engine()
    with db.session_scope() as s:
        rows = list(s.scalars(select(Proposal).where(Proposal.status == ProposalStatus(status))))
        for p in rows:
            typer.echo(f"[{p.id}] {p.kind} · {p.confidence} · {p.fields}")
            typer.echo(f"     {p.rationale}")
        typer.echo(f"\n{len(rows)} proposals with status {status}")


@app.command("approve-alias")
def approve_alias(proposal_id: int, by: str = typer.Option(..., help="Who approves.")):
    """Confirm a resolution. Until it passes through here, it is not an alias."""
    db.init_engine()
    with db.session_scope() as s:
        alias = seed_mod.approve_alias(s, proposal_id, by)
        typer.echo(f"alias confirmed: «{alias.alias_string}» → venue {alias.venue_id}")


@app.command()
def match(
    title: str = typer.Option(..., help="Manuscript title."),
    abstract_file: Path = typer.Option(..., help="Text file holding the abstract."),
    word_count: int = typer.Option(None),
    funder: str = typer.Option(None, help="e.g. snsf"),
    max_apc: int = typer.Option(None),
    discover: bool = typer.Option(True, help="Whether to query OpenAlex for new candidates."),
    doaj: bool = typer.Option(
        True, help="Enrich the finalists from DOAJ (free, one call per shortlisted venue)."
    ),
    profile: Path = typer.Option(
        None,
        help="JSON of a classification already done: skips stage 1 and its 100 credits.",
    ),
):
    """Stages 1 to 4. Produce and record a shortlist."""
    db.init_engine()
    abstract = abstract_file.read_text(encoding="utf-8").strip()
    constraints = {k: v for k, v in {"funder": funder, "max_apc": max_apc}.items() if v}
    client = OpenAlexClient()
    precomputed = json.loads(profile.read_text(encoding="utf-8")) if profile else None

    with db.session_scope() as s:
        try:
            run, shortlist, excluded_shown, unclassifiable = run_match(
                s,
                client,
                title,
                abstract,
                word_count,
                constraints,
                discover=discover,
                precomputed_profile=precomputed,
                doaj=DoajClient() if doaj else None,
            )
        except Refusal as e:
            typer.secho(f"refused: {e}", fg=typer.colors.YELLOW)
            raise typer.Exit(code=3)
        except (BudgetExhausted, RemoteBudgetExhausted) as e:
            # Two roads to the same wall: the local counter refusing before it
            # spends, and the server's 429, which knows about spend elsewhere.
            typer.secho(f"budget: {e}", fg=typer.colors.YELLOW)
            raise typer.Exit(code=2)
        except EndpointBroken as e:
            typer.secho(f"broken endpoint (not the budget): {e}", fg=typer.colors.RED)
            raise typer.Exit(code=5)
        except OpenAlexError as e:
            typer.secho(f"OpenAlex: {e}", fg=typer.colors.RED)
            raise typer.Exit(code=4)

        typer.echo(f"\nrun #{run.id} · {len(shortlist)} venues shortlisted\n")
        _print(shortlist)
        if excluded_shown:
            typer.secho(
                "\nFewer than three venues pass the constraints. Here are the excluded "
                "ones, with the constraint that removed them — an empty list is not "
                "an answer:\n",
                fg=typer.colors.YELLOW,
            )
            _print(excluded_shown)
        if unclassifiable:
            typer.secho(
                "\nUNCLASSIFIABLE — no constraint excludes them, but there is not "
                "enough to build a profile. Here a zero means «I don't know», not "
                "«out of scope»; give them a profile with `profile-venue`:\n",
                fg=typer.colors.YELLOW,
            )
            for r in unclassifiable:
                typer.echo(f"  {r.venue.display_name} — {', '.join(r.score.notes)}")


def _print(rows) -> None:
    for r in rows:
        red = criteria_mod.is_red(r.criteria)
        mark = "  RED" if red else ""
        colour = typer.colors.RED if red else None
        typer.secho(f"  {r.venue.display_name}{mark}", fg=colour, bold=True)
        typer.echo(
            f"    topic {r.score.topic:.4f} · subfield {r.score.subfield:.4f} "
            f"· field {r.score.field:.4f} · "
            f"{'reachable' if r.score.stage2_reachable else 'NOT reachable at stage 2'}"
        )
        merit = [c for c in r.criteria if c.kind == criteria_mod.MERIT]
        logistics = [c for c in r.criteria if c.kind == criteria_mod.LOGISTICS]
        typer.echo(f"    merit ({len(merit)}): " + ("; ".join(c.label for c in merit) or "—"))
        typer.echo(
            f"    logistics ({len(logistics)}): "
            + ("; ".join(c.label for c in logistics) or "—")
        )
        for o in r.outcomes:
            typer.echo(f"    [{o.outcome}] {o.constraint}: {o.reason}")
        for f in list(r.score.notes) + r.predatory["flags"]:
            typer.echo(f"    ! {f}")
        typer.echo("")


@app.command("add-venue")
def add_venue(
    name: str = typer.Option(..., help="Journal name."),
    issn: str = typer.Option(None),
    homepage: str = typer.Option(None),
    publisher: str = typer.Option(None),
    doaj: bool = typer.Option(None, "--doaj/--no-doaj"),
    oa: bool = typer.Option(None, "--oa/--no-oa"),
    apc: int = typer.Option(None),
    anvur: str = typer.Option(
        None, help='Band per sector, e.g. "11/C3:A" (comma separated for more than one).'
    ),
):
    """Declare by hand a journal no index knows about.

    For venues such as *Future of Science and Ethics*, which exists and publishes
    but is not on OpenAlex. After this command the journal is in the table but
    stays **unclassifiable** until you give it a profile with `profile-venue`.
    """
    from . import manual

    db.init_engine()
    with db.session_scope() as s:
        v = manual.add_venue(
            s,
            display_name=name,
            issn_l=issn,
            homepage_url=homepage,
            host_organization_name=publisher,
            is_in_doaj=doaj,
            is_oa=oa,
            apc_usd=apc,
            anvur_class=anvur,
        )
        typer.echo(f"venue #{v.id}: {v.display_name} · {getattr(v.oa_model, 'value', v.oa_model)}")
        typer.echo(
            "  no profile yet: it will come out under UNCLASSIFIABLE until you give "
            "it some articles with `profile-venue`."
        )


@app.command("profile-venue")
def profile_venue(
    name: str = typer.Option(..., help="Name (or ISSN) of a journal already in the table."),
    articles: Path = typer.Option(
        ..., help='JSON: [{"title": "...", "abstract": "..."}, ...] from that journal.'
    ),
):
    """Build the scope profile from the articles the journal publishes.

    Costs **100 credits per article**: five to ten is the right size. Take them
    from different years — ten articles from one special issue describe that
    issue, not the journal.
    """
    from . import manual

    db.init_engine()
    payload = json.loads(articles.read_text(encoding="utf-8"))
    with db.session_scope() as s:
        v = s.scalar(select(Venue).where(Venue.display_name == name)) or s.scalar(
            select(Venue).where(Venue.issn_l == name)
        )
        if v is None:
            typer.secho(f"no venue «{name}»: create it first with add-venue", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        try:
            report = manual.profile_from_texts(s, OpenAlexClient(), v, payload)
        except (BudgetExhausted, RemoteBudgetExhausted) as e:
            typer.secho(f"budget: {e}", fg=typer.colors.YELLOW)
            raise typer.Exit(code=2)
    typer.echo(json.dumps(report, indent=2, ensure_ascii=False))


@app.command("import-source")
def import_source(files: list[Path]):
    """Load an already downloaded `/sources` response into the table.

    Useful for working without spending credits, and legitimate because a saved
    response is a dated source like any other: the verification stamp says
    `openalex`, not "guessed".
    """
    db.init_engine()
    from .matching.pipeline import upsert_venue

    with db.session_scope() as s:
        for f in files:
            v = upsert_venue(s, json.loads(f.read_text(encoding="utf-8")))
            typer.echo(f"  {v.issn_l or '?':<11} {v.display_name}")


@app.command()
def venues():
    """What is in the table."""
    db.init_engine()
    with db.session_scope() as s:
        rows = list(s.scalars(select(Venue)))
        for v in rows:
            model = getattr(v.oa_model, "value", v.oa_model)
            typer.echo(f"  {v.issn_l or '?':<11} {v.display_name[:48]:<50} {model}")
        typer.echo(f"\n{len(rows)} venues")




@app.command("validate-against-published")
def validate_against_published(
    dois: Path = typer.Option(..., help="Text file with one DOI per line."),
    top_n: int = typer.Option(12),
    discover: bool = typer.Option(True),
):
    """RETIRED as validation. Kept as diagnostics, and it prints why.

    This asked where the journal that really published a paper comes out, and
    the question does not work: a paper lands somewhere for relationships,
    invitations and speed, none of which the tool models, so a disagreement
    cannot be read as the matcher being wrong rather than the matcher optimising
    something else. It also measures a **rank**, which SPEC §0 says this output
    is not.

    What replaced it: `check-negatives` and `blind-sheet` / `score-sheet`. See
    the module docstring of `validation.py`.

    Still useful for one thing — seeing *whether the true venue is reachable at
    all* — which is why it is here rather than deleted. Costs about 102 credits
    per paper.
    """
    typer.secho(
        "Note: this is diagnostics, not validation — see `check-negatives` and "
        "`blind-sheet`. A paper lands in a journal for reasons this tool does "
        "not model, so agreement here is not evidence and disagreement is not a "
        "fault.",
        fg=typer.colors.YELLOW,
    )
    from .validate import rank_of_true_venue, summarise

    db.init_engine()
    lines = [
        line.strip()
        for line in dois.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    client = OpenAlexClient()
    results = []

    with db.session_scope() as s:
        for doi in lines:
            try:
                r = rank_of_true_venue(s, client, doi, top_n=top_n, discover=discover)
            except (BudgetExhausted, RemoteBudgetExhausted) as e:
                typer.secho(f"budget ran out at {doi}: {e}", fg=typer.colors.YELLOW)
                break
            except OpenAlexError as e:
                typer.secho(f"  {doi}: {e}", fg=typer.colors.RED)
                continue
            results.append(r)
            place = f"{r.position} of {r.total}" if r.position else "NOT IN THE LIST"
            colour = (
                typer.colors.GREEN
                if r.in_top
                else (typer.colors.YELLOW if r.position else typer.colors.RED)
            )
            typer.secho(f"  {place:>12}  {r.true_venue[:44]:<46} {r.title[:48]}", fg=colour)
            if r.note:
                typer.echo(f"                {r.note}")

    typer.echo("\n" + json.dumps(summarise(results, top_n), indent=2, ensure_ascii=False))


@app.command("blind-sheet")
def blind_sheet_cmd(
    run_id: int = typer.Option(..., help="A consultation that already has a shortlist."),
    seed: int = typer.Option(..., help="Any integer. Write it down: scoring needs it."),
    out: Path = typer.Option(None, help="Where to write the sheet (default: stdout)."),
):
    """Phase 1b, measure 2: a sheet mixing the finalists with decoys, to judge blind.

    Costs nothing — the consultation already ran. The decoys are the control: if
    they are accepted as often as the finalists, the list added nothing, and no
    amount of agreement on the finalists alone would have shown it.
    """
    from .validation import blind_sheet, sheet_markdown

    db.init_engine()
    with db.session_scope() as s:
        run = s.get(MatchRun, run_id)
        if run is None:
            typer.secho(f"no consultation {run_id}", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        rows = blind_sheet(s, run, seed=seed)
        if not rows:
            typer.secho("that consultation has no shortlist to judge", fg=typer.colors.YELLOW)
            raise typer.Exit(code=1)
        text = sheet_markdown(rows, run, seed)
        decoys = sum(1 for r in rows if r.is_decoy)

    if out:
        out.write_text(text, encoding="utf-8")
        typer.echo(f"{out}: {len(rows)} rows, {decoys} of them decoys")
        typer.secho("Do not read the file's source. Fill the y/n column.", fg=typer.colors.YELLOW)
    else:
        typer.echo(text)


@app.command("score-sheet")
def score_sheet_cmd(
    run_id: int = typer.Option(...),
    seed: int = typer.Option(..., help="The same seed the sheet was built with."),
    marks: Path = typer.Option(..., help="The filled sheet, or a list like `1y 2n 3y`."),
):
    """Phase 1b, measure 2: score a filled sheet.

    Rebuilds the same shuffle from the same seed, so which rows were decoys is
    recovered rather than stored — nothing sitting on disk in the meantime can
    tell the judge the answer.
    """
    from .validation import blind_sheet, parse_marks, score_sheet

    db.init_engine()
    with db.session_scope() as s:
        run = s.get(MatchRun, run_id)
        if run is None:
            typer.secho(f"no consultation {run_id}", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        rows = blind_sheet(s, run, seed=seed)
    report = score_sheet(rows, parse_marks(marks.read_text(encoding="utf-8")))
    typer.echo(json.dumps(report, indent=2, ensure_ascii=False))


@app.command("check-negatives")
def check_negatives_cmd(
    run_id: int = typer.Option(..., help="The consultation for the paper in question."),
    cases: Path = typer.Option(
        ..., help='JSON: [{"paper": "...", "venue_name": "...", '
                  '"venue_openalex_id": "S123", "reason": "desk reject, out of scope"}]'
    ),
):
    """Phase 1b, measure 1: did the tool suggest a journal that then said no?

    The one direction of this validation nothing contaminates. A desk reject
    motivated on scope is the journal's own statement about fit — which is the
    thing the tool models — so a venue like that appearing in a shortlist is
    wrong in a way nobody has to interpret.

    Only cases rejected **on scope** belong in the file. A desk reject for length
    or format says nothing about fit and would make the number meaningless.
    """
    from .validation import NegativeCase, check_negatives, summarise_negatives

    db.init_engine()
    payload = json.loads(cases.read_text(encoding="utf-8"))
    with db.session_scope() as s:
        run = s.get(MatchRun, run_id)
        if run is None:
            typer.secho(f"no consultation {run_id}", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        results = list(
            s.scalars(select(MatchResult).where(MatchResult.run_id == run_id))
        )
        shortlisted = {r.venue_id for r in results if r.bucket == "shortlist"}
        reachable = {r.venue_id for r in results if r.score_topic > 0}
        outcomes = check_negatives(
            s,
            (run.text_profile or {}).get("topics") or [],
            [NegativeCase(**c) for c in payload],
            shortlisted,
            reachable,
        )

    for o in outcomes:
        colour = typer.colors.GREEN if o.caught else typer.colors.RED
        typer.secho(f"  {o.verdict:>12}  {o.venue_name[:44]:<46} {o.detail}", fg=colour)
    typer.echo("\n" + json.dumps(summarise_negatives(outcomes), indent=2, ensure_ascii=False))


@app.command("create-user")
def create_user(
    email: str = typer.Option(..., help="Sign-in address."),
    password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True),
    role: str = typer.Option("reader", help="reader or admin."),
    name: str = typer.Option(None),
):
    """Create a local user. Needed at least once: the web UI has no sign-up.

    Behind Borant ID people arrive through the gate and this is only the way back
    in when the gate is off — which is exactly why gateway-created users still
    get a local password.
    """
    from .auth import hash_password
    from .models import Role, User

    db.init_engine()
    with db.session_scope() as s:
        if s.scalar(select(User).where(User.email == email.strip().lower())):
            typer.secho(f"{email} already exists", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        u = User(
            email=email.strip().lower(),
            name=name or email,
            hashed_password=hash_password(password),
            role=Role(role),
        )
        s.add(u)
        s.flush()
        typer.echo(f"user #{u.id}: {u.email} · {u.role.value}")


@app.command("serve")
def serve(host: str = typer.Option("127.0.0.1"), port: int = typer.Option(8021)):
    """Run the web UI.

    Refuses to start without JWT_SECRET: a default secret is the same as no
    secret, because everyone has it.
    """
    import uvicorn

    from .auth import auth_mode, secret_key

    secret_key()
    typer.echo(f"auth mode: {auth_mode()}")
    uvicorn.run("dovetail.web:app", host=host, port=port)


@app.command("api-key")
def api_key(
    email: str = typer.Option(..., help="Whose key this is."),
    label: str = typer.Option(None, help="What it is for, e.g. 'ono desktop'."),
):
    """Issue an MCP key. Shown once and never again: it is stored hashed."""
    from . import apikeys
    from .models import User

    db.init_engine()
    with db.session_scope() as s:
        user = s.scalar(select(User).where(User.email == email.strip().lower()))
        if user is None:
            typer.secho(f"no user {email}", fg=typer.colors.RED)
            raise typer.Exit(code=1)
        key = apikeys.issue(s, user, label)
    typer.echo(f"key for {email} ({user.role.value}):\n\n  {key}\n")
    typer.secho(
        "Copy it now. Only a hash is stored, so nobody — including whoever runs "
        "the server — can show it to you again.",
        fg=typer.colors.YELLOW,
    )


# Must stay the last thing in this file. With commands defined below it, running
# the module as `python -m dovetail.cli` executes `app()` before they are
# registered and they simply do not exist — which is invisible through the
# console entry point, where the module is imported whole first, and shows up
# only inside the container, where DEPLOY.md says to use `python -m`.
if __name__ == "__main__":  # pragma: no cover
    app()
