"""Fetching one page of author guidelines, and saying plainly when it cannot.

Stage 5b reads a page a person pointed at. It does not crawl, and it does not
pretend to be a browser: the request says what it is. That has a known cost,
measured by hand on 8 Sep 2026 — Wiley, OUP and BMJ answer **403** to a plain
fetch, Springer and PDF guidelines do not — so this fails by publisher rather
than at random, and the failure is a code the caller can act on: read the page
some other way and hand the text in.

Two things it refuses on purpose.

**A host that is not on the public internet.** The URL comes from whoever calls
the tool and the fetch runs on a box whose other applications listen on
loopback. Every hop of a redirect is checked, not only the first.

**A PDF.** Extracting text from one needs a dependency this container does not
carry, and a half-extracted table of word limits is worse than none.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

USER_AGENT = "dovetail/0.1 (+https://dovetail.borant.eu; reads author guidelines on request)"
MAX_REDIRECTS = 5
# Above this the page is not a guidelines page, it is a site. Refused rather
# than cut: the limits are as likely to be in the half that was dropped.
MAX_PAGE_CHARS = 200_000


class GuidelinesError(RuntimeError):
    """Carries a code, because the caller picks the sentence."""

    code = "guidelines_error"

    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class Blocked(GuidelinesError):
    """The publisher refused an automated request. Not a bug and not transient:
    the page has to be read by a person or a browser and passed in as text."""

    code = "blocked_by_publisher"


class Unsupported(GuidelinesError):
    code = "unsupported_page"


class Unreachable(GuidelinesError):
    code = "unreachable"


def _check_public(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise Unsupported(f"not an http(s) address: {url!r}")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise Unreachable(f"{parts.hostname} does not resolve: {e}") from e
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise Unsupported(
                f"{parts.hostname} resolves to {address}, which is not a public "
                f"address. Guidelines are fetched from the open web only."
            )


def fetch_text(url: str, client: httpx.Client | None = None) -> str:
    """The readable text of one guidelines page."""
    owned = client is None
    client = client or httpx.Client(timeout=30.0, follow_redirects=False)
    try:
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            _check_public(current)
            try:
                r = client.get(current, headers={"User-Agent": USER_AGENT})
            except httpx.HTTPError as e:
                raise Unreachable(f"{urlsplit(current).hostname}: {type(e).__name__}: {e}") from e
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                current = urljoin(current, r.headers["location"])
                continue
            break
        else:
            raise Unreachable(f"more than {MAX_REDIRECTS} redirects from {url}")
    finally:
        if owned:
            client.close()

    host = urlsplit(current).hostname
    if r.status_code in (401, 403, 429):
        raise Blocked(f"{host} answered {r.status_code} to an automated request")
    if r.status_code >= 400:
        raise Unreachable(f"{host} answered {r.status_code}")

    kind = (r.headers.get("content-type") or "").lower()
    if "pdf" in kind or r.content[:5] == b"%PDF-":
        raise Unsupported(
            "the page is a PDF. Its text has to be extracted elsewhere and passed in."
        )
    if "html" not in kind and "text/plain" not in kind:
        raise Unsupported(f"content type {kind or '(none)'} is not a page of text")

    text = r.text if "text/plain" in kind else html_to_text(r.text)
    return _checked_length(text)


def _checked_length(text: str) -> str:
    if len(text) > MAX_PAGE_CHARS:
        raise Unsupported(
            f"the page is {len(text):,} characters, above the {MAX_PAGE_CHARS:,} this "
            f"reads in one go. Pass the section that holds the article types as text."
        )
    return text


def clean_text(text: str) -> str:
    """Text handed in by the caller, held to the same limit as a fetched page."""
    return _checked_length(_collapse(text))


_SKIP = {"script", "style", "noscript", "svg", "template", "head"}
_BLOCK = {
    "p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article",
    "h1", "h2", "h3", "h4", "h5", "h6", "dt", "dd", "header", "footer", "main",
}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP:
            self._skipping += 1
        elif tag in _BLOCK:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            # A table of article types is the commonest shape of this page, and
            # run together its cells read as one number followed by another.
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self._skipping = max(0, self._skipping - 1)
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skipping:
            # A newline in the source is whitespace, as it is to a browser. Left
            # in, a table row indented one cell per line comes out as one line
            # per cell, and a limit is separated from the type it belongs to.
            self.parts.append(re.sub(r"\s+", " ", data))


def html_to_text(html: str) -> str:
    parser = _Text()
    parser.feed(html)
    parser.close()
    return _collapse("".join(parser.parts))


def _collapse(text: str) -> str:
    text = text.replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()
