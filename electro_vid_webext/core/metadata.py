from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@dataclass(frozen=True, slots=True)
class MediaMetadata:
    duration: str = "—"
    quality: str = "—"
    codec: str = "—"
    size: str = "—"


def _format_duration(seconds: float | None) -> str:
    if not seconds or seconds <= 0:
        return "—"
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _format_size(size_bytes: int | None) -> str:
    if not size_bytes or size_bytes <= 0:
        return "—"
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return "—"


def _http_size(url: str, timeout: float = 8.0) -> int | None:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "*/*",
    }
    try:
        request = Request(url, headers=headers, method="HEAD")
        with urlopen(request, timeout=timeout) as response:
            length = response.headers.get("Content-Length")
            if length and length.isdigit():
                return int(length)
    except (HTTPError, URLError, TimeoutError, OSError):
        pass

    try:
        range_headers = dict(headers)
        range_headers["Range"] = "bytes=0-0"
        request = Request(url, headers=range_headers)
        with urlopen(request, timeout=timeout) as response:
            content_range = response.headers.get("Content-Range", "")
            if "/" in content_range:
                total = content_range.rsplit("/", 1)[-1]
                if total.isdigit():
                    return int(total)
            length = response.headers.get("Content-Length")
            if length and length.isdigit() and response.status == 200:
                return int(length)
    except (HTTPError, URLError, TimeoutError, OSError):
        pass
    return None


def _probe_with_ffprobe(url: str, timeout: float = 20.0) -> tuple[float | None, str, str]:
    executable = shutil.which("ffprobe")
    if not executable:
        return None, "—", "—"

    command = [
        executable,
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type,codec_name,codec_long_name,width,height",
        "-of",
        "json",
        url,
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0:
            return None, "—", "—"
        data = json.loads(completed.stdout or "{}")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None, "—", "—"

    duration: float | None = None
    try:
        duration = float(data.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        pass

    quality = "—"
    codec = "—"
    codec_labels = {
        "h264": "H.264 / AVC",
        "hevc": "H.265 / HEVC",
        "av1": "AV1",
        "vp9": "VP9",
        "vp8": "VP8",
        "mpeg4": "MPEG-4",
        "mpeg2video": "MPEG-2",
        "theora": "Theora",
    }

    for stream in data.get("streams", []):
        if stream.get("codec_type") != "video":
            continue

        codec_name = str(stream.get("codec_name") or "").lower()
        if codec_name:
            codec = codec_labels.get(codec_name, codec_name.upper())

        width = stream.get("width")
        height = stream.get("height")
        if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
            quality = f"{width}×{height}"
        break

    return duration, quality, codec


def _quality_from_url(url: str) -> str:
    resolution = re.search(r"(?<!\d)(\d{3,4})[xX×](\d{3,4})(?!\d)", url)
    if resolution:
        return f"{resolution.group(1)}×{resolution.group(2)}"

    vertical = re.search(r"(?<!\d)(2160|1440|1080|720|576|480|360|240)p(?!\d)", url, re.IGNORECASE)
    if vertical:
        return f"{vertical.group(1)}p"
    return "—"


def read_media_metadata(url: str) -> MediaMetadata:
    size = _http_size(url)
    duration, quality, codec = _probe_with_ffprobe(url)
    if quality == "—":
        quality = _quality_from_url(url)
    return MediaMetadata(
        duration=_format_duration(duration),
        quality=quality,
        codec=codec,
        size=_format_size(size),
    )


def ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None
