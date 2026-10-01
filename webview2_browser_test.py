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


def _attach_popup_handler_to_core(
    core,
    state: dict[str, object],
) -> None:
    if state.get("popup_handler_installed"):
        return
    if core is None:
        return

    def on_new_window_requested(sender, args) -> None:
        try:
            uri = str(args.Uri or "")
        except Exception:
            uri = ""

        if uri:
            print("[POPUP-HANDLED]", uri)

        try:
            args.Handled = True
        except Exception:
            try:
                args.set_Handled(True)
            except Exception as exc:
                print(f"[POPUP] no se pudo marcar como atendido: {exc}")

    try:
        core.NewWindowRequested += on_new_window_requested
        state["popup_handler_installed"] = True
        state["popup_handler"] = on_new_window_requested
        state["popup_core"] = core
        print("[POPUP] interceptor CoreWebView2.NewWindowRequested instalado")
    except Exception as exc:
        print(f"[POPUP] no se pudo instalar en CoreWebView2: {exc}")


def install_native_new_window_handler(
    window: webview.Window,
    state: dict[str, object],
) -> None:
    try:
        native_webview = window.native.webview
    except Exception as exc:
        print(f"[POPUP] WebView2 nativo no disponible: {exc}")
        return

    if state.get("popup_handler_installed"):
        return

    try:
        core = native_webview.CoreWebView2
    except Exception:
        core = None

    if core is not None:
        _attach_popup_handler_to_core(core, state)
        return

    if state.get("popup_init_handler_installed"):
        return

    def on_core_initialized(sender, args) -> None:
        try:
            success = bool(args.IsSuccess)
        except Exception:
            success = True

        if not success:
            print("[POPUP] CoreWebView2InitializationCompleted falló")
            return

        try:
            initialized_core = sender.CoreWebView2
        except Exception as exc:
            print(f"[POPUP] no se pudo obtener CoreWebView2 inicializado: {exc}")
            return

        _attach_popup_handler_to_core(initialized_core, state)

    try:
        native_webview.CoreWebView2InitializationCompleted += on_core_initialized
        state["popup_init_handler_installed"] = True
        state["popup_init_handler"] = on_core_initialized
        print("[POPUP] esperando CoreWebView2InitializationCompleted")
    except Exception as exc:
        print(f"[POPUP] no se pudo escuchar la inicialización: {exc}")

def host_of(value: str | None) -> str:
    if not value:
        return ""
    return (urlparse(value).hostname or "").lower()


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
    initial_host = host_of(url)
    guard_state: dict[str, object] = {
        "enabled": False,
        "allowed_hosts": {initial_host} if initial_host else set(),
        "handler_installed": False,
        "last_good_url": url,
        "popup_handler_installed": False,
        "popup_init_handler_installed": False,
    }
    def divert_external(uri: str) -> None:
        print("[NAV-BLOCKED]", uri)

    def on_before_show() -> None:
        install_native_navigation_guard(
            window,
            guard_state,
            divert_external,
        )
        install_native_new_window_handler(window, guard_state)

    window.events.before_show += on_before_show

    def on_loaded() -> None:
        nonlocal first_codec_report, player_depth
        try:
            current = window.get_current_url()
        except Exception:
            current = None

        if current:
            print("[PAGE]", current)

        if not guard_state.get("popup_handler_installed"):
            install_native_new_window_handler(window, guard_state)

        current_host = host_of(current)

        if first_codec_report:
            first_codec_report = False
            codec_report(window)

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
                guard_state["enabled"] = True

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

        if auto_player and player_depth >= 1 and current:
            allowed_hosts = guard_state.get("allowed_hosts")
            if isinstance(allowed_hosts, set):
                current_host = host_of(current)
                allowed = any(
                    current_host == allowed_host
                    or current_host.endswith("." + allowed_host)
                    for allowed_host in allowed_hosts
                    if allowed_host
                )
                if allowed:
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
        print("Modo --player: esperará la inicialización completa de WebView2, atenderá popups sin salir de la página y seguirá la cadena del player.")
    else:
        print("Se mostrarán los [IFRAME] detectados sin cambiar de página.")
    print("Cierra esta ventana para volver a PowerShell.")

    webview.start(gui="edgechromium", debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
