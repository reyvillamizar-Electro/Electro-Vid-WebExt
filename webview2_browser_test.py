from __future__ import annotations

import json
import os
import sys
import threading
import time
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


def log_request(request) -> None:
    url = str(getattr(request, "url", "") or "")
    lower = url.lower()
    if any(marker in lower for marker in MEDIA_MARKERS):
        print("[MEDIA]", url)


def install_native_navigation_guard(
    window: webview.Window,
    state: dict[str, object],
    divert_external,
) -> None:
    try:
        native_webview = window.native.webview
    except Exception as exc:
        print(f"[GUARD] WebView2 nativo no disponible: {exc}")
        return

    if state.get("handler_installed"):
        return

    def on_navigation_starting(sender, args) -> None:
        if not state.get("enabled"):
            return

        try:
            uri = str(args.Uri or "")
        except Exception:
            uri = ""

        if not uri:
            return

        parsed = urlparse(uri)
        if parsed.scheme not in {"http", "https"}:
            return

        host = (parsed.hostname or "").lower()
        allowed_hosts = state.get("allowed_hosts")
        if not isinstance(allowed_hosts, set):
            allowed_hosts = set()

        allowed = any(
            host == allowed_host or host.endswith("." + allowed_host)
            for allowed_host in allowed_hosts
            if allowed_host
        )
        if allowed:
            return

        try:
            args.Cancel = True
        except Exception:
            try:
                args.set_Cancel(True)
            except Exception as exc:
                print(f"[GUARD] no se pudo cancelar {uri}: {exc}")
                return

        print("[NAV-CANCEL]", uri)
        try:
            divert_external(uri)
        except Exception as exc:
            print(f"[AD-SINK] no se pudo desviar la navegación: {exc}")

    try:
        native_webview.NavigationStarting += on_navigation_starting
        state["handler_installed"] = True
        state["handler"] = on_navigation_starting
        print("[GUARD] interceptor nativo NavigationStarting instalado")
    except Exception as exc:
        print(f"[GUARD] no se pudo instalar: {exc}")


def host_of(value: str | None) -> str:
    if not value:
        return ""
    return (urlparse(value).hostname or "").lower()


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
    initial_host = host_of(url)
    guard_state: dict[str, object] = {
        "enabled": True,
        "allowed_hosts": {initial_host} if initial_host else set(),
        "handler_installed": False,
        "last_good_url": url,
    }
    ad_lock = threading.Lock()
    ad_window: webview.Window | None = None

    def close_ad_window_later(target: webview.Window, delay: float = 1.2) -> None:
        def worker() -> None:
            time.sleep(delay)
            try:
                target.destroy()
                print("[AD-SINK] ventana publicitaria cerrada")
            except Exception:
                pass

        threading.Thread(
            target=worker,
            name="webview2-ad-sink-close",
            daemon=True,
        ).start()

    def divert_external(uri: str) -> None:
        nonlocal ad_window
        with ad_lock:
            try:
                if ad_window is not None:
                    ad_window.destroy()
            except Exception:
                pass

            print("[AD-SINK]", uri)
            ad_window = webview.create_window(
                "Electro Ad Sink",
                url=uri,
                width=2,
                height=2,
                x=-10000,
                y=-10000,
                hidden=True,
                focus=False,
                resizable=False,
                text_select=False,
                confirm_close=False,
            )
            close_ad_window_later(ad_window)

    def on_before_show() -> None:
        install_native_navigation_guard(
            window,
            guard_state,
            divert_external,
        )

    window.events.before_show += on_before_show

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
                player_host = host_of(player)

                allowed_hosts = guard_state.get("allowed_hosts")
                if not isinstance(allowed_hosts, set):
                    allowed_hosts = set()
                    guard_state["allowed_hosts"] = allowed_hosts

                if player_host:
                    allowed_hosts.add(player_host)
                guard_state["last_good_url"] = player

                # The guard is active from startup. Add each discovered player
                # host before navigating to it so only the legitimate player
                # chain stays in the main WebView.
                print(
                    "[GUARD] hosts permitidos:",
                    ", ".join(sorted(allowed_hosts)),
                )

                visited_players.add(player)
                player_depth += 1
                print(f"[PLAYER {player_depth}]", player)
                window.load_url(player)
                return

        if auto_player and current:
            current_host = host_of(current)
            allowed_hosts = guard_state.get("allowed_hosts")
            if not isinstance(allowed_hosts, set):
                allowed_hosts = set()

            allowed = any(
                current_host == allowed_host
                or current_host.endswith("." + allowed_host)
                for allowed_host in allowed_hosts
                if allowed_host
            )

            if not allowed:
                last_good = str(guard_state.get("last_good_url") or url)
                print("[RECOVER] navegación externa detectada; restaurando:", last_good)
                window.load_url(last_good)
                return

            if player_depth >= 1:
                guard_state["last_good_url"] = current

    window.events.loaded += on_loaded
    window.events.request_sent += log_request

    # Keep new-window requests out of the system browser. Top-level redirects
    # are separately controlled by the native NavigationStarting guard.
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    webview.settings["ALLOW_DOWNLOADS"] = False
    webview.settings["IGNORE_SSL_ERRORS"] = False

    print("Abriendo prueba aislada con Microsoft Edge WebView2…")
    print("URL:", url)
    if "--player" in sys.argv[2:]:
        print("Modo --player: mantendrá el último player válido y recuperará la ventana si una publicidad logra reemplazarlo.")
    else:
        print("Se mostrarán los [IFRAME] detectados sin cambiar de página.")
    print("Cierra esta ventana para volver a PowerShell.")

    webview.start(gui="edgechromium", debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
