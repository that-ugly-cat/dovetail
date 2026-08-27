#!/usr/bin/env python
"""From article PDFs to the JSON `dovetail profile-venue` expects.

Many of the journals no index covers publish **PDFs only**: no API, no
structured HTML, the abstract sitting inside the layout. This pulls the title and
abstract off the first page.

    uv run --with pymupdf python scripts/pdf_to_articles.py PDF... > articles.json
    uv run --with pymupdf python scripts/pdf_to_articles.py --masthead "theFuture|ofScience andEthics" PDF...

It prefers the **English** abstract when the article is bilingual, because
OpenAlex's classifier is trained mostly on English and an Italian abstract yields
a thinner set of topics.

It does not guess: a PDF whose abstract cannot be isolated is reported as
skipped, with the reason, on stderr. Nine good articles beat ten of which one is
invented.
"""

from __future__ import annotations

import json
import re
import sys

try:
    import pymupdf  # the `fitz` alias is deprecated and prints a warning on
                    # stdout, which alone is enough to corrupt the JSON below
except ImportError:  # pragma: no cover
    sys.exit("needs pymupdf: uv run --with pymupdf python scripts/pdf_to_articles.py ...")

# Running heads and page furniture to drop before looking for the title.
NOISE = re.compile(
    r"^\s*(\d{1,4}|Volume\s+\d+.*|DOI[:\s].*|ISSN.*|https?://\S*)\s*$",
    re.IGNORECASE,
)

# Where the title ends and the apparatus begins. The title sits at the top;
# everything from here on — authors, affiliations, emails, the call theme — is
# not the title, and feeding it to the classifier alongside the abstract adds
# noise: "Institute for Technology & Global Health, Boston" says nothing about
# what the journal publishes.
END_OF_TITLE = re.compile(
    r"(@|^AFFILIAZIONE|^AFFILIATION|^Call\s+for\s+paper|^Dipartimento|^Department"
    r"|^Universit|^Istituto|^Institute|^\d\.\s|^ARTICOLI|^RECENSIONI|^ARTICLES)",
    re.IGNORECASE,
)
# Author name in small caps, the way many journals set them.
AUTHOR_IN_CAPS = re.compile(r"^[A-ZÀ-Ü][A-ZÀ-Ü\s.'’-]{5,}$")
ABSTRACT_START = re.compile(r"^\s*(ABSTRACT|SOMMARIO|RIASSUNTO)\s*$", re.IGNORECASE)
ABSTRACT_END = re.compile(
    r"^\s*(PAROLE\s+CHIAVE|KEYWORDS|KEY\s+WORDS|PAROLE-CHIAVE)\b", re.IGNORECASE
)


def masthead_pattern(fragment: str | None) -> re.Pattern | None:
    """A masthead can break across two lines in ways a line-by-line filter never
    catches («theFuture» / «ofScience andEthics»). Stripping it from the already
    assembled string is more robust than guessing where it wraps."""
    if not fragment:
        return None
    return re.compile(rf"^\s*(?:{fragment}|Volume\s*\d+)\s*", re.IGNORECASE)


def clean_title(title: str, masthead: re.Pattern | None) -> str:
    if masthead is None:
        return title
    previous = None
    while previous != title:
        previous = title
        title = masthead.sub("", title).strip()
    return title


def dehyphenate(text: str) -> str:
    """The layout breaks words at line ends: `ethi-\\ncal` becomes `ethical`."""
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
    return re.sub(r"\s*\n\s*", " ", text).strip()


def abstract_blocks(lines: list[str]) -> list[tuple[int, int]]:
    """(start, end) pairs for the blocks following an abstract marker."""
    found = []
    for i, line in enumerate(lines):
        if ABSTRACT_START.match(line):
            end = len(lines)
            for j in range(i + 1, len(lines)):
                if ABSTRACT_END.match(lines[j]) or ABSTRACT_START.match(lines[j]):
                    end = j
                    break
            found.append((i, end))
    return found


def looks_english(text: str) -> bool:
    """Crude, but enough to pick between two abstracts."""
    english = sum(text.lower().count(w) for w in (" the ", " this ", " and ", " of ", " that "))
    italian = sum(
        text.lower().count(w) for w in (" della ", " che ", " degli ", " nella ", " una ")
    )
    return english > italian


def extract(path: str, masthead: re.Pattern | None) -> dict | None:
    doc = pymupdf.open(path)
    text = doc[0].get_text()
    if len(doc) > 1 and "ABSTRACT" not in text.upper():
        text += "\n" + doc[1].get_text()
    doc.close()

    lines = [line for line in text.splitlines() if not NOISE.match(line)]
    blocks = abstract_blocks(lines)
    if not blocks:
        return None

    # The title is read **from the top**, not backwards from the abstract. Read
    # backwards you land inside the author and affiliation block, which in the
    # layout sits right in between. From the top you stop at the first sign of
    # apparatus.
    #
    # In bilingual PDFs the two titles sit one under the other and both are kept:
    # cutting it in half risks throwing away the English one, which is the one
    # the classifier understands best.
    title_lines: list[str] = []
    for line in lines[: blocks[0][0]]:
        line = line.strip()
        if not line:
            continue
        if END_OF_TITLE.search(line) or AUTHOR_IN_CAPS.match(line):
            break
        title_lines.append(line)
    title = clean_title(dehyphenate(" ".join(title_lines)), masthead)

    candidates = [dehyphenate("\n".join(lines[i + 1 : j])) for i, j in blocks]
    candidates = [c for c in candidates if len(c.split()) >= 40]
    if not candidates:
        return None

    english = [c for c in candidates if looks_english(c)]
    abstract = (english or candidates)[0]
    return {"title": title, "abstract": abstract, "source": path}


def main() -> None:
    # On Windows Python's stdout uses the console code page, not UTF-8: a
    # redirect to file would write accented characters as cp1252 and the JSON
    # would come back unreadable to anyone parsing it as UTF-8.
    sys.stdout.reconfigure(encoding="utf-8")

    args = sys.argv[1:]
    masthead_fragment = None
    if args and args[0] == "--masthead":
        masthead_fragment = args[1]
        args = args[2:]
    if not args:
        sys.exit(__doc__)

    masthead = masthead_pattern(masthead_fragment)
    kept, skipped = [], []
    for path in args:
        try:
            article = extract(path, masthead)
        except Exception as e:  # unreadable, protected or scanned PDF
            skipped.append({"file": path, "reason": f"{type(e).__name__}: {e}"})
            continue
        if article is None:
            skipped.append({"file": path, "reason": "no abstract could be isolated"})
        else:
            kept.append(article)

    for s in skipped:
        print(f"skipped {s['file']}: {s['reason']}", file=sys.stderr)
    print(f"{len(kept)} articles, {len(skipped)} skipped", file=sys.stderr)
    json.dump(kept, sys.stdout, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
