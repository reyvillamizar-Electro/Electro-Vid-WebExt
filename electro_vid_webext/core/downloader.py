from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

ProgressCallback = Callable[[int], None]
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
            with temp_target.open("wb") as output:
                while True:
                    if cancelled and cancelled():
                        raise DownloadCancelled("Descarga cancelada.")
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    output.write(chunk)
                    received += len(chunk)
                    if progress and total > 0:
                        progress(min(99, int(received * 100 / total)))

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

    if progress:
        progress(-1)

    source_url = _select_best_hls_variant(
        url,
        referer,
        user_agent,
        cookie_header,
        origin_header,
    )

    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
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

    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        while process.poll() is None:
            if cancelled and cancelled():
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)
                raise DownloadCancelled("Descarga cancelada.")
            try:
                process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                pass

        stdout, stderr = process.communicate()
        if process.returncode != 0:
            message = (stderr or "").strip() or "FFmpeg no pudo guardar la fuente."
            raise RuntimeError(message)

        _validate_download(temp_target)
        temp_target.replace(target)
        if progress:
            progress(100)
    except Exception:
        temp_target.unlink(missing_ok=True)
        raise
