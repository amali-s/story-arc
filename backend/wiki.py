"""Wikipedia retrieval. Every search and fetch happens here, in code.

System One never searches. Per TypeSafe's "how to build with System One"
guidance ("Do not rely on knowledge stored in model weights when current
information can come from your own knowledge base"), this module retrieves the
text and questions.py only asks the model to judge what was retrieved.

Endpoints, verified against the live en.wikipedia.org API on 2026-09-19:

  search    action=query&list=search&srsearch=<q>&srnamespace=0
            -> query.search[] {pageid, title, snippet, wordcount}
  describe  action=query&prop=description&pageids=<ids>
            -> query.pages[] {pageid, description}   e.g. "1993 film by Steven Spielberg"
  sections  action=parse&pageid=<id>&prop=tocdata
            -> parse.tocdata.sections[] {line, index, anchor, hLevel}
            `prop=sections` still responds but the API returns the deprecation
            warning: '"prop=sections" has been deprecated. Please use
            "prop=tocdata" instead.'  tocdata returns no warning.
  section   action=parse&pageid=<id>&prop=text&section=<index>
            -> parse.text  (HTML; stripped to plain text below)

All responses use formatversion=2, which unwraps `{"*": "..."}` into plain
strings. prop=extracts (TextExtracts) is deliberately NOT used: its own docs
warn it returns intro-only by default, that exlimit>1 requires exintro, and
that plaintext mode can keep citations.

User-Agent follows https://foundation.wikimedia.org/wiki/Policy:User-Agent_policy
("<client>/<version> (<contact>) <library>/<version>"). Set WIKI_CONTACT in
.env to your own URL or email: the policy warns that generic agents such as
python-requests "may be blocked without notice".
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from html.parser import HTMLParser

# The TypeSafe SDK pins httpx2 (an API-compatible httpx fork); reuse it
# rather than adding a second HTTP stack.
import httpx2 as httpx

API_URL = "https://en.wikipedia.org/w/api.php"
ARTICLE_URL = "https://en.wikipedia.org/wiki/"
ATTRIBUTION = "Text from Wikipedia, licensed CC BY-SA 4.0."

CONTACT = os.environ.get("WIKI_CONTACT", "https://github.com/story-arc; set WIKI_CONTACT in .env")
USER_AGENT = f"StoryArc/0.2 ({CONTACT}) httpx2/{httpx.__version__}"

# Section headings that hold a plot, in preference order. The first match wins.
PLOT_SECTION_TITLES = (
    "plot",
    "plot summary",
    "synopsis",
    "summary",
    "plot synopsis",
    "story",
    "storyline",
    "plot overview",
    "narrative",
)

# Media kinds recognized in a result's short description or title. Matched
# longest-first, so "short story" wins over "story" and "graphic novel" over
# "novel". This is a vocabulary, not an ontology: it only has to be good enough
# to tell a novel from its film adaptation in a result list.
MEDIA_TYPES = (
    "short story", "fairy tale", "folk tale", "folktale", "graphic novel",
    "epic poem", "narrative poem", "television series", "tv series",
    "television film", "video game", "picture book", "stage play",
    "novella", "novel", "play", "poem", "sonnet", "epic", "tragedy", "comedy",
    "film", "miniseries", "series", "opera", "operetta", "musical", "ballet",
    "manga", "anime", "album", "song", "memoir", "biography", "book",
    "franchise", "legend", "myth", "fable", "anthology", "comic", "story",
)
_MEDIA_TYPES_BY_LENGTH = sorted(MEDIA_TYPES, key=len, reverse=True)
# Descriptions of things that are not works. An article about a character names
# the work it appears in ("Fictional character in the novel The Great Gatsby"),
# which would otherwise label Jay Gatsby a novel.
_NOT_A_WORK = (
    "character", "protagonist", "antagonist", "fictional", "sculpture",
    "statue", "settlement", "disambiguation", "given name", "surname",
)
# Four-digit years, 1000-2099. Publication years older than that are written
# differently on Wikipedia anyway ("c. 1200", "8th century").
_YEAR = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")

SEARCH_LIMIT = 8
REQUEST_TIMEOUT = 20.0
# Guardrail: a very long plot section costs tokens on every candidate request.
MAX_PLOT_CHARS = 30_000


class WikiError(RuntimeError):
    """Any failure talking to Wikipedia, or an article we cannot use."""


class PlotNotFound(WikiError):
    """The article has no plot-like section. Deliberately not a silent fallback."""

    def __init__(self, title: str, sections: list[str]) -> None:
        self.title = title
        self.sections = sections
        super().__init__(f"No plot section in {title!r}")


@dataclass
class SearchResult:
    pageid: int
    title: str
    description: str
    media_type: str
    year: str
    url: str

    def to_dict(self) -> dict:
        return {
            "pageid": self.pageid,
            "title": self.title,
            "description": self.description,
            "media_type": self.media_type,
            "year": self.year,
            "url": self.url,
        }


def classify(title: str, description: str) -> tuple[str, str]:
    """Best-effort (media type, year) for one search result.

    Both come from text already in hand - the short description and the
    title's disambiguating parenthetical - so this costs no extra API call.
    The UI only shows the compact form when a media type was found, so a lone
    year never appears on its own.
    Neither source alone is enough: "Jurassic Park" is described as "1993 film
    by Steven Spielberg" (both), while "The Little Mermaid (1989 film)" is
    described as "American animated film" and carries its year only in the
    title. Either value may come back empty; the UI falls back to the raw
    description when both do.
    """
    haystack = f"{description} {title}".lower()
    media_type = ""
    if not any(marker in haystack for marker in _NOT_A_WORK):
        media_type = next(
            (t for t in _MEDIA_TYPES_BY_LENGTH if re.search(rf"\b{re.escape(t)}\b", haystack)),
            "",
        )
    match = _YEAR.search(description) or _YEAR.search(title)
    return media_type, match.group(1) if match else ""


@dataclass
class Plot:
    pageid: int
    title: str
    url: str
    section: str
    text: str

    def to_dict(self) -> dict:
        return {
            "pageid": self.pageid,
            "title": self.title,
            "url": self.url,
            "section": self.section,
            "text": self.text,
            "attribution": ATTRIBUTION,
        }


def article_url(title: str) -> str:
    return ARTICLE_URL + title.replace(" ", "_")


def client() -> httpx.AsyncClient:
    """One client per request cycle. Calls are serial: four per story at most."""
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
    )


async def _get(http: httpx.AsyncClient, params: dict) -> dict:
    query = {"format": "json", "formatversion": "2", **params}
    try:
        response = await http.get(API_URL, params=query)
        response.raise_for_status()
        body = response.json()
    except httpx.HTTPError as exc:
        raise WikiError(f"Wikipedia request failed: {exc}") from exc
    except ValueError as exc:
        raise WikiError("Wikipedia returned a response that was not JSON.") from exc
    if "error" in body:
        info = body["error"].get("info", "unknown error")
        raise WikiError(f"Wikipedia API error: {info}")
    return body


# ---------------------------------------------------------------------------
# HTML -> plain text
# ---------------------------------------------------------------------------

_VOID_TAGS = {"br", "img", "hr", "input", "meta", "link", "wbr", "source", "col"}
# Whole subtrees that never belong in a plot summary.
_SKIP_TAGS = {"style", "script", "table", "figure", "sup", "audio", "video", "cite"}
_SKIP_CLASSES = (
    "mw-editsection",
    "reference",
    "navbox",
    "hatnote",
    "thumb",
    "metadata",
    "mbox",
    "infobox",
    "gallery",
    "noprint",
    "shortdescription",
)
_BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "dd", "dt", "tr"}

# Leftover citation markers such as [1], [a], [citation needed].
_BRACKETED = re.compile(r"\[\s*(?:\d+|[a-z]|citation needed|note \d+)\s*\]", re.I)


class _TextExtractor(HTMLParser):
    """Collects readable text, dropping whole subtrees we never want."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._stack: list[str] = []
        self._skip_depth = 0  # depth at which the current skipped subtree opened

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID_TAGS:
            if tag == "br" and not self._skip_depth:
                self._parts.append("\n")
            return
        self._stack.append(tag)
        if self._skip_depth:
            return
        classes = (dict(attrs).get("class") or "").lower()
        if tag in _SKIP_TAGS or any(c in classes for c in _SKIP_CLASSES):
            self._skip_depth = len(self._stack)

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS:
            return
        if tag in self._stack:
            while self._stack:
                if self._stack.pop() == tag:
                    break
        if self._skip_depth and len(self._stack) < self._skip_depth:
            self._skip_depth = 0
            return
        if not self._skip_depth and tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    raw = _BRACKETED.sub("", parser.text())
    lines = [" ".join(line.split()) for line in raw.split("\n")]
    return "\n\n".join(line for line in lines if line)


def _normalize_heading(line: str) -> str:
    return " ".join(re.sub(r"\[.*?\]", "", line).split()).strip().lower()


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------


async def search(http: httpx.AsyncClient, query: str, limit: int = SEARCH_LIMIT) -> list[SearchResult]:
    """Top article matches, with short descriptions so a novel and its film adaptation are told apart."""
    body = await _get(
        http,
        {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srnamespace": "0",
            "srlimit": str(limit),
        },
    )
    hits = (body.get("query") or {}).get("search") or []
    if not hits:
        return []

    ids = [str(h["pageid"]) for h in hits]
    descriptions: dict[int, str] = {}
    try:
        desc_body = await _get(http, {"action": "query", "prop": "description", "pageids": "|".join(ids)})
        for page in (desc_body.get("query") or {}).get("pages") or []:
            if page.get("description"):
                descriptions[page["pageid"]] = page["description"]
    except WikiError:
        pass  # Descriptions are a nicety; a search without them still works.

    results = []
    for hit in hits:
        description = descriptions.get(hit["pageid"], "")
        media_type, year = classify(hit["title"], description)
        results.append(
            SearchResult(
                pageid=hit["pageid"],
                title=hit["title"],
                description=description,
                media_type=media_type,
                year=year,
                url=article_url(hit["title"]),
            )
        )
    return results


async def sections(http: httpx.AsyncClient, pageid: int) -> list[dict]:
    body = await _get(http, {"action": "parse", "pageid": str(pageid), "prop": "tocdata"})
    parse = body.get("parse") or {}
    # An article with no headings returns tocdata as null, not an empty object.
    return (parse.get("tocdata") or {}).get("sections") or []


def pick_plot_section(section_list: list[dict]) -> dict | None:
    """First section whose heading matches PLOT_SECTION_TITLES, in preference order."""
    by_heading = {}
    for section in section_list:
        heading = _normalize_heading(section.get("line", ""))
        by_heading.setdefault(heading, section)
    for wanted in PLOT_SECTION_TITLES:
        if wanted in by_heading:
            return by_heading[wanted]
    return None


async def fetch_plot(http: httpx.AsyncClient, pageid: int) -> Plot:
    """The article's plot section as plain text, or PlotNotFound.

    There is no fallback to other sections: a poem with only 'Structure' and
    'Analysis' should say so rather than quietly charting literary criticism.
    """
    section_list = await sections(http, pageid)
    chosen = pick_plot_section(section_list)
    if chosen is None:
        body = await _get(http, {"action": "parse", "pageid": str(pageid), "prop": "tocdata"})
        title = (body.get("parse") or {}).get("title") or str(pageid)
        raise PlotNotFound(title, [s.get("line", "") for s in section_list])

    body = await _get(
        http,
        {
            "action": "parse",
            "pageid": str(pageid),
            "prop": "text",
            "section": str(chosen["index"]),
            "disabletoc": "1",
            "disableeditsection": "1",
        },
    )
    parse = body.get("parse") or {}
    title = parse.get("title") or str(pageid)
    text = html_to_text(parse.get("text") or "")

    # The section's own heading comes back as the first line; drop it.
    heading = _normalize_heading(chosen.get("line", ""))
    paragraphs = text.split("\n\n")
    if paragraphs and _normalize_heading(paragraphs[0]) == heading:
        paragraphs = paragraphs[1:]
    text = "\n\n".join(paragraphs).strip()

    if not text:
        raise PlotNotFound(title, [s.get("line", "") for s in section_list])
    if len(text) > MAX_PLOT_CHARS:
        text = text[:MAX_PLOT_CHARS].rsplit(" ", 1)[0] + "…"

    return Plot(
        pageid=pageid,
        title=title,
        url=article_url(title),
        section=chosen.get("line", "Plot"),
        text=text,
    )
