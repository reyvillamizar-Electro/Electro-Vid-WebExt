from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from PySide6.QtCore import QUrl, Signal
from PySide6.QtWebEngineCore import (
    QWebEngineProfile,
    QWebEngineUrlRequestInfo,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from electro_vid_webext.core.detector import VideoSource, media_kind_from_url

_MEDIA_HINT_RE = re.compile(
    r"(?:\.mp4|\.webm|\.m3u8|\.mpd|\.mov|\.m4v|\.mkv)(?:$|[?#])",
    re.IGNORECASE,
)


@dataclass(slots=True)
class _CookieRecord:
    name: str
    value: str
    domain: str
    path: str
    secure: bool


def _bytes_text(value) -> str:
    try:
        return bytes(value).decode("utf-8", errors="replace")
    except Exception:
        return str(value)


class MediaRequestInterceptor(QWebEngineUrlRequestInterceptor):
    media_found = Signal(object)

    def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:
        url = info.requestUrl().toString()
        kind = media_kind_from_url(url)

        is_media_resource = (
            info.resourceType() == QWebEngineUrlRequestInfo.ResourceType.ResourceTypeMedia
        )
        if not kind and not is_media_resource and not _MEDIA_HINT_RE.search(url.lower()):
            return

        headers: dict[str, str] = {}
        try:
            for key, value in info.httpHeaders().items():
                headers[_bytes_text(key).lower()] = _bytes_text(value)
        except Exception:
            pass

        first_party = info.firstPartyUrl().toString()
        referer = headers.get("referer") or first_party or None
        origin = headers.get("origin")

        self.media_found.emit(
            {
                "url": url,
                "kind": kind or "Media",
                "origin": f"Red · {kind or 'Media'}",
                "referer": referer,
                "cookie_header": headers.get("cookie"),
                "origin_header": origin,
            }
        )


class BrowserView(QWebEngineView):
    media_found = Signal(object)
    page_ready = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._cookies: dict[tuple[str, str, str], _CookieRecord] = {}
        self.profile = QWebEngineProfile.defaultProfile()

        self.interceptor = MediaRequestInterceptor(self)
        self.interceptor.media_found.connect(self._on_network_media)
        self.profile.setUrlRequestInterceptor(self.interceptor)

        cookie_store = self.profile.cookieStore()
        cookie_store.cookieAdded.connect(self._cookie_added)
        cookie_store.cookieRemoved.connect(self._cookie_removed)
        cookie_store.loadAllCookies()

        self.loadFinished.connect(self._on_load_finished)

    def load_page(self, url: str) -> None:
        self.setUrl(QUrl(url))

    def session_source(
        self,
        url: str,
        kind: str,
        origin: str,
        referer: str | None = None,
        origin_header: str | None = None,
        cookie_header: str | None = None,
    ) -> VideoSource:
        return VideoSource(
            url=url,
            kind=kind,
            origin=origin,
            referer=referer or self.url().toString() or None,
            user_agent=self.profile.httpUserAgent(),
            cookie_header=cookie_header or self.cookie_header_for(url),
            origin_header=origin_header,
        )

    def cookie_header_for(self, url: str) -> str | None:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path or "/"
        secure_request = parsed.scheme.lower() == "https"

        values: list[str] = []
        for cookie in self._cookies.values():
            domain = cookie.domain.lower().lstrip(".")
            domain_ok = host == domain or host.endswith(f".{domain}")
            path_ok = path.startswith(cookie.path or "/")
            secure_ok = not cookie.secure or secure_request
            if domain_ok and path_ok and secure_ok:
                values.append(f"{cookie.name}={cookie.value}")

        return "; ".join(values) if values else None

    def _cookie_added(self, cookie) -> None:
        record = _CookieRecord(
            name=_bytes_text(cookie.name()),
            value=_bytes_text(cookie.value()),
            domain=str(cookie.domain() or ""),
            path=str(cookie.path() or "/"),
            secure=bool(cookie.isSecure()),
        )
        self._cookies[(record.name, record.domain, record.path)] = record

    def _cookie_removed(self, cookie) -> None:
        key = (
            _bytes_text(cookie.name()),
            str(cookie.domain() or ""),
            str(cookie.path() or "/"),
        )
        self._cookies.pop(key, None)

    def _on_network_media(self, context: object) -> None:
        if not isinstance(context, dict):
            return

        url = str(context.get("url") or "")
        if not url:
            return

        source = self.session_source(
            url=url,
            kind=str(context.get("kind") or "Media"),
            origin=str(context.get("origin") or "Red"),
            referer=context.get("referer"),
            origin_header=context.get("origin_header"),
            cookie_header=context.get("cookie_header"),
        )
        self.media_found.emit(source)

    def _on_load_finished(self, ok: bool) -> None:
        if not ok:
            return
        self.rescan_dom()
        self.page_ready.emit()

    def rescan_dom(self) -> None:
        script = """
        (() => {
          const found = new Set();
          const add = (value) => {
            if (!value || typeof value !== 'string') return;
            if (value.startsWith('blob:') || value.startsWith('data:')) return;
            found.add(value);
          };

          document.querySelectorAll('video').forEach(video => {
            add(video.currentSrc);
            add(video.src);
          });
          document.querySelectorAll('source').forEach(source => add(source.src));

          try {
            performance.getEntriesByType('resource').forEach(entry => add(entry.name));
          } catch (_) {}

          return Array.from(found);
        })();
        """
        self.page().runJavaScript(script, self._consume_dom_results)

    def _consume_dom_results(self, values) -> None:
        if not isinstance(values, list):
            return

        for value in values:
            if not isinstance(value, str):
                continue
            kind = media_kind_from_url(value)
            if not kind:
                continue

            self.media_found.emit(
                self.session_source(
                    url=value,
                    kind=kind,
                    origin=f"DOM/Red · {kind}",
                    referer=self.url().toString() or None,
                )
            )
