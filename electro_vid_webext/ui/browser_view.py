from __future__ import annotations

import re

from PySide6.QtCore import QUrl, Signal
from PySide6.QtWebEngineCore import (
    QWebEngineProfile,
    QWebEngineUrlRequestInfo,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from electro_vid_webext.core.detector import media_kind_from_url

_MEDIA_HINT_RE = re.compile(
    r"(?:\.mp4|\.webm|\.m3u8|\.mpd|\.mov|\.m4v|\.mkv)(?:$|[?#])",
    re.IGNORECASE,
)


class MediaRequestInterceptor(QWebEngineUrlRequestInterceptor):
    media_found = Signal(str, str)

    def interceptRequest(self, info: QWebEngineUrlRequestInfo) -> None:
        url = info.requestUrl().toString()
        kind = media_kind_from_url(url)
        if kind:
            self.media_found.emit(url, f"Red · {kind}")
            return

        lowered = url.lower()
        if _MEDIA_HINT_RE.search(lowered):
            self.media_found.emit(url, "Red · Media")


class BrowserView(QWebEngineView):
    media_found = Signal(str, str)
    page_ready = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self.interceptor = MediaRequestInterceptor(self)
        self.interceptor.media_found.connect(self.media_found.emit)

        profile = QWebEngineProfile.defaultProfile()
        profile.setUrlRequestInterceptor(self.interceptor)

        self.loadFinished.connect(self._on_load_finished)

    def load_page(self, url: str) -> None:
        self.setUrl(QUrl(url))

    def _on_load_finished(self, ok: bool) -> None:
        if not ok:
            return

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
            if kind:
                self.media_found.emit(value, f"DOM/Red · {kind}")
