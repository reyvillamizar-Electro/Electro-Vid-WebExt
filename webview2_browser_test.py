from __future__ import annotations

import json
import os
import sys
import threading
import time
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


def install_ghost_popup(window: webview.Window) -> None:
    try:
        window.run_js(
            """
            (() => {
              if (window.__ELECTRO_GHOST_POPUP__) return;
              window.__ELECTRO_GHOST_POPUP__ = true;

              const frame = document.createElement('iframe');
              frame.src = 'about:blank';
              frame.setAttribute('aria-hidden', 'true');
              frame.tabIndex = -1;
              frame.style.cssText = [
                'position:fixed',
                'left:-10000px',
                'top:-10000px',
                'width:1px',
                'height:1px',
                'opacity:0',
                'pointer-events:none',
                'border:0'
              ].join(';');
              document.documentElement.appendChild(frame);

              const ghost = frame.contentWindow;
              if (!ghost) return;

              try {
                ghost.focus = () => {};
                ghost.blur = () => {};
              } catch (_) {}

              window.open = function(url, target, features) {
                try {
                  console.warn(
                    '[Electro Test] popup fantasma:',
                    String(url || '')
                  );
                } catch (_) {}

                // Keep a real WindowProxy alive but do not navigate it to the
                // advertising URL. This avoids a visible popup while giving
                // scripts a genuine window-like return value.
                try {
                  if (ghost.closed) {
                    return null;
                  }
                } catch (_) {}

                return ghost;
              };

              document.addEventListener(
                'click',
                (event) => {
                  const element = event.target instanceof Element
                    ? event.target.closest('a[target="_blank"]')
                    : null;
                  if (!element) return;

                  // Do not let target=_blank links escape to the system
                  // browser. The site's window.open path receives the ghost.
                  event.preventDefault();
                  event.stopPropagation();
                  try {
                    console.warn(
                      '[Electro Test] target=_blank contenido:',
                      element.href || ''
                    );
                  } catch (_) {}
                },
                true
              );
            })();
            """
        )
        print("[GHOST] bloqueo de popups activo en el reproductor")
    except Exception as exc:
        print(f"[GHOST] no se pudo instalar: {exc}")


def media_snapshot(window: webview.Window) -> list[str]:
    try:
        raw = window.evaluate_js(
            """
            (() => {
              const values = new Set();
              const add = (value) => {
                if (!value || typeof value !== 'string') return;
                if (value.startsWith('blob:') || value.startsWith('data:')) return;
                values.add(value);
              };

              document.querySelectorAll('video').forEach(video => {
                add(video.currentSrc);
                add(video.src);
              });
              document.querySelectorAll('source').forEach(source => add(source.src));

              try {
                performance.getEntriesByType('resource').forEach(entry => {
                  add(entry.name);
                });
              } catch (_) {}

              return JSON.stringify(Array.from(values));
            })();
            """
        )
        if isinstance(raw, str):
            values = json.loads(raw)
        elif isinstance(raw, list):
            values = raw
        else:
            values = []
    except Exception:
        return []

    result = []
    for value in values:
        if not isinstance(value, str):
            continue
        lower = value.lower()
        if any(marker in lower for marker in MEDIA_MARKERS):
            result.append(value)
    return result


def start_media_monitor(window: webview.Window) -> None:
    def worker() -> None:
        seen: set[str] = set()
        while True:
            try:
                values = media_snapshot(window)
            except Exception:
                values = []

            for value in values:
                if value in seen:
                    continue
                seen.add(value)
                print("[MEDIA-DOM]", value)

            time.sleep(1.0)

    thread = threading.Thread(
        target=worker,
        name="webview2-media-monitor",
        daemon=True,
    )
    thread.start()


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
    visited_players: set[str] = set()
    player_depth = 0
    monitor_started = False

    def on_loaded() -> None:
        nonlocal first_codec_report, player_depth, monitor_started
        try:
            current = window.get_current_url()
        except Exception:
            current = None

        if current:
            print("[PAGE]", current)

        if first_codec_report:
            first_codec_report = False
            codec_report(window)

        if not monitor_started:
            monitor_started = True
            start_media_monitor(window)

        frames = iframe_report(window)
        if auto_player and player_depth < 5:
            player = choose_player_iframe(frames)
            if player and player not in visited_players:
                visited_players.add(player)
                player_depth += 1
                print(f"[PLAYER {player_depth}]", player)
                window.load_url(player)
                return

        if auto_player and player_depth >= 2:
            install_ghost_popup(window)

    window.events.loaded += on_loaded
    window.events.request_sent += log_request

    # For this diagnostic, do not alter the site's popup/player flow.
    # New-window links may open in the system browser so the WebView2 player
    # page remains intact while we verify actual media playback.
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    webview.settings["ALLOW_DOWNLOADS"] = False
    webview.settings["IGNORE_SSL_ERRORS"] = False

    print("Abriendo prueba aislada con Microsoft Edge WebView2…")
    print("URL:", url)
    if "--player" in sys.argv[2:]:
        print("Modo --player: seguirá la cadena de iframes y activará popup fantasma en el player.")
    else:
        print("Se mostrarán los [IFRAME] detectados sin cambiar de página.")
    print("Cierra esta ventana para volver a PowerShell.")

    webview.start(gui="edgechromium", debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
