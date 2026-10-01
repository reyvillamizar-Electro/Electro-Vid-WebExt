from __future__ import annotations

import json
import os
import sys
from urllib.parse import urlparse

import webview


DEFAULT_URL = "https://www.google.com"

MEDIA_MARKERS = (
    ".m3u8",
    ".mpd",
    ".mp4",
    ".webm",
    ".m4s",
    ".ts",
)


def install_popup_blocker(window: webview.Window) -> None:
    try:
        window.run_js(
            """
            (() => {
              if (window.__ELECTRO_POPUP_BLOCKER__) return;
              window.__ELECTRO_POPUP_BLOCKER__ = true;

              const originalOpen = window.open;
              window.open = function(url, ...args) {
                console.warn('[Electro Test] popup bloqueado:', url || '');

                // Some ad-supported players check whether window.open()
                // returned an object before unlocking the real player.
                // Return a harmless decoy instead of null, while never
                // creating an actual browser window.
                const decoy = {
                  closed: false,
                  opener: window,
                  location: {
                    href: String(url || ''),
                    replace() {},
                    assign() {}
                  },
                  focus() {},
                  blur() {},
                  close() { this.closed = true; },
                  postMessage() {},
                  addEventListener() {},
                  removeEventListener() {}
                };
                return decoy;
              };

              document.addEventListener(
                'click',
                (event) => {
                  const target = event.target instanceof Element
                    ? event.target.closest('a[target="_blank"]')
                    : null;
                  if (!target) return;
                  event.preventDefault();
                  event.stopImmediatePropagation();
                  console.warn(
                    '[Electro Test] enlace target=_blank bloqueado:',
                    target.href || ''
                  );
                },
                true
              );
            })();
            """
        )
    except Exception as exc:
        print(f"No se pudo instalar el bloqueador de popups: {exc}")


def log_request(request) -> None:
    url = str(getattr(request, "url", "") or "")
    lower = url.lower()
    if any(marker in lower for marker in MEDIA_MARKERS):
        print("[MEDIA]", url)


def codec_report(window: webview.Window) -> None:
    try:
        raw = window.evaluate_js(
            """
            (() => {
              const video = document.createElement('video');
              const audio = document.createElement('audio');
              const tests = [
                ['H.264 / AVC', video.canPlayType('video/mp4; codecs="avc1.42E01E"')],
                ['H.264 High', video.canPlayType('video/mp4; codecs="avc1.640028"')],
                ['AAC-LC', audio.canPlayType('audio/mp4; codecs="mp4a.40.2"')],
                ['VP9', video.canPlayType('video/webm; codecs="vp09.00.10.08"')],
                ['AV1', video.canPlayType('video/mp4; codecs="av01.0.05M.08"')],
                ['Opus', audio.canPlayType('audio/webm; codecs="opus"')]
              ];
              return JSON.stringify({
                ua: navigator.userAgent,
                tests
              });
            })();
            """
        )
        if isinstance(raw, str):
            data = json.loads(raw)
        elif isinstance(raw, dict):
            data = raw
        else:
            data = {}

        print("\n=== WebView2 codec diagnostic ===")
        print("User-Agent:", data.get("ua", "—"))
        for name, result in data.get("tests", []):
            print(f"{name}: {result or 'NO'}")
        print("=================================\n")
    except Exception as exc:
        print(f"No se pudo ejecutar diagnóstico de codecs: {exc}")


def main() -> int:
    url = sys.argv[1].strip() if len(sys.argv) > 1 else DEFAULT_URL
    if "://" not in url:
        url = "https://" + url

    os.environ.setdefault("PYWEBVIEW_LOG", "debug")

    window = webview.create_window(
        "WebView2 Test Browser — Electro Vid-WebExt",
        url=url,
        maximized=True,
        resizable=True,
        text_select=True,
        confirm_close=False,
    )
    original_url = url
    original_host = (urlparse(original_url).hostname or "").lower()

    def host_allowed(candidate_url: str | None) -> bool:
        if not candidate_url:
            return True
        parsed = urlparse(candidate_url)
        if parsed.scheme not in {"http", "https"}:
            return True
        host = (parsed.hostname or "").lower()
        return (
            host == original_host
            or host.endswith("." + original_host)
        )

    def on_before_load() -> None:
        # before_load fires before the native WebView window is fully ready.
        # Do not query get_current_url() here; just install the JS guard.
        install_popup_blocker(window)

    def on_loaded() -> None:
        try:
            current = window.get_current_url()
        except Exception:
            current = None

        if current and not host_allowed(current):
            print("[NAV BLOQUEADA]", current)
            window.load_url(original_url)
            return

        install_popup_blocker(window)
        codec_report(window)

    window.events.before_load += on_before_load
    window.events.loaded += on_loaded
    window.events.request_sent += log_request

    # Keep popups/new-window links inside the WebView layer so our injected
    # blocker and top-level navigation guard can suppress them.
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    webview.settings["ALLOW_DOWNLOADS"] = False
    webview.settings["IGNORE_SSL_ERRORS"] = False

    print("Abriendo prueba aislada con Microsoft Edge WebView2…")
    print("URL:", url)
    print("Cierra esta ventana para volver a PowerShell.")

    webview.start(gui="edgechromium", debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
