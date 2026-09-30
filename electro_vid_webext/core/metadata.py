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
    resolution: str = "—"
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


def _quality_label(width: int | None, height: int | None) -> str:
    if not width or not height:
        return "—"

    if height >= 2160:
        return "2160p / 4K"
    if height >= 1440:
        return "1440p"
    if height >= 1080:
        return "1080p"
    if height >= 720:
        return "720p"
    if height >= 576:
        return "576p"
    if height >= 480:
        return "480p"
    if height >= 360:
        return "360p"
    if height >= 240:
        return "240p"
    return f"{height}p"


def _request_headers(
    referer: str | None,
    user_agent: str | None,
    cookie_header: str | None,
    origin_header: str | None,
) -> dict[str, str]:
    headers = {
        "User-Agent": user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "*/*",
    }
    if referer:
        headers["Referer"] = referer
    if cookie_header:
        headers["Cookie"] = cookie_header
    if origin_header:
        headers["Origin"] = origin_header
    return headers


def _http_size(
    url: str,
    timeout: float = 8.0,
    *,
    referer: str | None = None,
    user_agent: str | None = None,
    cookie_header: str | None = None,
    origin_header: str | None = None,
) -> int | None:
    headers = _request_headers(referer, user_agent, cookie_header, origin_header)
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


def _probe_with_ffprobe(
    url: str,
    timeout: float = 20.0,
    *,
    referer: str | None = None,
    user_agent: str | None = None,
    cookie_header: str | None = None,
    origin_header: str | None = None,
) -> tuple[float | None, str, str, str]:
    executable = shutil.which("ffprobe")
    if not executable:
        return None, "—", "—", "—"

    command = [
        executable,
        "-v",
        "error",
    ]

    if user_agent:
        command.extend(["-user_agent", user_agent])
    if referer:
        command.extend(["-referer", referer])

    extra_headers: list[str] = []
    if cookie_header:
        extra_headers.append(f"Cookie: {cookie_header}")
    if origin_header:
        extra_headers.append(f"Origin: {origin_header}")
    if extra_headers:
        command.extend(["-headers", "\r\n".join(extra_headers) + "\r\n"])

    command.extend([
        "-show_entries",
        "format=duration:stream=codec_type,codec_name,width,height",
        "-of",
        "json",
        url,
    ])

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
            return None, "—", "—", "—"
        data = json.loads(completed.stdout or "{}")
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None, "—", "—", "—"

    duration: float | None = None
    try:
        duration = float(data.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        pass

    resolution = "—"
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
            resolution = f"{width}×{height}"
            quality = _quality_label(width, height)
        break

    return duration, quality, resolution, codec


def _metadata_from_url(url: str) -> tuple[str, str]:
    resolution_match = re.search(r"(?<!\d)(\d{3,4})[xX×](\d{3,4})(?!\d)", url)
    if resolution_match:
        width = int(resolution_match.group(1))
        height = int(resolution_match.group(2))
        return _quality_label(width, height), f"{width}×{height}"

    vertical = re.search(
        r"(?<!\d)(2160|1440|1080|720|576|480|360|240)p(?!\d)",
        url,
        re.IGNORECASE,
    )
    if vertical:
        height = int(vertical.group(1))
        label = "2160p / 4K" if height == 2160 else f"{height}p"
        return label, "—"

    return "—", "—"


def read_media_metadata(
    url: str,
    *,
    referer: str | None = None,
    user_agent: str | None = None,
    cookie_header: str | None = None,
    origin_header: str | None = None,
) -> MediaMetadata:
    size = _http_size(
        url,
        referer=referer,
        user_agent=user_agent,
        cookie_header=cookie_header,
        origin_header=origin_header,
    )
    duration, quality, resolution, codec = _probe_with_ffprobe(
        url,
        referer=referer,
        user_agent=user_agent,
        cookie_header=cookie_header,
        origin_header=origin_header,
    )

    if quality == "—" and resolution == "—":
        quality, resolution = _metadata_from_url(url)

    return MediaMetadata(
        duration=_format_duration(duration),
        quality=quality,
        resolution=resolution,
        codec=codec,
        size=_format_size(size),
    )


def ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None
