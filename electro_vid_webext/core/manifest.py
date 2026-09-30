from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from electro_vid_webext.core.detector import VideoSource

_WIDEVINE_IDS = ("edef8ba9-79d6-4ace-a3c8-27dcd51d21ed", "widevine")
_PLAYREADY_IDS = ("9a04f079-9840-4286-ab92-e65be0885f95", "playready")
_FAIRPLAY_IDS = ("com.apple.streamingkeydelivery", "skd:")


def inspect_manifest(source: VideoSource, timeout: float = 15.0) -> tuple[list[VideoSource], str]:
    if source.kind.upper() == "HLS":
        return _inspect_hls(source, timeout)
    if source.kind.upper() == "DASH":
        return [], _inspect_dash_protection(source, timeout)
    return [], source.protection


def _headers(source: VideoSource) -> dict[str, str]:
    headers = {
        "User-Agent": source.user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "*/*",
        "Accept-Encoding": "identity",
    }
    if source.referer:
        headers["Referer"] = source.referer
    if source.cookie_header:
        headers["Cookie"] = source.cookie_header
    if source.origin_header:
        headers["Origin"] = source.origin_header
    return headers


def _fetch_text(source: VideoSource, timeout: float) -> str:
    request = Request(source.url, headers=_headers(source))
    with urlopen(request, timeout=timeout) as response:
        return response.read(4 * 1024 * 1024).decode("utf-8", errors="replace")


def _quality_label(height: int) -> str:
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
    if height > 0:
        return f"{height}p"
    return "—"


def _protection_from_text(text: str) -> str:
    lowered = text.lower()
    if any(token in lowered for token in _WIDEVINE_IDS):
        return "DRM · Widevine"
    if any(token in lowered for token in _PLAYREADY_IDS):
        return "DRM · PlayReady"
    if any(token in lowered for token in _FAIRPLAY_IDS):
        return "DRM · FairPlay"
    return "—"


def _inspect_hls(source: VideoSource, timeout: float) -> tuple[list[VideoSource], str]:
    try:
        text = _fetch_text(source, timeout)
    except Exception:
        return [], source.protection

    protection = _protection_from_text(text)
    if protection == "—":
        for line in text.splitlines():
            upper = line.upper()
            if upper.startswith("#EXT-X-KEY:") and "METHOD=NONE" not in upper:
                protection = "Cifrado HLS"
                break

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    variants: list[VideoSource] = []

    for index, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue

        attrs = _parse_hls_attributes(line.split(":", 1)[1])
        resolution = attrs.get("RESOLUTION", "")
        width = 0
        height = 0
        if "x" in resolution.lower():
            left, right = resolution.lower().split("x", 1)
            if left.isdigit() and right.isdigit():
                width, height = int(left), int(right)

        next_url = None
        for following in lines[index + 1:]:
            if following.startswith("#"):
                continue
            next_url = urljoin(source.url, following)
            break

        if not next_url:
            continue

        bandwidth = attrs.get("AVERAGE-BANDWIDTH") or attrs.get("BANDWIDTH") or ""
        origin = "HLS variante"
        if bandwidth.isdigit():
            origin += f" · {round(int(bandwidth) / 1_000_000, 2)} Mbps"

        variants.append(
            VideoSource(
                url=next_url,
                kind="HLS",
                origin=origin,
                referer=source.referer,
                user_agent=source.user_agent,
                cookie_header=source.cookie_header,
                origin_header=source.origin_header,
                quality_hint=_quality_label(height),
                resolution_hint=f"{width}×{height}" if width and height else "—",
                protection=protection,
            )
        )

    return variants, protection


def _parse_hls_attributes(value: str) -> dict[str, str]:
    result: dict[str, str] = {}
    current = ""
    in_quotes = False
    parts: list[str] = []

    for char in value:
        if char == '"':
            in_quotes = not in_quotes
            current += char
        elif char == "," and not in_quotes:
            parts.append(current)
            current = ""
        else:
            current += char
    if current:
        parts.append(current)

    for part in parts:
        key, sep, raw = part.partition("=")
        if sep:
            result[key.strip().upper()] = raw.strip().strip('"')
    return result


def _inspect_dash_protection(source: VideoSource, timeout: float) -> str:
    try:
        text = _fetch_text(source, timeout)
    except Exception:
        return source.protection

    protection = _protection_from_text(text)
    if protection != "—":
        return protection

    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return source.protection

    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "ContentProtection":
            continue
        scheme = (element.attrib.get("schemeIdUri") or "").lower()
        if "edef8ba9" in scheme:
            return "DRM · Widevine"
        if "9a04f079" in scheme:
            return "DRM · PlayReady"

    return source.protection
