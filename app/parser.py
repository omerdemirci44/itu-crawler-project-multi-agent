"""HTML parsing and URL normalization helpers for the crawler MVP.

This module stays intentionally small and standard-library only. It handles:

- practical URL normalization for crawl candidates
- anchor link extraction with stable ordering
- conservative visible-text extraction
- title extraction from HTML documents

There is no fetch logic and no crawler coordination in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re
from typing import Optional
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

_SUPPORTED_SCHEMES = {"http", "https"}
_SKIPPED_SCHEMES = {"javascript", "mailto", "tel"}
_IGNORED_TEXT_TAGS = {"script", "style", "noscript", "template"}
_WHITESPACE_RE = re.compile(r"\s+")
_PUNCTUATION_SPACE_RE = re.compile(r"\s+([,.;:!?])")


@dataclass(frozen=True, slots=True)
class ParsedPage:
    """Structured result for one parsed HTML document.

    Attributes:
        url: Normalized base URL for the page when available.
        title: Extracted page title, or ``None`` when missing.
        text: Conservatively extracted visible text.
        links: Normalized outgoing links in stable discovery order.
    """

    url: Optional[str]
    title: Optional[str]
    text: str
    links: list[str]


def _collapse_whitespace(value: str) -> str:
    """Collapse repeated whitespace and strip leading/trailing space."""

    return _WHITESPACE_RE.sub(" ", value).strip()


def _normalize_text_output(value: str) -> str:
    """Normalize extracted text while keeping the rules simple and stable."""

    collapsed = _collapse_whitespace(value)
    return _PUNCTUATION_SPACE_RE.sub(r"\1", collapsed)


def _rebuild_netloc(parts) -> Optional[str]:
    """Rebuild ``netloc`` with normalized host casing and simple port cleanup."""

    hostname = parts.hostname
    if not hostname:
        return None

    hostname = hostname.lower()

    try:
        port = parts.port
    except ValueError:
        return None

    if (parts.scheme == "http" and port == 80) or (parts.scheme == "https" and port == 443):
        port = None

    userinfo = ""
    if parts.username is not None:
        userinfo = parts.username
        if parts.password is not None:
            userinfo += f":{parts.password}"
        userinfo += "@"

    # ``SplitResult.hostname`` drops IPv6 brackets, so restore them for round-tripping.
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"

    if port is None:
        return f"{userinfo}{hostname}"
    return f"{userinfo}{hostname}:{port}"


def normalize_url(url: str, base_url: str | None = None) -> str | None:
    """Normalize a crawl candidate URL.

    Practical normalization rules for the MVP:

    - strip surrounding whitespace
    - resolve relative links against ``base_url`` when provided
    - drop fragments
    - normalize scheme and host case
    - remove default ports for HTTP/HTTPS
    - ensure root URLs use ``/`` as the path

    The helper intentionally rejects unsupported or obviously non-crawlable
    schemes such as ``javascript:``, ``mailto:``, and ``tel:``.
    """

    if not isinstance(url, str):
        return None

    candidate = url.strip()
    if not candidate or candidate.startswith("#"):
        return None

    lowered_candidate = candidate.lower()
    if any(lowered_candidate.startswith(f"{scheme}:") for scheme in _SKIPPED_SCHEMES):
        return None

    resolved_base = None
    if base_url:
        resolved_base = normalize_url(base_url)
        if resolved_base is None:
            resolved_base = base_url.strip() or None

    if resolved_base:
        candidate = urljoin(resolved_base, candidate)

    candidate, _fragment = urldefrag(candidate)
    parts = urlsplit(candidate)

    scheme = parts.scheme.lower()
    if scheme not in _SUPPORTED_SCHEMES:
        return None

    netloc = _rebuild_netloc(parts)
    if not netloc:
        return None

    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


class _DocumentParser(HTMLParser):
    """One-pass HTML parser for links, title, and conservative visible text."""

    def __init__(self, base_url: str | None) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self._ignored_depth = 0
        self._in_head = False
        self._in_title = False
        self._title_chunks: list[str] = []
        self._text_chunks: list[str] = []
        self._links: list[str] = []
        self._seen_links: set[str] = set()

    @property
    def title(self) -> str | None:
        """Return the normalized title text, if any."""

        title = _normalize_text_output(" ".join(self._title_chunks))
        return title or None

    @property
    def text(self) -> str:
        """Return the normalized visible text content."""

        return _normalize_text_output(" ".join(self._text_chunks))

    @property
    def links(self) -> list[str]:
        """Return normalized outgoing links in stable order."""

        return list(self._links)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered_tag = tag.lower()

        if lowered_tag == "head":
            self._in_head = True
            return

        if lowered_tag in _IGNORED_TEXT_TAGS:
            self._ignored_depth += 1
            return

        if lowered_tag == "title":
            self._in_title = True
            return

        if lowered_tag != "a":
            return

        href = None
        for name, value in attrs:
            if name.lower() == "href":
                href = value
                break

        normalized = normalize_url(href, self.base_url) if href else None
        if normalized and normalized not in self._seen_links:
            self._seen_links.add(normalized)
            self._links.append(normalized)

    def handle_endtag(self, tag: str) -> None:
        lowered_tag = tag.lower()

        if lowered_tag == "head":
            self._in_head = False
            return

        if lowered_tag in _IGNORED_TEXT_TAGS:
            if self._ignored_depth > 0:
                self._ignored_depth -= 1
            return

        if lowered_tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if not data:
            return

        normalized = _collapse_whitespace(data)
        if not normalized:
            return

        if self._in_title:
            self._title_chunks.append(normalized)

        if self._ignored_depth == 0 and not self._in_title and not self._in_head:
            self._text_chunks.append(normalized)


def extract_links(html_text: str, base_url: str) -> list[str]:
    """Extract normalized anchor links from an HTML document.

    Links are returned without duplicates, in the order they are first seen.
    """

    parser = _DocumentParser(normalize_url(base_url) or base_url)
    parser.feed(html_text or "")
    parser.close()
    return parser.links


def extract_text_and_title(html_text: str) -> tuple[str, str | None]:
    """Extract conservative visible text and the page title.

    Returns:
        A ``(text, title)`` tuple where ``title`` is ``None`` if missing.
    """

    parser = _DocumentParser(base_url=None)
    parser.feed(html_text or "")
    parser.close()
    return parser.text, parser.title


def parse_html_document(html_text: str, base_url: str) -> ParsedPage:
    """Parse an HTML document into normalized crawl-friendly fields."""

    normalized_base_url = normalize_url(base_url)
    parser = _DocumentParser(normalized_base_url or base_url)
    parser.feed(html_text or "")
    parser.close()
    return ParsedPage(
        url=normalized_base_url,
        title=parser.title,
        text=parser.text,
        links=parser.links,
    )


__all__ = [
    "ParsedPage",
    "extract_links",
    "extract_text_and_title",
    "normalize_url",
    "parse_html_document",
]
