from __future__ import annotations

import argparse
import base64
import json
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

import websocket


def emit(event_type: str, **payload: Any) -> None:
    print(
        json.dumps({"type": event_type, **payload}, ensure_ascii=False),
        flush=True,
    )


class CDPClient:
    def __init__(self, ws_url: str, origin: str) -> None:
        self.ws = websocket.create_connection(
            ws_url,
            timeout=8,
            origin=origin,
        )
        self._next_id = 1

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        message_id = self._next_id
        self._next_id += 1
        self.ws.send(
            json.dumps(
                {
                    "id": message_id,
                    "method": method,
                    "params": params or {},
                }
            )
        )

        while True:
            raw = self.ws.recv()
            message = json.loads(raw)
            if message.get("id") == message_id:
                if "error" in message:
                    raise RuntimeError(str(message["error"]))
                return message.get("result") or {}

    def evaluate(self, expression: str, await_promise: bool = False) -> Any:
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": await_promise,
                "returnByValue": True,
                "userGesture": True,
            },
        )
        remote = result.get("result") or {}
        if remote.get("subtype") == "error":
            raise RuntimeError(str(remote.get("description") or "JavaScript error"))
        if "value" in remote:
            return remote["value"]
        return None


def json_get(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=4) as response:
        return json.loads(response.read().decode("utf-8"))


def find_target(port: int, timeout: float = 10.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error = ""

    while time.monotonic() < deadline:
        try:
            targets = json_get(f"http://127.0.0.1:{port}/json/list")
            pages = [
                target
                for target in targets
                if target.get("type") == "page"
                and target.get("webSocketDebuggerUrl")
            ]
            if pages:
                # The WebView2 app has one top-level page; prefer the currently
                # visible player URL rather than devtools/internal pages.
                pages.sort(
                    key=lambda target: (
                        str(target.get("url") or "").startswith(("http://", "https://")),
                        "/v/" in str(target.get("url") or ""),
                        len(str(target.get("url") or "")),
                    ),
                    reverse=True,
                )
                return pages[0]
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.25)

    raise RuntimeError(
        "No se pudo conectar con el WebView2 activo."
        + (f" Detalle: {last_error}" if last_error else "")
    )


START_SCRIPT = r"""
(() => {
  if (window.__ELECTRO_EXTERNAL_REC?.active) {
    return {ok:false,error:'Ya hay una grabación activa.'};
  }

  const videos = Array.from(document.querySelectorAll('video'))
    .filter(video => video.readyState >= 2)
    .sort(
      (a, b) =>
        (b.videoWidth * b.videoHeight || b.clientWidth * b.clientHeight)
        - (a.videoWidth * a.videoHeight || a.clientWidth * a.clientHeight)
    );

  const video = videos[0];
  if (!video) {
    return {ok:false,error:'No se encontró un <video> listo en el reproductor.'};
  }

  if (typeof video.captureStream !== 'function') {
    return {ok:false,error:'Este WebView2 no expone captureStream() para el video.'};
  }

  let stream;
  try {
    stream = video.captureStream();
  } catch (error) {
    return {ok:false,error:'captureStream() falló: ' + String(error)};
  }

  const videoTracks = stream.getVideoTracks();
  const audioTracks = stream.getAudioTracks();

  if (!videoTracks.length) {
    return {ok:false,error:'captureStream() no entregó pista de video.'};
  }

  const mimeCandidates = [
    'video/webm;codecs=vp9,opus',
    'video/webm;codecs=vp8,opus',
    'video/webm'
  ];
  const mimeType =
    mimeCandidates.find(type => MediaRecorder.isTypeSupported(type)) || '';

  let recorder;
  try {
    recorder = mimeType
      ? new MediaRecorder(stream, {mimeType, videoBitsPerSecond: 8000000})
      : new MediaRecorder(stream, {videoBitsPerSecond: 8000000});
  } catch (error) {
    return {ok:false,error:'MediaRecorder no pudo iniciarse: ' + String(error)};
  }

  const previousMuted = video.muted;
  const chunks = [];

  recorder.ondataavailable = event => {
    if (event.data && event.data.size > 0) {
      chunks.push(event.data);
    }
  };

  recorder.onerror = event => {
    window.__ELECTRO_EXTERNAL_REC.error =
      String(event.error || event || 'Error de MediaRecorder');
  };

  recorder.onstop = () => {
    const state = window.__ELECTRO_EXTERNAL_REC;
    if (state) {
      state.active = false;
      state.stopped = true;
    }
    try {
      video.muted = previousMuted;
    } catch (_) {}
  };

  window.__ELECTRO_EXTERNAL_REC = {
    active:true,
    stopped:false,
    error:'',
    recorder,
    stream,
    video,
    previousMuted,
    chunks
  };

  try {
    recorder.start(1000);
  } catch (error) {
    window.__ELECTRO_EXTERNAL_REC.active = false;
    return {ok:false,error:'No se pudo iniciar MediaRecorder: ' + String(error)};
  }

  if (__SILENT__) {
    try {
      video.muted = true;
    } catch (_) {}
  }

  return {
    ok:true,
    paused:video.paused,
    currentTime:Number(video.currentTime || 0),
    width:Number(video.videoWidth || video.clientWidth || 0),
    height:Number(video.videoHeight || video.clientHeight || 0),
    videoTracks:videoTracks.length,
    audioTracks:audioTracks.length,
    mimeType:recorder.mimeType || 'video/webm',
    silent:__SILENT__
  };
})()
"""


TAKE_CHUNK_SCRIPT = r"""
(async () => {
  const rec = window.__ELECTRO_EXTERNAL_REC;
  if (!rec) {
    return {
      data:'',
      hasMore:false,
      stopped:true,
      error:'El grabador del navegador ya no existe.'
    };
  }

  if (rec.error) {
    return {
      data:'',
      hasMore:rec.chunks.length > 0,
      stopped:rec.stopped,
      error:rec.error
    };
  }

  const blob = rec.chunks.shift();
  if (!blob) {
    return {
      data:'',
      hasMore:false,
      stopped:rec.stopped,
      error:''
    };
  }

  const buffer = await blob.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  let binary = '';
  const step = 0x8000;
  for (let i = 0; i < bytes.length; i += step) {
    binary += String.fromCharCode(...bytes.subarray(i, i + step));
  }

  return {
    data:btoa(binary),
    hasMore:rec.chunks.length > 0,
    stopped:rec.stopped,
    error:''
  };
})()
"""


STOP_SCRIPT = r"""
(() => {
  const rec = window.__ELECTRO_EXTERNAL_REC;
  if (!rec || !rec.recorder) {
    return {ok:false,error:'No hay una grabación activa.'};
  }

  try {
    if (rec.recorder.state !== 'inactive') {
      rec.recorder.requestData();
      rec.recorder.stop();
    }
    return {ok:true};
  } catch (error) {
    return {ok:false,error:String(error)};
  }
})()
"""


def command_watcher(stop_event: threading.Event) -> None:
    for line in sys.stdin:
        if line.strip().lower() in {"stop", "q", "quit"}:
            stop_event.set()
            return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--silent", action="store_true")
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)

    stop_event = threading.Event()
    threading.Thread(
        target=command_watcher,
        args=(stop_event,),
        name="recorder-command-watcher",
        daemon=True,
    ).start()

    client: CDPClient | None = None
    handle = None

    try:
        target = find_target(args.port)
        ws_url = str(target["webSocketDebuggerUrl"])
        origin = f"http://127.0.0.1:{args.port}"
        client = CDPClient(ws_url, origin)
        client.call("Runtime.enable")

        script = START_SCRIPT.replace(
            "__SILENT__",
            "true" if args.silent else "false",
        )
        started = client.evaluate(script)
        if not isinstance(started, dict) or not started.get("ok"):
            message = (
                str(started.get("error"))
                if isinstance(started, dict)
                else "Respuesta inválida del navegador."
            )
            raise RuntimeError(message)

        handle = output.open("wb")

        emit(
            "started",
            output=str(output),
            target_url=str(target.get("url") or ""),
            paused=bool(started.get("paused")),
            current_time=float(started.get("currentTime") or 0),
            width=int(started.get("width") or 0),
            height=int(started.get("height") or 0),
            video_tracks=int(started.get("videoTracks") or 0),
            audio_tracks=int(started.get("audioTracks") or 0),
            mime_type=str(started.get("mimeType") or "video/webm"),
            silent=bool(started.get("silent")),
        )

        while not stop_event.is_set():
            packet = client.evaluate(TAKE_CHUNK_SCRIPT, await_promise=True)
            if not isinstance(packet, dict):
                packet = {}
            error = str(packet.get("error") or "")
            if error:
                raise RuntimeError(error)
            payload = str(packet.get("data") or "")
            if payload:
                handle.write(base64.b64decode(payload))
                handle.flush()
            time.sleep(0.15 if payload else 0.35)

        stopped = client.evaluate(STOP_SCRIPT)
        if not isinstance(stopped, dict) or not stopped.get("ok"):
            raise RuntimeError(
                str(stopped.get("error") if isinstance(stopped, dict) else "")
                or "No se pudo detener MediaRecorder."
            )

        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            packet = client.evaluate(TAKE_CHUNK_SCRIPT, await_promise=True)
            if not isinstance(packet, dict):
                packet = {}
            error = str(packet.get("error") or "")
            if error:
                raise RuntimeError(error)
            payload = str(packet.get("data") or "")
            if payload:
                handle.write(base64.b64decode(payload))
                handle.flush()

            if bool(packet.get("stopped")) and not bool(packet.get("hasMore")) and not payload:
                break
            time.sleep(0.15)

        handle.close()
        handle = None

        if not output.exists() or output.stat().st_size <= 0:
            raise RuntimeError("La grabación terminó sin producir datos.")

        emit("finished", output=str(output), size=output.stat().st_size)
        return 0

    except Exception as exc:
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
        try:
            output.unlink(missing_ok=True)
        except OSError:
            pass
        emit("error", message=str(exc))
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
