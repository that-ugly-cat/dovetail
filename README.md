# Dovetail

Finding the right journal for a paper, starting from title, abstract and word count.

The name is the dovetail joint: it holds because the two shapes match, not because it is glued.

**Status: live** at [dovetail.borant.eu](https://dovetail.borant.eu), behind Borant ID. Schema, OpenAlex and DOAJ ingestion, matcher stages 1 to 4,
seeding from the PaperTrail vocabulary, a CLI, an MCP surface behind per-user API keys, and a web UI
with two roles. Deployed on borant at port 8021, standalone or behind Borant ID.

The spec is in [SPEC.md](SPEC.md) — written in Italian, as the design document — and §17 records
what changed after the adversarial review.

## The web UI

`/` is a public front page that **never looks at who is reading it**, with one button into the app
at `/app`. That shape is shared with the other borant tools, and the reason is not tidiness: on the
gateway's public branch the identity headers are stripped by construction, so a front page that
consulted the user would be always-logged-out behind the gate and sometimes-logged-in standalone —
one page with two behaviours, and the difference invisible to every test that runs locally.

Inside, five screens and two roles. A **reader** looks; an **admin** starts consultations, declares
journals by hand, and turns queue entries into facts. The split is by what a thing *costs*, not by
seniority — and it is enforced in a dependency, never in a template, because hiding a button while
leaving the route open is a decoration over a permission.

Everything that spends says how much **before** the button, from the rate-limit figures OpenAlex
publishes: a cost seen afterwards is not a decision. A consultation started here answers before it
has finished — the sweep takes the better part of a minute — so its page says `running` and reloads
itself until the row says otherwise.

## What it does

Given a manuscript it produces a list of candidate venues, each with its article type,
word limit, open access status against the funder, and the criteria that hold it up **labelled as
merit or logistics**. A venue standing on fewer than two merit criteria comes out in red, however
well it does on logistics — that rule comes from a post-mortem, not from taste.

Give it the **reference list** too, pasted as it is, and it takes a second road to a candidate. The
topics classify a text by its object, so a normative paper about a technology comes back with the
journals that build the technology. With the references Dovetail also asks where the works the
manuscript cites are themselves being cited, and lists those journals apart. See below, and SPEC.md
§6b.

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
uv run dovetail match --title "..." --abstract-file abstract.txt --references-file refs.txt
uv run pytest                   # 220 tests
```

The topic sweep pages through OpenAlex with a cursor, and **anonymous cursor paging answers 401**:
without `OPENALEX_API_KEY` in `.env`, run `match` with `--no-discover`. The reference list works
either way. In the web app and over MCP a sweep with no key anywhere is refused before it spends
anything, with the place to get one.

### Whose consultations

Journals are shared by everyone on an instance. **A consultation is seen only by whoever started
it** — not by other members and not by admins, who get a count and a credit total per person and
nothing else. It holds the title and the bibliography of a manuscript that is not published. Its
owner can delete it; the journals it found stay.

Anyone with their own OpenAlex key can start one. An Anthropic key is only needed for the check on
the kind of paper. A consultation started from the command line has no owner and is seen by admins.
The rules are in `src/dovetail/ownership.py`.

### Whose OpenAlex budget

Each person can store **their own OpenAlex key** at `/app/settings` (the «?» there says how to get
one: it is free). Their consultations then spend that key's daily budget of 10,000 credits, on a
ledger of their own, and not the budget everybody on the instance shares. The key is tried against
OpenAlex before it is kept, stored encrypted, and never shown again. `OPENALEX_API_KEY` in `.env`
stays what everyone without a key of their own falls back to.

A text profile can be reused with `--profile file.json`, which skips stage 1 and its hundred
credits; `import-source` loads an already downloaded `/sources` response, so you can work without
spending.

### What a consultation costs, before you start one

A ceiling and not a bill, computed from the rate-limit figures OpenAlex publishes:

| | |
|---|---|
| classifying the manuscript | 100 credits |
| sweeping for candidates | ≤ 25 |
| journals that publish on the subject as a sideline | 10 |
| fetching the records those groups name | ≤ 2 |
| enriching the finalists from DOAJ | free |
| **at most** | **137** |

A real run on 28 Aug 2026 spent 133. The first version of this table said 125 and was wrong: it
priced the paginated sweep and neither of the other two calls stage 2 makes. **An estimate that is
under is worse than none, because it is the one people believe** — so the terms now live beside the
calls that generate them, and a test reads the source of `generate_candidates` and fails if it calls
anything the estimate does not price.

### Five baskets, not one list

`cut` returns three, the reference list adds two, and they are kept apart everywhere:

- **shortlist** — scored, passed the constraints, inside the cut of twelve.
- **excluded** — a constraint removed it, shown anyway when fewer than three passed. An empty list
  is not an answer.
- **unclassifiable** — no profile, so no score applies. **Its zero means «I don't know», not «out of
  scope»**, and it is not numbered among the others.
- **neighbourhood** — reached through the reference list: journals whose recent papers cite the
  works the manuscript cites, through at least 5% of the references, densest first. Citation density
  is not the shortlist's axis, so these are numbered on their own. A shortlisted venue the references
  also reach stays in the shortlist and says so.
- **conversation** — the same count inside one minority field of the references at a time. A
  bibliography is mostly about the paper's subject, and the journals where its argument belongs are
  reached through the few references that are not.

### The citation neighbourhood, and what it is not

It describes the **bibliography**, not the argument. Measured on 20 Nov 2026 on two real papers:

- 55 references, a conceptual paper: the two specialist journals of its own philosophical subfield
  came first, where the topic channel had returned the journals of the technology it discusses.
  163 credits.
- 89 references, 73 of them in one field, a paper whose argument belongs to another: the overall
  list is that field's journals, and the journals of the argument come out of the minority fields.
  197 credits.

The journals the references bring into the table are scored by the topic channel too, which is how
one of the journals the authors had picked by hand entered the second paper's shortlist.

Both manuscripts are unpublished, so neither is named here or anywhere in this repository (SPEC.md
§0b).

## Stage 5b — what the guidelines say about length

```bash
uv run dovetail read-guidelines --venue-id 12 --url https://.../submission-guidelines
uv run dovetail read-guidelines --venue-id 12 --url https://... --text-file page.txt
```

Reads one page of author guidelines into article types with their limits, and files a proposal.
**Every limit is checked against the page**: it is kept only if the sentence quoted for it is on the
page and the number is in the sentence. **A word limit never comes without what it counts** — 8,000
including references and 9,000 excluding them give opposite verdicts on one manuscript.

Some publishers refuse an automated request (Wiley did on 20 Nov 2026, Springer did not). That comes
back as `blocked_by_publisher`: read the page yourself and pass the text in. Approving the proposal
in the web app is what writes the article types; until 20 Nov 2026 nothing did.

**Not yet run against a real model.** The tests use a fake client.

### Journals no index knows about

```bash
uv run dovetail add-venue --name "Future of Science and Ethics" \
    --publisher "Fondazione Umberto Veronesi" --oa --no-doaj --anvur "11/C3:A"
uv run --with pymupdf python scripts/pdf_to_articles.py article*.pdf > articles.json
uv run dovetail profile-venue --name "Future of Science and Ethics" --articles articles.json
```

Both are also in the web UI. A declared journal arrives **unclassifiable** and stays that way until
it has a profile built from articles it actually published — five to ten from different years, at
100 credits each. Ten from one special issue describe that issue, not the journal.

## Stage 5a — does this journal publish work of this *kind*?

Scope says what a paper is **about**; genre says what **shape** it is. An empirical study and a
conceptual essay on the same subject score identically on the cosine and belong in different
journals. It runs on the finalists only, and **never reorders**: a positive verdict becomes a
criterion of merit, a negative one stays a flag, and nothing moves — a judgement that is not
reproducible cannot order a list that has to be explainable.

It needs your own Anthropic key, stored encrypted per user at `/app/settings`, because that call is
charged to a person rather than to a shared budget. Set `FERNET_KEY` on the server to enable it;
without one the feature is off and everything through stage 4 works unchanged. The same key pays
for stage 5b, and the same `FERNET_KEY` is what lets a personal OpenAlex key be stored.

```bash
uv run dovetail judge-venues --run-id 3 --issn 1472-6939 --as-user you@example.org
```

That command judges journals you name rather than the shortlist — diagnostics, for the question the
product cannot ask: *what does the judgement say about a journal the matcher never suggested?*

**It was built on a claim that turned out to be false.** The claim was that stage 5a is «the
criterion the two desk rejects of 2026 were missing». Asked about those two venues it answers *same
kind*, both times, with high confidence and correctly — they publish exactly this form. Those
rejections were about subject, which stage 2 already caught by never producing either venue. What it
*is* good for, from the same run: ten of twelve finalists the same kind, two not — a philosophy
journal and a laboratory immunology one, both sitting in the shortlist with three merit criteria and
neither reachable by any score the tool computes. See SPEC.md §16e.

## Validation

Two measures, and neither needs the tool to be a ranking. The first design — *where does the journal
that really published this paper rank?* — is **retired**: it measures a rank, which SPEC §0 says
this output is not, and it compares two criteria rather than one criterion against truth. See
SPEC.md §16d.

### Known negatives — 4/4, all unreachable

```bash
uv run dovetail check-negatives --run-id 1 --cases negatives.local.json
```

The clean ground truth is not «this journal said yes», which relationships and invitations
contaminate. It is **«this journal said no, out of scope»** — the journal's own statement about the
thing the tool models. Run over everything the corpus contains: three papers, four rejections where
the venue itself named scope, and none of the four would ever have been suggested.

Four is not a sample, it is the whole corpus — and the reason is not about journals. Of 53
rejections recorded in PaperTrail only 6 carry any reason from the venue, because there is no reason
field on a submission. **The bottleneck for validating this tool is a missing field in a different
one.** SPEC.md §16f.

### Precision, judged blind — no number yet

```bash
uv run dovetail blind-sheet --run-id 1 --seed 12345 --out sheet.md
uv run dovetail score-sheet  --run-id 1 --seed 12345 --marks sheet.md
```

Finalists shuffled with decoys drawn from the same subfield, stripped of score, position and
criteria. When the run had a reference list, both channels are on the sheet and each is scored
against the same decoys. The key is stored when the sheet is built: scoring used to rebuild the
shuffle from the seed, and a sweep in between changed which journals the row numbers stood for. **The decoy rate is the finding, not the precision**: a judge who accepts everything
scores twelve out of twelve, and only the control shows it. Costs nothing and needs an hour of
somebody's judgement.

## Phase 0 validation

```bash
node validation/retrodict-case.mjs --mailto you@example.org
```

The case itself is **not in this repository**: it is an unpublished manuscript and the record of
which journals turned it down, which is not ours to publish. The script reads a gitignored
`validation/case.local.json` and falls back to a made-up `case.example.json`. See SPEC.md §0b.

## Stack

FastAPI + SQLite, single workspace, standalone or behind Borant ID, deployed on borant at
`dovetail.borant.eu`, port 8021 (8015 turned out to be GrantRadar's). `SPEC.md` is the design
document and holds the reasoning; `DEPLOY.md` is the server.
