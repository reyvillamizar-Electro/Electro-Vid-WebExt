from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

MEDIA_EXTENSIONS = {
    ".mp4": "MP4",
    ".webm": "WebM",
    ".m3u8": "HLS",
    ".mpd": "DASH",
    ".mov": "MOV",
    ".m4v": "M4V",
}

MEDIA_URL_RE = re.compile(
    r"""(?P<url>(?:https?:)?//[^\s\"'<>]+?\.(?:mp4|webm|m3u8|mpd|mov|m4v)(?:\?[^\s\"'<>]*)?)""",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class VideoSource:
    url: str
    kind: str
    origin: str
    referer: str | None = None
    user_agent: str | None = None
    cookie_header: str | None = None
    origin_header: str | None = None
    quality_hint: str = "—"
    resolution_hint: str = "—"
    protection: str = "—"


class _MediaHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.candidates: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value for key, value in attrs if value}
        tag = tag.lower()

        if tag in {"video", "source"} and values.get("src"):
            self.candidates.append((values["src"], f"<{tag}>"))

        if tag == "a" and values.get("href"):
            self.candidates.append((values["href"], "<a>"))

        if tag == "meta":
            prop = (values.get("property") or values.get("name") or "").lower()
            if prop in {"og:video", "og:video:url", "og:video:secure_url", "twitter:player:stream"}:
                if values.get("content"):
                    self.candidates.append((values["content"], f"<meta {prop}>"))


def media_kind_from_url(url: str) -> str | None:
    path = urlparse(url).path.lower()
    for extension, kind in MEDIA_EXTENSIONS.items():
        if path.endswith(extension):
            return kind
    return None


def _normalize_url(candidate: str, page_url: str) -> str | None:
    value = candidate.strip().replace("&amp;", "&")
    if value.startswith("blob:") or value.startswith("data:"):
        return None
    if value.startswith("//"):
        scheme = urlparse(page_url).scheme or "https"
        value = f"{scheme}:{value}"
    return urljoin(page_url, value)


def detect_video_sources(page_url: str, timeout: float = 15.0) -> list[VideoSource]:
    """Download a web page and find direct media URLs present in its HTML."""
    parsed = urlparse(page_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("La URL debe comenzar con http:// o https://")

    request = Request(
        page_url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )

    with urlopen(request, timeout=timeout) as response:
        content_type = response.headers.get_content_type()
        if content_type.startswith("video/") or media_kind_from_url(response.geturl()):
            final_url = response.geturl()
            return [VideoSource(final_url, media_kind_from_url(final_url) or content_type, "URL directa")]

        charset = response.headers.get_content_charset() or "utf-8"
        raw = response.read(8 * 1024 * 1024)
        html = raw.decode(charset, errors="replace")
        final_page_url = response.geturl()

    parser = _MediaHTMLParser()
    parser.feed(html)

    candidates = list(parser.candidates)
    candidates.extend((match.group("url"), "HTML/JavaScript") for match in MEDIA_URL_RE.finditer(html))

    found: dict[str, VideoSource] = {}
    for candidate, origin in candidates:
        normalized = _normalize_url(candidate, final_page_url)
        if not normalized:
            continue
        kind = media_kind_from_url(normalized)
        if not kind:
            continue
        found.setdefault(normalized, VideoSource(normalized, kind, origin))

    return list(found.values())
