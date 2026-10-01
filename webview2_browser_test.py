from __future__ import annotations

import json
import os
import sys

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
                return null;
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
    def on_loaded(current_window: webview.Window) -> None:
        install_popup_blocker(current_window)
        codec_report(current_window)

    window.events.loaded += on_loaded
    window.events.request_sent += log_request

    # pywebview normally sends target=_blank links to the external browser.
    # Keep them inside the WebView layer so our blocker can suppress them.
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
