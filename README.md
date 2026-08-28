# Dovetail

Finding the right journal for a paper, starting from title, abstract and word count.

The name is the dovetail joint: it holds because the two shapes match, not because it is glued.

**Status: Phase 1 in progress.** Schema, OpenAlex and DOAJ ingestion, matcher stages 1 to 4,
seeding from the PaperTrail vocabulary, CLI. No UI and no MCP yet: those are Phases 2 and 3.

The spec is in [SPEC.md](SPEC.md) — written in Italian, as the design document — and §17 records
what changed after the adversarial review.

## What it does

Given a manuscript it produces a list of candidate venues, each with its article type,
word limit, open access status against the funder, and the criteria that hold it up **labelled as
merit or logistics**. A venue standing on fewer than two merit criteria comes out in red, however
well it does on logistics — that rule comes from a post-mortem, not from taste.

It sits beside PaperTrail without overlapping it: PaperTrail knows where a paper *is* and where it
has already been, Dovetail knows where it *could* go. Dovetail reads PaperTrail and never writes
to it.

## Getting started

```bash
uv sync --extra dev
uv run dovetail init-db
uv run dovetail budget          # how many OpenAlex credits are left today
uv run dovetail seed            # resolve PaperTrail's venues, file alias proposals
uv run dovetail proposals       # the queue: nothing becomes an alias without approval
uv run dovetail match --title "..." --abstract-file abstract.txt --funder snsf
uv run pytest
```

A text profile can be reused with `--profile file.json`, which skips stage 1 and its hundred
credits; `import-source` loads an already downloaded `/sources` response, so you can work without
spending.

### Journals no index knows about

```bash
uv run dovetail add-venue --name "Future of Science and Ethics" \
    --publisher "Fondazione Umberto Veronesi" --oa --no-doaj --anvur "11/C3:A"
uv run --with pymupdf python scripts/pdf_to_articles.py article*.pdf > articles.json
uv run dovetail profile-venue --name "Future of Science and Ethics" --articles articles.json
```

Until it has a profile such a venue comes out under **UNCLASSIFIABLE** rather than being silently
dropped: there, a zero means "I don't know", not "out of scope".

## Phase 0 validation

The central hypothesis was tested on a real case before any code was written:

```bash
node validation/retrodict-case.mjs --mailto you@example.org
```

Two of the case's venues are **not reachable at stage 2**: they share no topic with the text.
That validates candidate generation. The scope score, on the other hand, **remains unvalidated** —
the case contains no positive outcome, so it can show that low scores track venues that said no,
never that high scores track venues that said yes. Full limits in SPEC.md §2.

The case itself is **not in this repository**: it is an unpublished manuscript and the record of
which journals turned it down, which is not ours to publish. The script reads a gitignored
`validation/case.local.json` and falls back to a made-up `case.example.json`. See SPEC.md §0b.

The text profile is cached on disk: `/text/topics` costs 100 OpenAlex credits against 1 for
`/sources`, and the anonymous daily budget is 1000. A free account key makes it ten times that.

## Stack

FastAPI + SQLite, single workspace, standalone or behind Borant ID, deployed on borant at
`dovetail.borant.eu`, port 8021 (8015 turned out to be GrantRadar's).
