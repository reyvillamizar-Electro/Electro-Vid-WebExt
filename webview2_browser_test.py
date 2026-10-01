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


def log_request(request) -> None:
    url = str(getattr(request, "url", "") or "")
    lower = url.lower()
    if any(marker in lower for marker in MEDIA_MARKERS):
        print("[MEDIA]", url)


def iframe_report(window: webview.Window) -> list[str]:
    try:
        raw = window.evaluate_js(
            """
            (() => {
              const values = [];
              document.querySelectorAll('iframe[src], frame[src]').forEach(frame => {
                try {
                  values.push(new URL(frame.src, document.baseURI).href);
                } catch (_) {}
              });
              return JSON.stringify(Array.from(new Set(values)));
            })();
            """
        )
        if isinstance(raw, str):
            values = json.loads(raw)
        elif isinstance(raw, list):
            values = raw
        else:
            values = []
    except Exception as exc:
        print(f"No se pudieron leer iframes: {exc}")
        return []

    result = [value for value in values if isinstance(value, str) and value.startswith(("http://", "https://"))]
    for value in result:
        print("[IFRAME]", value)
    return result


def choose_player_iframe(values: list[str]) -> str | None:
    if not values:
        return None

    reject = (
        "yandex.",
        "google.",
        "doubleclick.",
        "googletagmanager.",
        "facebook.",
        "twitter.",
        "tiktok.",
    )
    preferred = (
        "/v/",
        "/embed/",
        "player",
        "stream",
        "video",
    )

    candidates = [
        value
        for value in values
        if not any(token in value.lower() for token in reject)
    ]
    if not candidates:
        return None

    ranked = sorted(
        candidates,
        key=lambda value: (
            any(token in value.lower() for token in preferred),
            len(value),
        ),
        reverse=True,
    )
    return ranked[0]


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
    first_codec_report = True
    auto_player = "--player" in sys.argv[2:]
    player_opened = False

    def on_loaded() -> None:
        nonlocal first_codec_report, player_opened
        try:
            current = window.get_current_url()
        except Exception:
            current = None

        if current:
            print("[PAGE]", current)

        if first_codec_report:
            first_codec_report = False
            codec_report(window)

        frames = iframe_report(window)
        if auto_player and not player_opened:
            player = choose_player_iframe(frames)
            if player:
                player_opened = True
                print("[PLAYER]", player)
                window.load_url(player)

    window.events.loaded += on_loaded
    window.events.request_sent += log_request

    # For this diagnostic, do not alter the site's popup/player flow.
    # New-window links may open in the system browser so the WebView2 player
    # page remains intact while we verify actual media playback.
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    webview.settings["ALLOW_DOWNLOADS"] = False
    webview.settings["IGNORE_SSL_ERRORS"] = False

    print("Abriendo prueba aislada con Microsoft Edge WebView2…")
    print("URL:", url)
    if "--player" in sys.argv[2:]:
        print("Modo --player: abrirá automáticamente el iframe que parece ser el reproductor.")
    else:
        print("Se mostrarán los [IFRAME] detectados sin cambiar de página.")
    print("Cierra esta ventana para volver a PowerShell.")

    webview.start(gui="edgechromium", debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
