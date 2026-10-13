from __future__ import annotations

import json
import os
import sys
import threading
from urllib.parse import urlparse

import webview


MEDIA_MARKERS = (
    ".m3u8",
    ".mpd",
    ".mp4",
    ".webm",
    ".m4s",
    ".ts",
    ".mov",
    ".m4v",
    ".mkv",
)


def emit(event_type: str, **payload) -> None:
    data = {"type": event_type, **payload}
    print(json.dumps(data, ensure_ascii=False), flush=True)


def kind_from_url(url: str) -> str | None:
    path = urlparse(url).path.lower()
    if path.endswith(".m3u8"):
        return "HLS"
    if path.endswith(".mpd"):
        return "DASH"
    if path.endswith(".mp4"):
        return "MP4"
    if path.endswith(".webm"):
        return "WebM"
    if path.endswith(".mov"):
        return "MOV"
    if path.endswith(".m4v"):
        return "M4V"
    if path.endswith(".mkv"):
        return "MKV"
    if path.endswith((".m4s", ".ts")):
        return "Media"
    return None


def host_of(value: str | None) -> str:
    if not value:
        return ""
    return (urlparse(value).hostname or "").lower()


def media_report(window: webview.Window) -> list[str]:
    try:
        raw = window.evaluate_js(
            """
            (() => {
              const values = new Set();
              const add = value => {
                if (!value || typeof value !== 'string') return;
                if (value.startsWith('blob:') || value.startsWith('data:')) return;
                values.add(value);
              };

              document.querySelectorAll('video').forEach(video => {
                add(video.currentSrc);
                add(video.src);
              });
              document.querySelectorAll('source[src]').forEach(source => add(source.src));

              try {
                performance.getEntriesByType('resource').forEach(entry => add(entry.name));
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

    result: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        lower = value.lower()
        if kind_from_url(value) or any(marker in lower for marker in MEDIA_MARKERS):
            result.append(value)
    return result


BLOCKED_AD_HOSTS = (
    "dingerepilobe.com",
)

FAKE_CAPTCHA_MARKERS = (
    "haz clic en permitir",
    "si no eres un robot",
    "e-captcha",
)


def install_content_guard(window: webview.Window) -> None:
    script = r"""
    (() => {
      if (window.__ELECTRO_CONTENT_GUARD__) return;
      window.__ELECTRO_CONTENT_GUARD__ = true;

      const blockedHosts = [
        'dingerepilobe.com'
      ];

      const fakeMarkers = [
        'haz clic en permitir',
        'si no eres un robot',
        'e-captcha'
      ];

      const hostBlocked = value => {
        try {
          const host = new URL(value, document.baseURI).hostname.toLowerCase();
          return blockedHosts.some(
            blocked => host === blocked || host.endsWith('.' + blocked)
          );
        } catch (_) {
          return false;
        }
      };

      const looksLikeFakeCaptcha = node => {
        if (!(node instanceof Element)) return false;

        const text = String(node.innerText || node.textContent || '')
          .toLowerCase()
          .replace(/\s+/g, ' ')
          .trim();

        if (!text) return false;

        const hits = fakeMarkers.filter(marker => text.includes(marker)).length;
        if (hits < 2) return false;

        const style = getComputedStyle(node);
        const rect = node.getBoundingClientRect();
        const overlayLike =
          style.position === 'fixed'
          || style.position === 'absolute'
          || Number(style.zIndex || 0) >= 1000
          || (rect.width > innerWidth * 0.45 && rect.height > innerHeight * 0.35);

        return overlayLike;
      };

      const clean = root => {
        try {
          const scope = root instanceof Element || root instanceof Document
            ? root
            : document;

          scope.querySelectorAll?.('script[src], iframe[src]').forEach(el => {
            const src = el.getAttribute('src') || '';
            if (src && hostBlocked(src)) {
              el.remove();
            }
          });

          const nodes = [];
          if (scope instanceof Element) nodes.push(scope);
          scope.querySelectorAll?.('div,section,aside,dialog').forEach(
            el => nodes.push(el)
          );

          let fakeCaptchaRemoved = false;
          for (const node of nodes) {
            if (looksLikeFakeCaptcha(node)) {
              node.remove();
              fakeCaptchaRemoved = true;
              break;
            }
          }

          if (fakeCaptchaRemoved) {
            const clearBlockingLayers = () => {
              try {
                document.querySelectorAll(
                  'div,section,aside,dialog,form'
                ).forEach(el => {
                  if (!(el instanceof Element)) return;
                  if (el.querySelector('video,canvas')) return;

                  const style = getComputedStyle(el);
                  const rect = el.getBoundingClientRect();
                  const coversViewport =
                    rect.width >= innerWidth * 0.75
                    && rect.height >= innerHeight * 0.75;
                  const floating =
                    style.position === 'fixed'
                    || style.position === 'absolute';
                  const highLayer = Number(style.zIndex || 0) >= 10;
                  const text = String(
                    el.innerText || el.textContent || ''
                  ).trim();

                  if (
                    coversViewport
                    && floating
                    && highLayer
                    && text.length < 80
                  ) {
                    el.remove();
                  }
                });

                document.documentElement.style.overflow = '';
                document.documentElement.style.pointerEvents = '';
                if (document.body) {
                  document.body.style.overflow = '';
                  document.body.style.pointerEvents = '';
                }
              } catch (_) {}
            };

            clearBlockingLayers();
            setTimeout(clearBlockingLayers, 50);
            setTimeout(clearBlockingLayers, 250);
          }
        } catch (_) {}
      };

      clean(document);

      const observer = new MutationObserver(records => {
        for (const record of records) {
          for (const node of record.addedNodes) {
            if (node instanceof Element) {
              if (
                (node.matches('script[src], iframe[src]'))
                && hostBlocked(node.getAttribute('src') || '')
              ) {
                node.remove();
                continue;
              }
              clean(node);
            }
          }
        }
      });

      observer.observe(document.documentElement, {
        childList: true,
        subtree: true
      });

      const originalCreateElement = document.createElement.bind(document);
      document.createElement = function(name, options) {
        const el = originalCreateElement(name, options);
        if (String(name).toLowerCase() === 'script') {
          const originalSetAttribute = el.setAttribute.bind(el);
          el.setAttribute = function(attr, value) {
            if (
              String(attr).toLowerCase() === 'src'
              && hostBlocked(String(value || ''))
            ) {
              return;
            }
            return originalSetAttribute(attr, value);
          };
        }
        return el;
      };
    })();
    """

    try:
        window.evaluate_js(script)
    except Exception:
        pass


def emit_media_snapshot(window: webview.Window, state: dict[str, object]) -> None:
    current = str(state.get("current_url") or "")
    user_agent = str(state.get("user_agent") or "")
    for url in media_report(window):
        kind = kind_from_url(url) or "Media"
        emit(
            "media",
            url=url,
            kind=kind,
            origin=f"WebView2 actual · {kind}",
            referer=current,
            origin_header=None,
            cookie_header=None,
            user_agent=user_agent,
        )


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
    except Exception:
        return []

    return [
        value
        for value in values
        if isinstance(value, str)
        and value.startswith(("http://", "https://"))
    ]


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

    return sorted(
        candidates,
        key=lambda value: (
            any(token in value.lower() for token in preferred),
            len(value),
        ),
        reverse=True,
    )[0]


def attach_native_content_guard(core, state: dict[str, object]) -> None:
    if core is None or state.get("native_content_guard_installed"):
        return

    guard_script = r"""
    (() => {
      if (window.__ELECTRO_NATIVE_GUARD__) return;
      window.__ELECTRO_NATIVE_GUARD__ = true;

      const blockedHosts = ['dingerepilobe.com'];
      const markers = [
        'haz clic en permitir',
        'si no eres un robot',
        'e-captcha'
      ];

      const normalize = value =>
        String(value || '').toLowerCase().replace(/\s+/g, ' ').trim();

      const hostBlocked = value => {
        try {
          const host = new URL(value, document.baseURI).hostname.toLowerCase();
          return blockedHosts.some(
            blocked => host === blocked || host.endsWith('.' + blocked)
          );
        } catch (_) {
          return false;
        }
      };

      const removeBlockedResources = root => {
        try {
          const scope =
            root instanceof Element || root instanceof Document
              ? root
              : document;

          scope.querySelectorAll?.('script[src], iframe[src], img[src]').forEach(el => {
            const src = el.getAttribute('src') || '';
            if (src && hostBlocked(src)) {
              el.remove();
            }
          });
        } catch (_) {}
      };

      const removeFakeCaptcha = root => {
        try {
          const scope =
            root instanceof Element || root instanceof Document
              ? root
              : document;

          const candidates = [];
          if (scope instanceof Element) candidates.push(scope);

          scope.querySelectorAll?.(
            'div,section,aside,dialog,form,main,article'
          ).forEach(el => candidates.push(el));

          for (const el of candidates) {
            const text = normalize(el.innerText || el.textContent || '');
            if (!text) continue;

            const hits = markers.filter(marker => text.includes(marker)).length;
            if (hits < 2) continue;

            let target = el;
            for (let i = 0; i < 5 && target?.parentElement; i++) {
              const parent = target.parentElement;
              const style = getComputedStyle(parent);
              const rect = parent.getBoundingClientRect();
              const overlayLike =
                style.position === 'fixed'
                || style.position === 'absolute'
                || Number(style.zIndex || 0) >= 100
                || (
                  rect.width >= innerWidth * 0.45
                  && rect.height >= innerHeight * 0.35
                );

              if (overlayLike) {
                target = parent;
              } else {
                break;
              }
            }

            target?.remove();

            const clearBlockingLayers = () => {
              try {
                document.querySelectorAll(
                  'div,section,aside,dialog,form'
                ).forEach(layer => {
                  if (!(layer instanceof Element)) return;
                  if (layer.querySelector('video,canvas')) return;

                  const layerStyle = getComputedStyle(layer);
                  const layerRect = layer.getBoundingClientRect();
                  const coversViewport =
                    layerRect.width >= innerWidth * 0.75
                    && layerRect.height >= innerHeight * 0.75;
                  const floating =
                    layerStyle.position === 'fixed'
                    || layerStyle.position === 'absolute';
                  const highLayer = Number(layerStyle.zIndex || 0) >= 10;
                  const layerText = normalize(
                    layer.innerText || layer.textContent || ''
                  );

                  if (
                    coversViewport
                    && floating
                    && highLayer
                    && layerText.length < 80
                  ) {
                    layer.remove();
                  }
                });

                document.documentElement.style.overflow = '';
                document.documentElement.style.pointerEvents = '';
                if (document.body) {
                  document.body.style.overflow = '';
                  document.body.style.pointerEvents = '';
                }
              } catch (_) {}
            };

            clearBlockingLayers();
            setTimeout(clearBlockingLayers, 50);
            setTimeout(clearBlockingLayers, 250);
            return true;
          }
        } catch (_) {}

        return false;
      };

      const clean = root => {
        removeBlockedResources(root);
        removeFakeCaptcha(root);
      };

      const start = () => {
        clean(document);

        try {
          const observer = new MutationObserver(records => {
            for (const record of records) {
              for (const node of record.addedNodes) {
                if (node instanceof Element) clean(node);
              }
            }
            removeFakeCaptcha(document);
          });

          observer.observe(document.documentElement || document, {
            childList: true,
            subtree: true
          });
        } catch (_) {}

        // Some ad scripts rebuild the overlay repeatedly. Keep a lightweight
        // guard active so it is removed even when inserted through timers.
        setInterval(() => {
          clean(document);
        }, 400);
      };

      if (document.documentElement) {
        start();
      } else {
        addEventListener('DOMContentLoaded', start, {once:true});
      }
    })();
    """

    try:
        task = core.AddScriptToExecuteOnDocumentCreatedAsync(guard_script)
        state["native_content_guard_task"] = task
    except Exception as exc:
        emit(
            "diagnostic",
            message=f"No se pudo instalar script preventivo en frames: {exc}",
        )

    def on_web_resource_requested(sender, args) -> None:
        try:
            uri = str(args.Request.Uri or "")
        except Exception:
            uri = ""

        if not uri:
            return

        host = host_of(uri)
        if not any(
            host == blocked or host.endswith("." + blocked)
            for blocked in BLOCKED_AD_HOSTS
        ):
            return

        blocked = False
        try:
            from System.IO import MemoryStream
            from System.Text import Encoding

            stream = MemoryStream(Encoding.UTF8.GetBytes(""))
            args.Response = core.Environment.CreateWebResourceResponse(
                stream,
                403,
                "Blocked",
                "Content-Type: text/plain",
            )
            blocked = True
        except Exception:
            pass

        emit(
            "navigation",
            event="Recurso publicitario bloqueado",
            from_url=str(state.get("current_url") or ""),
            to=uri,
            detail=(
                "Petición cancelada por WebView2"
                if blocked
                else "Dominio publicitario detectado"
            ),
        )

    try:
        from Microsoft.Web.WebView2.Core import (
            CoreWebView2WebResourceContext,
            CoreWebView2WebResourceRequestSourceKinds,
        )

        try:
            core.AddWebResourceRequestedFilter(
                "*",
                CoreWebView2WebResourceContext.All,
                CoreWebView2WebResourceRequestSourceKinds.All,
            )
        except Exception:
            core.AddWebResourceRequestedFilter(
                "*",
                CoreWebView2WebResourceContext.All,
            )

        core.WebResourceRequested += on_web_resource_requested
        state["web_resource_handler"] = on_web_resource_requested
    except Exception as exc:
        emit(
            "diagnostic",
            message=f"No se pudo instalar filtro de recursos WebView2: {exc}",
        )

    state["native_content_guard_installed"] = True
    emit(
        "status",
        message="Protección de anuncios y falsos CAPTCHA activa en todos los frames.",
    )


def attach_permission_handler(core, state: dict[str, object]) -> None:
    if core is None or state.get("permission_handler_installed"):
        return

    def on_permission_requested(sender, args) -> None:
        try:
            uri = str(args.Uri or "")
        except Exception:
            uri = ""

        try:
            kind = str(args.PermissionKind)
        except Exception:
            kind = "Unknown"

        denied = False

        try:
            current_state = args.State
            state_type = type(current_state)
            deny_value = getattr(state_type, "Deny", None)
            if deny_value is not None:
                args.State = deny_value
                denied = True
        except Exception:
            pass

        if not denied:
            try:
                from Microsoft.Web.WebView2.Core import CoreWebView2PermissionState

                args.State = CoreWebView2PermissionState.Deny
                denied = True
            except Exception:
                pass

        try:
            if hasattr(args, "SavesInProfile"):
                args.SavesInProfile = False
        except Exception:
            pass

        try:
            if hasattr(args, "Handled"):
                args.Handled = True
        except Exception:
            pass

        emit(
            "navigation",
            event="Permiso bloqueado",
            from_url=str(state.get("current_url") or ""),
            to=uri,
            detail=(
                f"{kind} · denegado por WebView2"
                if denied
                else f"{kind} · intento de permiso interceptado"
            ),
        )

    try:
        core.PermissionRequested += on_permission_requested
        state["permission_handler_installed"] = True
        state["permission_handler"] = on_permission_requested
        emit("status", message="Protección de permisos WebView2 activa.")
    except Exception as exc:
        emit(
            "diagnostic",
            message=f"No se pudo instalar PermissionRequested: {exc}",
        )


def attach_popup_handler(core, state: dict[str, object]) -> None:
    if core is None:
        return

    attach_native_content_guard(core, state)
    attach_permission_handler(core, state)

    if state.get("popup_handler_installed"):
        return

    def on_new_window_requested(sender, args) -> None:
        try:
            uri = str(args.Uri or "")
        except Exception:
            uri = ""

        emit(
            "navigation",
            event="Popup / nueva ventana",
            from_url=str(state.get("current_url") or ""),
            to=uri,
            detail="Bloqueado por WebView2",
        )

        try:
            args.Handled = True
        except Exception:
            try:
                args.set_Handled(True)
            except Exception:
                pass

    try:
        core.NewWindowRequested += on_new_window_requested
        state["popup_handler_installed"] = True
        state["popup_handler"] = on_new_window_requested
        state["popup_core"] = core
        emit("status", message="Protección de popups WebView2 activa.")
    except Exception as exc:
        emit("diagnostic", message=f"No se pudo instalar NewWindowRequested: {exc}")


def install_popup_handler(window: webview.Window, state: dict[str, object]) -> None:
    try:
        control = window.native.webview
    except Exception as exc:
        emit("diagnostic", message=f"WebView2 nativo no disponible: {exc}")
        return

    try:
        core = control.CoreWebView2
    except Exception:
        core = None

    if core is not None:
        attach_popup_handler(core, state)
        return

    if state.get("popup_init_handler_installed"):
        return

    def on_initialized(sender, args) -> None:
        try:
            if hasattr(args, "IsSuccess") and not bool(args.IsSuccess):
                emit("diagnostic", message="CoreWebView2 no pudo inicializarse.")
                return
            initialized_core = sender.CoreWebView2
        except Exception as exc:
            emit("diagnostic", message=f"No se pudo obtener CoreWebView2: {exc}")
            return
        attach_popup_handler(initialized_core, state)

    try:
        control.CoreWebView2InitializationCompleted += on_initialized
        state["popup_init_handler_installed"] = True
        state["popup_init_handler"] = on_initialized
    except Exception as exc:
        emit("diagnostic", message=f"No se pudo escuchar inicialización WebView2: {exc}")


def install_navigation_guard(window: webview.Window, state: dict[str, object]) -> None:
    try:
        control = window.native.webview
    except Exception as exc:
        emit("diagnostic", message=f"Guard WebView2 no disponible: {exc}")
        return

    if state.get("navigation_handler_installed"):
        return

    def on_navigation_starting(sender, args) -> None:
        if not state.get("guard_enabled"):
            return

        try:
            uri = str(args.Uri or "")
        except Exception:
            uri = ""

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
            except Exception:
                return

        emit(
            "navigation",
            event="Redirección bloqueada",
            from_url=str(state.get("current_url") or ""),
            to=uri,
            detail="Dominio externo bloqueado; player conservado",
        )

    try:
        control.NavigationStarting += on_navigation_starting
        state["navigation_handler_installed"] = True
        state["navigation_handler"] = on_navigation_starting
    except Exception as exc:
        emit("diagnostic", message=f"No se pudo instalar NavigationStarting: {exc}")


def main() -> int:
    if len(sys.argv) < 2:
        return 2

    initial_url = sys.argv[1].strip()
    auto_player = "--auto-player" in sys.argv[2:]

    os.environ.setdefault("PYWEBVIEW_LOG", "error")

    state: dict[str, object] = {
        "current_url": initial_url,
        "user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/154.0.0.0 Safari/537.36 Edg/154.0.0.0"
        ),
        "allowed_hosts": {host_of(initial_url)} if host_of(initial_url) else set(),
        "guard_enabled": True,
        "visited_players": set(),
        "player_depth": 0,
        "popup_handler_installed": False,
        "popup_init_handler_installed": False,
        "permission_handler_installed": False,
        "native_content_guard_installed": False,
        "navigation_handler_installed": False,
    }

    window = webview.create_window(
        "Electro Vid-WebExt · WebView2",
        url=initial_url,
        maximized=True,
        resizable=True,
        text_select=True,
        confirm_close=False,
    )

    def on_before_show() -> None:
        install_navigation_guard(window, state)
        install_popup_handler(window, state)
        install_content_guard(window)
        emit("ready", url=initial_url)

    def on_loaded() -> None:
        try:
            current = window.get_current_url() or initial_url
        except Exception:
            current = str(state.get("current_url") or initial_url)

        previous = str(state.get("current_url") or "")
        state["current_url"] = current

        emit(
            "navigation",
            event="Carga completada",
            from_url=previous,
            to=current,
            detail="WebView2",
        )

        if not state.get("popup_handler_installed"):
            install_popup_handler(window, state)

        install_content_guard(window)

        try:
            ua = window.evaluate_js("navigator.userAgent")
            if isinstance(ua, str) and ua:
                state["user_agent"] = ua
        except Exception:
            pass

        frames = iframe_report(window)
        for frame in frames:
            emit(
                "iframe",
                url=frame,
                from_url=current,
            )

        emit_media_snapshot(window, state)

        if not auto_player:
            return

        depth = int(state.get("player_depth") or 0)
        if depth >= 5:
            return

        player = choose_player_iframe(frames)
        visited = state.get("visited_players")
        if not isinstance(visited, set):
            visited = set()
            state["visited_players"] = visited

        if not player or player in visited:
            return

        player_host = host_of(player)
        allowed_hosts = state.get("allowed_hosts")
        if not isinstance(allowed_hosts, set):
            allowed_hosts = set()
            state["allowed_hosts"] = allowed_hosts
        if player_host:
            allowed_hosts.add(player_host)

        visited.add(player)
        state["player_depth"] = depth + 1

        emit(
            "navigation",
            event=f"Player {depth + 1}",
            from_url=current,
            to=player,
            detail="Iframe de reproductor abierto automáticamente",
        )
        window.load_url(player)

    def on_request(request) -> None:
        url = str(getattr(request, "url", "") or "")
        if not url:
            return
        kind = kind_from_url(url)
        if not kind and not any(marker in url.lower() for marker in MEDIA_MARKERS):
            return

        headers = getattr(request, "headers", None)
        header_map: dict[str, str] = {}
        if isinstance(headers, dict):
            header_map = {str(k).lower(): str(v) for k, v in headers.items()}

        current = str(state.get("current_url") or initial_url)
        emit(
            "media",
            url=url,
            kind=kind or "Media",
            origin=f"WebView2 · {kind or 'Media'}",
            referer=header_map.get("referer") or current,
            origin_header=header_map.get("origin"),
            cookie_header=header_map.get("cookie"),
            user_agent=str(state.get("user_agent") or ""),
        )

    window.events.before_show += on_before_show
    window.events.loaded += on_loaded
    window.events.request_sent += on_request

    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = False
    webview.settings["ALLOW_DOWNLOADS"] = False
    webview.settings["IGNORE_SSL_ERRORS"] = False

    def command_loop() -> None:
        for raw in sys.stdin:
            try:
                command = json.loads(raw)
            except Exception:
                continue
            action = str(command.get("action") or "")
            try:
                if action == "load":
                    target = str(command.get("url") or "")
                    if target:
                        state["current_url"] = target
                        host = host_of(target)
                        allowed_hosts = state.get("allowed_hosts")
                        if isinstance(allowed_hosts, set) and host:
                            allowed_hosts.add(host)
                        window.load_url(target)
                elif action == "back":
                    window.evaluate_js("history.back()")
                elif action == "reload":
                    window.evaluate_js("location.reload()")
                elif action == "rescan":
                    frames = iframe_report(window)
                    current = str(state.get("current_url") or "")
                    for frame in frames:
                        emit("iframe", url=frame, from_url=current)
                    emit_media_snapshot(window, state)
                elif action == "prepare_recording":
                    script = """
                    (async () => {
                      const result = {
                        played: false,
                        quality: '',
                        backend: 'html5',
                        detail: ''
                      };

                      try {
                        if (typeof window.jwplayer === 'function') {
                          let player = null;
                          try {
                            player = window.jwplayer();
                          } catch (_) {}

                          if (player) {
                            result.backend = 'jwplayer';

                            try {
                              const levels = player.getQualityLevels?.() || [];
                              if (levels.length) {
                                let bestIndex = 0;
                                let bestScore = -1;

                                levels.forEach((level, index) => {
                                  const label = String(level?.label || '');
                                  const heightMatch = label.match(/(\\d{3,4})\\s*p/i);
                                  const height = heightMatch
                                    ? Number(heightMatch[1])
                                    : Number(level?.height || 0);
                                  const width = Number(level?.width || 0);
                                  const bitrate = Number(
                                    level?.bitrate
                                    || level?.bandwidth
                                    || 0
                                  );

                                  const score =
                                    (height * 1000000000)
                                    + (width * 1000000)
                                    + bitrate;

                                  if (score > bestScore) {
                                    bestScore = score;
                                    bestIndex = index;
                                  }
                                });

                                try {
                                  player.setCurrentQuality(bestIndex);
                                  const best = levels[bestIndex] || {};
                                  result.quality = String(
                                    best.label
                                    || best.height
                                    || ('nivel ' + bestIndex)
                                  );
                                } catch (_) {}
                              }
                            } catch (_) {}

                            try {
                              player.play(true);
                              result.played = true;
                              result.detail = 'Play enviado por JW Player API';
                            } catch (_) {}
                          }
                        }

                        if (!result.played) {
                          const videos = Array.from(
                            document.querySelectorAll('video')
                          ).sort(
                            (a, b) =>
                              (b.clientWidth * b.clientHeight)
                              - (a.clientWidth * a.clientHeight)
                          );

                          const video = videos[0];
                          if (video) {
                            result.backend = 'html5';
                            try {
                              await video.play();
                              result.played = !video.paused;
                              result.detail = 'Play enviado al elemento <video>';
                            } catch (error) {
                              result.detail = String(error);
                            }
                          } else {
                            result.detail = 'No se encontró un reproductor controlable.';
                          }
                        }
                      } catch (error) {
                        result.detail = String(error);
                      }

                      return JSON.stringify(result);
                    })();
                    """

                    raw_result = window.evaluate_js(script)
                    if isinstance(raw_result, str):
                        try:
                            result = json.loads(raw_result)
                        except json.JSONDecodeError:
                            result = {
                                "played": False,
                                "quality": "",
                                "backend": "",
                                "detail": raw_result,
                            }
                    elif isinstance(raw_result, dict):
                        result = raw_result
                    else:
                        result = {
                            "played": False,
                            "quality": "",
                            "backend": "",
                            "detail": "Respuesta inválida del reproductor.",
                        }

                    emit(
                        "player_control",
                        played=bool(result.get("played")),
                        quality=str(result.get("quality") or ""),
                        backend=str(result.get("backend") or ""),
                        detail=str(result.get("detail") or ""),
                    )
                elif action == "close":
                    window.destroy()
                    return
            except Exception as exc:
                emit("diagnostic", message=f"Comando {action} falló: {exc}")

    threading.Thread(
        target=command_loop,
        name="electro-webview2-commands",
        daemon=True,
    ).start()

    webview.start(gui="edgechromium", debug=False)
    emit("closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
