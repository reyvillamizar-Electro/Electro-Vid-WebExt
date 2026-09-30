from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ProgressCallback = Callable[[int], None]


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
) -> None:
    suffix = Path(urlparse(url).path).suffix.lower()
    if kind.upper() in {"HLS", "DASH"} or suffix in {".m3u8", ".mpd"}:
        _download_stream(url, destination, progress)
    else:
        _download_direct(url, destination, progress)


def _download_direct(
    url: str,
    destination: str,
    progress: ProgressCallback | None,
) -> None:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Accept": "*/*",
        },
    )
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        with urlopen(request, timeout=30) as response, target.open("wb") as output:
            total_header = response.headers.get("Content-Length")
            total = int(total_header) if total_header and total_header.isdigit() else 0
            received = 0

            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                received += len(chunk)
                if progress and total > 0:
                    progress(min(100, int(received * 100 / total)))

        if progress:
            progress(100)
    except Exception:
        target.unlink(missing_ok=True)
        raise


def _download_stream(
    url: str,
    destination: str,
    progress: ProgressCallback | None,
) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "Esta fuente es HLS/DASH y requiere ffmpeg para descargarla."
        )

    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    if progress:
        progress(-1)

    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        url,
        "-c",
        "copy",
        str(target),
    ]

    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        target.unlink(missing_ok=True)
        message = completed.stderr.strip() or "FFmpeg no pudo guardar el stream."
        raise RuntimeError(message)

    if progress:
        progress(100)
