from __future__ import annotations

import base64
import json
import os
import sys
import threading
import time
from pathlib import Path
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


def attach_popup_handler(core, state: dict[str, object]) -> None:
    if core is None or state.get("popup_handler_installed"):
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


class BrowserMediaRecorder:
    def __init__(self, window: webview.Window) -> None:
        self.window = window
        self._lock = threading.Lock()
        self._active = False
        self._stopping = False
        self._destination = ""
        self._handle = None
        self._worker: threading.Thread | None = None

    def start(self, destination: str, silent: bool = True) -> None:
        with self._lock:
            if self._active:
                emit("recording_error", message="Ya hay una grabación activa.")
                return

        target = Path(destination)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            handle = target.open("wb")
        except Exception as exc:
            emit("recording_error", message=f"No se pudo crear el archivo: {exc}")
            return

        script = """
        (() => {
          if (window.__ELECTRO_REC && window.__ELECTRO_REC.active) {
            return JSON.stringify({ok:false,error:'Ya hay una grabación activa.'});
          }

          const videos = Array.from(document.querySelectorAll('video'))
            .sort((a, b) =>
              (b.clientWidth * b.clientHeight)
              - (a.clientWidth * a.clientHeight)
            );
          const video = videos[0];

          if (!video) {
            return JSON.stringify({
              ok:false,
              error:'No se encontró un elemento <video> en el reproductor.'
            });
          }
          if (typeof video.captureStream !== 'function') {
            return JSON.stringify({
              ok:false,
              error:'Este reproductor no expone captureStream().'
            });
          }

          let stream;
          try {
            stream = video.captureStream();
          } catch (error) {
            return JSON.stringify({
              ok:false,
              error:'captureStream() falló: ' + String(error)
            });
          }

          const videoTracks = stream.getVideoTracks();
          const audioTracks = stream.getAudioTracks();
          if (!videoTracks.length) {
            return JSON.stringify({
              ok:false,
              error:'captureStream() no entregó una pista de video.'
            });
          }

          const candidates = [
            'video/webm;codecs=vp9,opus',
            'video/webm;codecs=vp8,opus',
            'video/webm'
          ];
          const mimeType =
            candidates.find(type => MediaRecorder.isTypeSupported(type)) || '';

          let recorder;
          try {
            recorder = mimeType
              ? new MediaRecorder(stream, {
                  mimeType,
                  videoBitsPerSecond: 8000000
                })
              : new MediaRecorder(stream, {
                  videoBitsPerSecond: 8000000
                });
          } catch (error) {
            return JSON.stringify({
              ok:false,
              error:'MediaRecorder no pudo iniciarse: ' + String(error)
            });
          }

          const previousMuted = video.muted;
          const chunks = [];

          recorder.ondataavailable = event => {
            if (event.data && event.data.size > 0) {
              chunks.push(event.data);
            }
          };

          recorder.onerror = event => {
            window.__ELECTRO_REC.error = String(
              event.error || event || 'Error de MediaRecorder'
            );
          };

          recorder.onstop = () => {
            window.__ELECTRO_REC.stopped = true;
            try {
              video.muted = previousMuted;
            } catch (_) {}
          };

          window.__ELECTRO_REC = {
            active: true,
            stopped: false,
            error: '',
            recorder,
            stream,
            video,
            previousMuted,
            chunks
          };

          try {
            recorder.start(1000);
          } catch (error) {
            window.__ELECTRO_REC.active = false;
            return JSON.stringify({
              ok:false,
              error:'No se pudo iniciar MediaRecorder: ' + String(error)
            });
          }

          if (__SILENT__) {
            try { video.muted = true; } catch (_) {}
          }

          return JSON.stringify({
            ok:true,
            mimeType:recorder.mimeType || 'video/webm',
            videoTracks:videoTracks.length,
            audioTracks:audioTracks.length,
            silent:__SILENT__,
            paused:video.paused
          });
        })();
        """.replaceAll("__SILENT__", silent ? "true" : "false")

        try:
            raw = self.window.evaluate_js(script)
            result = json.loads(raw) if isinstance(raw, str) else raw
        except Exception as exc:
            handle.close()
            target.unlink(missing_ok=True)
            emit("recording_error", message=f"No se pudo iniciar la captura: {exc}")
            return

        if not isinstance(result, dict) or not result.get("ok"):
            handle.close()
            target.unlink(missing_ok=True)
            message = (
                str(result.get("error"))
                if isinstance(result, dict)
                else "Respuesta inválida del navegador."
            )
            emit("recording_error", message=message)
            return

        with self._lock:
            self._active = True
            self._stopping = False
            self._destination = str(target)
            self._handle = handle

        emit(
            "recording_started",
            destination=str(target),
            mime_type=str(result.get("mimeType") or "video/webm"),
            video_tracks=int(result.get("videoTracks") or 0),
            audio_tracks=int(result.get("audioTracks") or 0),
            silent=bool(result.get("silent")),
            paused=bool(result.get("paused")),
        )

        self._worker = threading.Thread(
            target=self._drain_loop,
            name="webview2-media-recorder",
            daemon=True,
        )
        self._worker.start()

    def stop(self) -> None:
        with self._lock:
            if not self._active:
                emit("recording_error", message="No hay una grabación activa.")
                return
            self._stopping = True

        try:
            self.window.evaluate_js(
                """
                (() => {
                  const rec = window.__ELECTRO_REC;
                  if (!rec || !rec.recorder) return false;
                  rec.active = false;
                  if (rec.recorder.state !== 'inactive') {
                    rec.recorder.requestData();
                    rec.recorder.stop();
                  }
                  return true;
                })();
                """
            )
            emit("recording_stopping")
        except Exception as exc:
            emit("recording_error", message=f"No se pudo detener MediaRecorder: {exc}")

    def abort(self) -> None:
        with self._lock:
            destination = self._destination
            handle = self._handle
            self._active = False
            self._stopping = False
            self._destination = ""
            self._handle = None
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
        if destination:
            try:
                Path(destination).unlink(missing_ok=True)
            except OSError:
                pass

    def _drain_loop(self) -> None:
        try:
            while True:
                packet = self._take_chunk()
                if packet.get("error"):
                    raise RuntimeError(str(packet["error"]))

                payload = str(packet.get("data") or "")
                if payload:
                    data = base64.b64decode(payload)
                    with self._lock:
                        handle = self._handle
                    if handle is None:
                        return
                    handle.write(data)
                    handle.flush()

                stopped = bool(packet.get("stopped"))
                empty = not bool(packet.get("has_more"))
                with self._lock:
                    stopping = self._stopping

                if stopping and stopped and empty:
                    break

                time.sleep(0.20 if payload else 0.50)

            with self._lock:
                destination = self._destination
                handle = self._handle
                self._active = False
                self._stopping = False
                self._destination = ""
                self._handle = None

            if handle is not None:
                handle.close()

            if destination:
                emit("recording_saved", path=destination)
        except Exception as exc:
            self.abort()
            emit("recording_error", message=f"Error durante la grabación: {exc}")

    def _take_chunk(self) -> dict[str, object]:
        raw = self.window.evaluate_js(
            """
            (async () => {
              const rec = window.__ELECTRO_REC;
              if (!rec) {
                return JSON.stringify({
                  data:'',
                  has_more:false,
                  stopped:true,
                  error:'El grabador del navegador desapareció.'
                });
              }

              if (rec.error) {
                return JSON.stringify({
                  data:'',
                  has_more:rec.chunks.length > 0,
                  stopped:rec.stopped,
                  error:rec.error
                });
              }

              const blob = rec.chunks.shift();
              if (!blob) {
                return JSON.stringify({
                  data:'',
                  has_more:false,
                  stopped:rec.stopped,
                  error:''
                });
              }

              const buffer = await blob.arrayBuffer();
              const bytes = new Uint8Array(buffer);
              let binary = '';
              const step = 0x8000;
              for (let i = 0; i < bytes.length; i += step) {
                binary += String.fromCharCode(
                  ...bytes.subarray(i, i + step)
                );
              }

              return JSON.stringify({
                data:btoa(binary),
                has_more:rec.chunks.length > 0,
                stopped:rec.stopped,
                error:''
              });
            })();
            """
        )
        if isinstance(raw, str):
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        return raw if isinstance(raw, dict) else {}


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

    media_recorder = BrowserMediaRecorder(window)

    def on_before_show() -> None:
        install_navigation_guard(window, state)
        install_popup_handler(window, state)
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
                elif action == "start_recording":
                    destination = str(command.get("destination") or "")
                    silent = bool(command.get("silent", True))
                    media_recorder.start(destination, silent=silent)
                elif action == "stop_recording":
                    media_recorder.stop()
                elif action == "close":
                    media_recorder.abort()
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
