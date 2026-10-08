"""Search, retrieval and evidence handling for MIND Research.

Retrieved content is untrusted data: it is stored and quoted, never executed or
treated as instructions. Fetching refuses private network addresses (SSRF) and
honours robots.txt.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import math
import re
import socket
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any, ClassVar
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

USER_AGENT = "MIND-AI-Research/0.1 (+https://github.com/nhqtr124-dot/MIND-AI)"
MAX_BYTES = 3_000_000


class ResearchError(Exception):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass
class SearchHit:
    url: str
    title: str
    snippet: str
    engine: str


@dataclass
class FetchedPage:
    url: str
    final_url: str
    title: str
    text: str
    content_type: str
    retrieved_at: str
    sha256: str
    status: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Passage:
    source_index: int
    text: str
    score: float


# ---------------------------------------------------------------------------- SSRF guard


def _is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


async def assert_public_url(url: str) -> None:
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise ResearchError("blocked", f"only http(s) URLs may be fetched, got '{p.scheme}'")
    if not p.hostname:
        raise ResearchError("blocked", "URL has no host")
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            p.hostname, p.port or (443 if p.scheme == "https" else 80)
        )
    except socket.gaierror as exc:
        raise ResearchError("dns", f"could not resolve {p.hostname}") from exc
    for info in infos:
        ip = str(info[4][0])
        if not _is_public_ip(ip):
            raise ResearchError(
                "blocked", f"{p.hostname} resolves to a non-public address ({ip}); refusing to fetch"
            )


# ---------------------------------------------------------------------------- HTML -> text


class _Readable(HTMLParser):
    SKIP: ClassVar[set[str]] = {
        "script",
        "style",
        "noscript",
        "nav",
        "footer",
        "header",
        "aside",
        "form",
        "svg",
        "iframe",
        "template",
    }
    BLOCK: ClassVar[set[str]] = {
        "p",
        "div",
        "li",
        "br",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "tr",
        "section",
        "article",
        "blockquote",
        "pre",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.out.append(data)


def html_to_text(markup: str) -> tuple[str, str]:
    p = _Readable()
    p.feed(markup)
    lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in "".join(p.out).splitlines()]
    text = "\n".join(ln for ln in lines if len(ln) > 1)
    return p.title.strip(), text


# ---------------------------------------------------------------------------- fetching


class Fetcher:
    def __init__(
        self, client: httpx.AsyncClient | None = None, check_ssrf: bool = True, respect_robots: bool = True
    ) -> None:
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(20.0), follow_redirects=False, headers={"User-Agent": USER_AGENT}
        )
        self.check_ssrf = check_ssrf
        self.respect_robots = respect_robots
        self._robots: dict[str, RobotFileParser | None] = {}

    async def allowed_by_robots(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        p = urlparse(url)
        origin = f"{p.scheme}://{p.netloc}"
        if origin not in self._robots:
            rp: RobotFileParser | None = RobotFileParser()
            try:
                if self.check_ssrf:
                    await assert_public_url(origin + "/robots.txt")
                r = await self.client.get(origin + "/robots.txt")
                if r.status_code in (401, 403):
                    rp = None  # robots unreadable due to auth: treat everything as disallowed (RFC 9309)
                elif r.status_code >= 400:
                    rp.parse([])  # no robots.txt: allowed
                else:
                    rp.parse(r.text.splitlines())
            except (httpx.HTTPError, ResearchError):
                rp.parse([])
            self._robots[origin] = rp
        rp = self._robots[origin]
        return rp is not None and rp.can_fetch(USER_AGENT, url)

    async def fetch(self, url: str, max_redirects: int = 5) -> FetchedPage:
        current = url
        for _ in range(max_redirects + 1):
            if self.check_ssrf:
                await assert_public_url(current)
            if not await self.allowed_by_robots(current):
                raise ResearchError("robots", f"robots.txt disallows fetching {current}")
            try:
                async with self.client.stream("GET", current) as r:
                    if r.is_redirect and r.headers.get("location"):
                        current = urljoin(current, r.headers["location"])
                        continue
                    if r.status_code >= 400:
                        raise ResearchError("http", f"HTTP {r.status_code} from {current}")
                    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                    body = b""
                    async for chunk in r.aiter_bytes():
                        body += chunk
                        if len(body) > MAX_BYTES:
                            raise ResearchError("too_large", f"{current} exceeds {MAX_BYTES} bytes")
                    status = r.status_code
                    encoding = r.encoding or "utf-8"
            except httpx.HTTPError as exc:
                raise ResearchError("network", f"could not fetch {current}: {type(exc).__name__}") from exc
            if ctype in ("text/html", "application/xhtml+xml", ""):
                title, text = html_to_text(body.decode(encoding, errors="replace"))
            elif ctype.startswith("text/") or ctype == "application/json":
                title, text = "", body.decode(encoding, errors="replace")
            else:
                raise ResearchError(
                    "unsupported", f"content type '{ctype}' is not supported for research extraction"
                )
            return FetchedPage(
                url=url,
                final_url=current,
                title=title or current,
                text=text,
                content_type=ctype or "text/html",
                retrieved_at=datetime.now(UTC).isoformat(),
                sha256=hashlib.sha256(body).hexdigest(),
                status=status,
            )
        raise ResearchError("redirects", f"too many redirects for {url}")


# ---------------------------------------------------------------------------- search engines


class SearchEngine:
    name: ClassVar[str]

    def __init__(
        self, api_key: str | None = None, base_url: str | None = None, client: httpx.AsyncClient | None = None
    ) -> None:
        self.api_key = api_key or ""
        self.base_url = base_url
        self.client = client or httpx.AsyncClient(timeout=20.0)

    async def search(self, query: str, count: int = 8) -> list[SearchHit]:  # pragma: no cover - interface
        raise NotImplementedError

    def _check(self, r: httpx.Response) -> Any:
        if r.status_code >= 400:
            raise ResearchError("search", f"{self.name} search failed with HTTP {r.status_code}")
        return r.json()


class BraveSearch(SearchEngine):
    name = "brave"

    async def search(self, query: str, count: int = 8) -> list[SearchHit]:
        r = await self.client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": str(min(count, 20))},
            headers={"X-Subscription-Token": self.api_key, "Accept": "application/json"},
        )
        data = self._check(r)
        return [
            SearchHit(h["url"], h.get("title", ""), h.get("description", ""), self.name)
            for h in (data.get("web") or {}).get("results", [])
        ]


class TavilySearch(SearchEngine):
    name = "tavily"

    async def search(self, query: str, count: int = 8) -> list[SearchHit]:
        r = await self.client.post(
            "https://api.tavily.com/search",
            json={"query": query, "max_results": min(count, 20)},
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        data = self._check(r)
        return [
            SearchHit(h["url"], h.get("title", ""), h.get("content", ""), self.name)
            for h in data.get("results", [])
        ]


class SearxngSearch(SearchEngine):
    """Self-hosted SearXNG instance (free); requires the JSON output format enabled in settings.yml."""

    name = "searxng"

    async def search(self, query: str, count: int = 8) -> list[SearchHit]:
        if not self.base_url:
            raise ResearchError("config", "SearXNG base URL is not configured")
        r = await self.client.get(
            f"{self.base_url.rstrip('/')}/search", params={"q": query, "format": "json"}
        )
        data = self._check(r)
        return [
            SearchHit(h["url"], h.get("title", ""), h.get("content", ""), self.name)
            for h in data.get("results", [])[:count]
        ]


ENGINES: dict[str, type[SearchEngine]] = {e.name: e for e in (BraveSearch, TavilySearch, SearxngSearch)}


# ---------------------------------------------------------------------------- evidence


_WORD = re.compile(r"\w+", re.UNICODE)
_STOP = set(
    "the a an and or of to in on for with is are was were be by as at from that this it its into than then".split()
)


def _terms(s: str) -> list[str]:
    return [w for w in _WORD.findall(s.casefold()) if w not in _STOP and len(w) > 1]


def split_passages(text: str, size: int = 700) -> list[str]:
    paras = [p.strip() for p in re.split(r"\n{1,}", text) if len(p.strip()) > 40]
    out: list[str] = []
    buf = ""
    for p in paras:
        if len(buf) + len(p) > size and buf:
            out.append(buf)
            buf = ""
        buf = f"{buf}\n{p}".strip()
    if buf:
        out.append(buf)
    return out


def rank_passages(query: str, sources: list[str], top_k: int = 8) -> list[Passage]:
    """BM25 ranking of passages across sources. Deterministic, no model needed."""
    docs: list[tuple[int, str, list[str]]] = []
    for i, text in enumerate(sources):
        for psg in split_passages(text):
            docs.append((i, psg, _terms(psg)))
    if not docs:
        return []
    q = _terms(query)
    n = len(docs)
    avgdl = sum(len(d[2]) for d in docs) / n
    df = Counter(t for d in docs for t in set(d[2]))
    k1, b = 1.5, 0.75
    scored = []
    for i, psg, terms in docs:
        tf = Counter(terms)
        s = 0.0
        for t in q:
            if t in tf:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(terms) / max(avgdl, 1)))
        if s > 0:
            scored.append(Passage(i, psg, round(s, 4)))
    scored.sort(key=lambda p: -p.score)
    return scored[:top_k]


@dataclass
class CitationReport:
    cited: list[int] = field(default_factory=list)
    invalid: list[int] = field(default_factory=list)
    unverified_quotes: list[str] = field(default_factory=list)
    uncited_paragraphs: int = 0

    @property
    def ok(self) -> bool:
        return not self.invalid and not self.unverified_quotes

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ok"] = self.ok
        return d


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold()


def verify_citations(report: str, sources: list[str]) -> CitationReport:
    """Check that [n] markers point at real sources and that quoted text appears verbatim in a cited source."""
    rep = CitationReport()
    refs = sorted({int(m) for m in re.findall(r"\[(\d{1,3})\]", report)})
    rep.cited = [r for r in refs if 1 <= r <= len(sources)]
    rep.invalid = [r for r in refs if not 1 <= r <= len(sources)]
    normed = [_norm(s) for s in sources]
    for para in [p for p in report.split("\n\n") if p.strip()]:
        markers = [int(m) for m in re.findall(r"\[(\d{1,3})\]", para)]
        if not markers and len(para) > 120 and not para.lstrip().startswith("#"):
            rep.uncited_paragraphs += 1
        for quote in re.findall(r"[\"“]([^\"”]{12,400})[\"”]", para):
            pool = [normed[m - 1] for m in markers if 1 <= m <= len(sources)] or normed
            if not any(_norm(quote) in s for s in pool):
                rep.unverified_quotes.append(quote[:120])
    return rep
