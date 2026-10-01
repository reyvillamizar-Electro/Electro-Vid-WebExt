from __future__ import annotations

import json
import queue
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

ProgressCallback = Callable[[int], None]
ProgressDetailsCallback = Callable[[dict[str, object]], None]
CancelCallback = Callable[[], bool]


class DownloadCancelled(RuntimeError):
    pass


USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
BAD_CONTENT_TYPES = (
    "text/html",
    "text/plain",
    "application/json",
    "application/xml",
    "text/xml",
)


def suggested_extension(url: str, kind: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix in {".mp4", ".webm", ".mov", ".m4v", ".mkv"}:
        return suffix
    if kind.upper() in {"HLS", "DASH"} or suffix in {".m3u8", ".mpd"}:
        return ".mp4"
    return ".mp4"


def download_media(
    url: str,
    destination: str,
    kind: str,
    progress: ProgressCallback | None = None,
    progress_details: ProgressDetailsCallback | None = None,
    referer: str | None = None,
    user_agent: str | None = None,
    cookie_header: str | None = None,
    origin_header: str | None = None,
    cancelled: CancelCallback | None = None,
) -> None:
    """Download media with browser session context and validate the result."""
    suffix = Path(urlparse(url).path).suffix.lower()
    is_stream = kind.upper() in {"HLS", "DASH"} or suffix in {".m3u8", ".mpd"}

    if is_stream:
        _download_with_ffmpeg(
            url,
            destination,
            progress,
            progress_details,
            referer,
            user_agent,
            cookie_header,
            origin_header,
            cancelled,
        )
        return

    try:
        _download_direct(
            url,
            destination,
            progress,
            progress_details,
            referer,
            user_agent,
            cookie_header,
            origin_header,
            cancelled,
        )
    except DownloadCancelled:
        raise
    except Exception as direct_error:
        try:
            _download_with_ffmpeg(
                url,
                destination,
                progress,
                progress_details,
                referer,
                user_agent,
                cookie_header,
                origin_header,
                cancelled,
            )
        except DownloadCancelled:
            raise
        except Exception as ffmpeg_error:
            raise RuntimeError(
                "La descarga HTTP directa no produjo un video válido y el intento con "
                f"FFmpeg también falló.\n\nHTTP: {direct_error}\n\nFFmpeg: {ffmpeg_error}"
            ) from ffmpeg_error


def _format_progress_time(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "—"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _probe_source_duration(
    url: str,
    referer: str | None,
    user_agent: str | None,
    cookie_header: str | None,
    origin_header: str | None,
) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None

    command = [
        ffprobe,
        "-v",
        "error",
    ]

    clean_ua = _clean_header_value(user_agent)
    clean_referer = _clean_header_value(referer)
    clean_cookie = _clean_header_value(cookie_header)
    clean_origin = _clean_header_value(origin_header)

    if clean_ua:
        command.extend(["-user_agent", clean_ua])
    if clean_referer:
        command.extend(["-referer", clean_referer])

    headers: list[str] = []
    if clean_cookie:
        headers.append(f"Cookie: {clean_cookie}")
    if clean_origin:
        headers.append(f"Origin: {clean_origin}")
    if headers:
        command.extend(["-headers", "\r\n".join(headers) + "\r\n"])

    command.extend(
        [
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            url,
        ]
    )

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            return None
        value = float((completed.stdout or "").strip())
        return value if value > 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _clean_header_value(value: str | None) -> str | None:
    if not value:
        return None
    return value.replace("\r", " ").replace("\n", " ").strip()


def _headers(
    referer: str | None,
    user_agent: str | None = None,
    cookie_header: str | None = None,
    origin_header: str | None = None,
) -> dict[str, str]:
    headers = {
        "User-Agent": _clean_header_value(user_agent) or USER_AGENT,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
    }

    referer = _clean_header_value(referer)
    cookie_header = _clean_header_value(cookie_header)
    origin_header = _clean_header_value(origin_header)

    if referer:
        headers["Referer"] = referer
    if cookie_header:
        headers["Cookie"] = cookie_header
    if origin_header:
        headers["Origin"] = origin_header
    return headers


def _select_best_hls_variant(
    url: str,
    referer: str | None,
    user_agent: str | None,
    cookie_header: str | None,
    origin_header: str | None,
) -> str:
    if not urlparse(url).path.lower().endswith(".m3u8"):
        return url

    request = Request(
        url,
        headers=_headers(referer, user_agent, cookie_header, origin_header),
    )
    try:
        with urlopen(request, timeout=15) as response:
            text = response.read(2 * 1024 * 1024).decode("utf-8", errors="replace")
    except Exception:
        return url

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    variants: list[tuple[int, int, str]] = []

    for index, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue

        attrs = line.split(":", 1)[1]
        bandwidth = 0
        width = 0
        height = 0

        for part in attrs.split(","):
            key, _, value = part.partition("=")
            key = key.strip().upper()
            value = value.strip().strip('"')

            if key in {"BANDWIDTH", "AVERAGE-BANDWIDTH"} and value.isdigit():
                bandwidth = max(bandwidth, int(value))
            elif key == "RESOLUTION" and "x" in value.lower():
                left, right = value.lower().split("x", 1)
                if left.isdigit() and right.isdigit():
                    width = int(left)
                    height = int(right)

        variant_url = None
        for following in lines[index + 1:]:
            if following.startswith("#"):
                continue
            variant_url = urljoin(url, following)
            break

        if variant_url:
            variants.append((width * height, bandwidth, variant_url))

    if not variants:
        return url

    variants.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return variants[0][2]


def _fetch_hls_text(
    url: str,
    referer: str | None,
    user_agent: str | None,
    cookie_header: str | None,
    origin_header: str | None,
) -> str:
    request = Request(
        url,
        headers=_headers(referer, user_agent, cookie_header, origin_header),
    )
    with urlopen(request, timeout=20) as response:
        return response.read(8 * 1024 * 1024).decode("utf-8", errors="replace")


def _suspicious_hls_resource(url: str, playlist_url: str) -> bool:
    parsed = urlparse(url)
    suffix = Path(parsed.path).suffix.lower()
    playlist_host = (urlparse(playlist_url).hostname or "").lower()
    resource_host = (parsed.hostname or "").lower()

    # Do not broadly reject cross-domain media: CDNs are normal for HLS.
    # Only reject extensions that are clearly non-media for this workflow.
    if suffix in {".image", ".html", ".htm"}:
        return True

    # Data/blob/javascript resources are never valid FFmpeg HLS segments here.
    if parsed.scheme.lower() in {"data", "blob", "javascript"}:
        return True

    return False


def _sanitize_hls_playlist(
    playlist_url: str,
    referer: str | None,
    user_agent: str | None,
    cookie_header: str | None,
    origin_header: str | None,
) -> tuple[str, int]:
    text = _fetch_hls_text(
        playlist_url,
        referer,
        user_agent,
        cookie_header,
        origin_header,
    )

    raw_lines = text.splitlines()
    output: list[str] = []
    removed = 0

    # Tags immediately before a URI that describe only that URI. If the URI is
    # removed, these must be removed with it to keep the media playlist valid.
    uri_scoped_prefixes = (
        "#EXTINF:",
        "#EXT-X-BYTERANGE:",
        "#EXT-X-PROGRAM-DATE-TIME:",
        "#EXT-X-GAP",
    )

    for raw_line in raw_lines:
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("#"):
            # Never remove encryption/key directives. Altering them could turn
            # an encrypted stream into invalid data and is outside the purpose
            # of this sanitizer.
            encryption_tag = line.startswith(
                ("#EXT-X-KEY:", "#EXT-X-SESSION-KEY:")
            )

            # Rewrite URI="..." attributes to absolute URLs. Reject clearly
            # non-media URI attributes rather than handing them to FFmpeg.
            if 'URI="' in line:
                prefix, sep, rest = line.partition('URI="')
                if sep:
                    uri_value, quote, tail = rest.partition('"')
                    if quote:
                        absolute = urljoin(playlist_url, uri_value)
                        if _suspicious_hls_resource(absolute, playlist_url):
                            if encryption_tag:
                                raise RuntimeError(
                                    "El manifiesto usa una clave/cifrado con una "
                                    "URI no multimedia; no se modificará."
                                )
                            removed += 1
                            continue
                        line = prefix + 'URI="' + absolute + '"' + tail
            output.append(line)
            continue

        absolute = urljoin(playlist_url, line)
        if _suspicious_hls_resource(absolute, playlist_url):
            removed += 1
            while output and output[-1].startswith(uri_scoped_prefixes):
                output.pop()
            continue

        output.append(absolute)

    if not output or output[0] != "#EXTM3U":
        raise RuntimeError("El manifiesto HLS saneado no es válido.")

    if removed <= 0:
        raise RuntimeError(
            "FFmpeg rechazó el HLS, pero no se encontraron recursos no multimedia seguros de retirar."
        )

    fd, path = tempfile.mkstemp(
        prefix="electro-vid-webext-",
        suffix=".m3u8",
        text=True,
    )
    try:
        with open(fd, "w", encoding="utf-8", newline="\n", closefd=True) as handle:
            handle.write("\n".join(output))
            handle.write("\n")
    except Exception:
        Path(path).unlink(missing_ok=True)
        raise

    return path, removed


def _serve_local_hls_playlist(
    playlist_path: str,
) -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    data = Path(playlist_path).read_bytes()

    class _PlaylistHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?", 1)[0] != "/playlist.m3u8":
                self.send_error(404)
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_HEAD(self) -> None:  # noqa: N802
            if self.path.split("?", 1)[0] != "/playlist.m3u8":
                self.send_error(404)
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

        def log_message(self, format: str, *args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _PlaylistHandler)
    port = int(server.server_address[1])
    thread = threading.Thread(
        target=server.serve_forever,
        name="electro-hls-local",
        daemon=True,
    )
    thread.start()
    return server, thread, f"http://127.0.0.1:{port}/playlist.m3u8"


def temporary_download_path(destination: str) -> Path:
    target = Path(destination)
    suffix = target.suffix or ".mp4"
    return target.with_name(f"{target.stem}.part{suffix}")


def _temporary_target(destination: str) -> Path:
    return temporary_download_path(destination)


def _validate_download(path: Path, content_type: str = "") -> None:
    if not path.exists():
        raise RuntimeError("El servidor no creó ningún archivo.")

    if path.stat().st_size <= 0:
        raise RuntimeError("El servidor devolvió un archivo vacío.")

    normalized = content_type.lower().split(";", 1)[0].strip()
    if any(normalized.startswith(value) for value in BAD_CONTENT_TYPES):
        raise RuntimeError(
            f"El servidor devolvió {content_type or 'contenido no multimedia'} en vez del video."
        )

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return

    completed = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        raise RuntimeError("El archivo recibido no es un video válido según FFprobe.")

    try:
        data = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("FFprobe no pudo validar el archivo descargado.") from exc

    if not data.get("streams"):
        raise RuntimeError("El archivo descargado no contiene una pista de video.")


def _download_direct(
    url: str,
    destination: str,
    progress: ProgressCallback | None,
    progress_details: ProgressDetailsCallback | None,
    referer: str | None,
    user_agent: str | None,
    cookie_header: str | None,
    origin_header: str | None,
    cancelled: CancelCallback | None,
) -> None:
    target = Path(destination)
    temp_target = _temporary_target(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_target.unlink(missing_ok=True)

    request = Request(
        url,
        headers=_headers(referer, user_agent, cookie_header, origin_header),
    )

    try:
        with urlopen(request, timeout=30) as response:
            status = getattr(response, "status", 200)
            if status >= 400:
                raise RuntimeError(f"El servidor respondió HTTP {status}.")

            content_type = response.headers.get("Content-Type", "")
            total_header = response.headers.get("Content-Length")
            total = int(total_header) if total_header and total_header.isdigit() else 0

            if total_header == "0":
                raise RuntimeError("El servidor informó Content-Length: 0.")

            received = 0
            started_at = time.monotonic()
            with temp_target.open("wb") as output:
                while True:
                    if cancelled and cancelled():
                        raise DownloadCancelled("Descarga cancelada.")
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    received += len(chunk)
                    percent = min(99, int(received * 100 / total)) if total > 0 else -1
                    if progress and percent >= 0:
                        progress(percent)
                    if progress_details:
                        elapsed = max(time.monotonic() - started_at, 0.001)
                        progress_details(
                            {
                                "mode": "http",
                                "percent": percent,
                                "bytes": received,
                                "total_bytes": total or None,
                                "speed_bps": received / elapsed,
                                "time_seconds": None,
                                "duration_seconds": None,
                                "speed_factor": None,
                            }
                        )

        _validate_download(temp_target, content_type)
        temp_target.replace(target)
        if progress:
            progress(100)
    except Exception:
        temp_target.unlink(missing_ok=True)
        raise


def _download_with_ffmpeg(
    url: str,
    destination: str,
    progress: ProgressCallback | None,
    progress_details: ProgressDetailsCallback | None,
    referer: str | None,
    user_agent: str | None,
    cookie_header: str | None,
    origin_header: str | None,
    cancelled: CancelCallback | None,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg no está disponible en el PATH.")

    target = Path(destination)
    temp_target = _temporary_target(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_target.unlink(missing_ok=True)

    source_url = _select_best_hls_variant(
        url,
        referer,
        user_agent,
        cookie_header,
        origin_header,
    )

    duration = _probe_source_duration(
        source_url,
        referer,
        user_agent,
        cookie_header,
        origin_header,
    )

    if progress:
        progress(0 if duration else -1)

    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostats",
        "-stats_period",
        "0.5",
        "-progress",
        "pipe:1",
        "-y",
        "-user_agent",
        _clean_header_value(user_agent) or USER_AGENT,
    ]

    clean_referer = _clean_header_value(referer)
    if clean_referer:
        command.extend(["-referer", clean_referer])

    extra_headers: list[str] = []
    clean_cookie = _clean_header_value(cookie_header)
    clean_origin = _clean_header_value(origin_header)
    if clean_cookie:
        extra_headers.append(f"Cookie: {clean_cookie}")
    if clean_origin:
        extra_headers.append(f"Origin: {clean_origin}")
    if extra_headers:
        command.extend(["-headers", "\r\n".join(extra_headers) + "\r\n"])

    hls_source = urlparse(source_url).path.lower().endswith((".m3u8", ".m3u"))

    command.extend(
        [
            "-i",
            source_url,
            "-map",
            "0:v:0?",
            "-map",
            "0:a:0?",
            "-c",
            "copy",
            str(temp_target),
        ]
    )

    def _run_ffmpeg(command_to_run: list[str]) -> tuple[int, str]:
        process = subprocess.Popen(
            command_to_run,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        progress_queue: queue.Queue[str | None] = queue.Queue()

        def _read_progress() -> None:
            stream = process.stdout
            if stream is None:
                progress_queue.put(None)
                return
            try:
                for raw_line in stream:
                    progress_queue.put(raw_line.rstrip("\r\n"))
            finally:
                progress_queue.put(None)

        reader = threading.Thread(
            target=_read_progress,
            name="ffmpeg-progress",
            daemon=True,
        )
        reader.start()

        current: dict[str, str] = {}
        progress_stream_done = False

        while process.poll() is None or not progress_stream_done:
            if cancelled and cancelled():
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)
                raise DownloadCancelled("Descarga cancelada.")

            try:
                line = progress_queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if line is None:
                progress_stream_done = True
                continue

            key, sep, value = line.partition("=")
            if not sep:
                continue

            current[key] = value
            if key != "progress":
                continue

            out_time_seconds: float | None = None
            raw_us = current.get("out_time_us") or current.get("out_time_ms")
            if raw_us:
                try:
                    out_time_seconds = int(raw_us) / 1_000_000
                except ValueError:
                    pass

            total_size: int | None = None
            try:
                total_size = int(current.get("total_size", ""))
            except ValueError:
                pass

            speed_factor: float | None = None
            speed_text = current.get("speed", "").rstrip("x")
            try:
                speed_factor = float(speed_text)
            except ValueError:
                pass

            percent = -1
            if duration and out_time_seconds is not None:
                percent = max(
                    0,
                    min(99, int(out_time_seconds * 100 / duration)),
                )
                if progress:
                    progress(percent)

            if progress_details:
                progress_details(
                    {
                        "mode": "ffmpeg",
                        "percent": percent,
                        "bytes": total_size,
                        "total_bytes": None,
                        "speed_bps": None,
                        "time_seconds": out_time_seconds,
                        "duration_seconds": duration,
                        "speed_factor": speed_factor,
                    }
                )

            current = {}

        _, stderr = process.communicate()
        return process.returncode or 0, (stderr or "").strip()

    try:
        return_code, stderr = _run_ffmpeg(command)
        if (
            return_code != 0
            and hls_source
            and "not in allowed_segment_extensions" in stderr
        ):
            sanitized_path: str | None = None
            local_server: ThreadingHTTPServer | None = None
            local_thread: threading.Thread | None = None
            try:
                sanitized_path, removed = _sanitize_hls_playlist(
                    source_url,
                    referer,
                    user_agent,
                    cookie_header,
                    origin_header,
                )

                (
                    local_server,
                    local_thread,
                    local_playlist_url,
                ) = _serve_local_hls_playlist(sanitized_path)

                sanitized = list(command)
                input_index = sanitized.index("-i")
                sanitized[input_index + 1] = local_playlist_url

                # Keep FFmpeg on an HTTP input so protocol-specific options
                # such as user_agent, referer and headers remain valid. The
                # manifest itself is served only on localhost and all segment
                # URLs inside it are absolute remote URLs.
                sanitized[input_index:input_index] = [
                    "-protocol_whitelist",
                    "http,https,tcp,tls,crypto",
                ]

                temp_target.unlink(missing_ok=True)
                if progress_details:
                    progress_details(
                        {
                            "mode": "ffmpeg",
                            "percent": -1,
                            "bytes": None,
                            "total_bytes": None,
                            "speed_bps": None,
                            "time_seconds": None,
                            "duration_seconds": duration,
                            "speed_factor": None,
                            "message": (
                                f"HLS saneado: {removed} recurso(s) "
                                "no multimedia omitido(s)."
                            ),
                        }
                    )

                return_code, stderr = _run_ffmpeg(sanitized)
            finally:
                if local_server is not None:
                    try:
                        local_server.shutdown()
                    except Exception:
                        pass
                    try:
                        local_server.server_close()
                    except Exception:
                        pass
                if local_thread is not None:
                    local_thread.join(timeout=1.0)
                if sanitized_path:
                    Path(sanitized_path).unlink(missing_ok=True)

        if return_code != 0:
            message = stderr or "FFmpeg no pudo guardar la fuente."
            raise RuntimeError(message)

        _validate_download(temp_target)
        temp_target.replace(target)
        if progress:
            progress(100)
    except Exception:
        temp_target.unlink(missing_ok=True)
        raise
