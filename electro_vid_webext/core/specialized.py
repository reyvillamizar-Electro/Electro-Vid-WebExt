from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from yt_dlp import YoutubeDL
from yt_dlp.extractor import gen_extractor_classes

from electro_vid_webext.core.detector import VideoSource


ProgressCallback = Callable[[int], None]
ProgressDetailsCallback = Callable[[dict[str, object]], None]
CancelCallback = Callable[[], bool]


class SpecializedDownloadCancelled(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExtractorMatch:
    key: str
    name: str


def detect_specific_extractor(url: str) -> ExtractorMatch | None:
    for extractor_class in gen_extractor_classes():
        name = str(getattr(extractor_class, "IE_NAME", "") or "")
        if name.lower() == "generic":
            continue
        try:
            if extractor_class.suitable(url):
                key = extractor_class.ie_key()
                return ExtractorMatch(key=key, name=name or key)
        except Exception:
            continue
    return None


def extract_specialized_sources(url: str) -> tuple[ExtractorMatch, list[VideoSource]]:
    match = detect_specific_extractor(url)
    if match is None:
        raise RuntimeError("yt-dlp no encontró un extractor específico para esta URL.")

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 20,
    }

    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)

    if not isinstance(info, dict):
        raise RuntimeError("yt-dlp no devolvió información utilizable.")

    if info.get("_type") in {"playlist", "multi_video"}:
        entries = [entry for entry in (info.get("entries") or []) if isinstance(entry, dict)]
        if not entries:
            raise RuntimeError("yt-dlp no encontró videos en esta URL.")
        info = entries[0]

    formats = [fmt for fmt in (info.get("formats") or []) if isinstance(fmt, dict)]
    video_formats = [
        fmt
        for fmt in formats
        if fmt.get("url")
        and str(fmt.get("vcodec") or "none").lower() != "none"
        and not fmt.get("has_drm")
    ]
    if not video_formats:
        raise RuntimeError("yt-dlp no encontró formatos de video reproducibles.")

    audio_formats = [
        fmt
        for fmt in formats
        if fmt.get("url")
        and str(fmt.get("vcodec") or "none").lower() == "none"
        and str(fmt.get("acodec") or "none").lower() != "none"
        and not fmt.get("has_drm")
    ]
    best_audio = max(
        audio_formats,
        key=lambda fmt: (
            float(fmt.get("abr") or 0),
            float(fmt.get("tbr") or 0),
            int(fmt.get("filesize") or fmt.get("filesize_approx") or 0),
        ),
        default=None,
    )

    # Keep one practical candidate per vertical resolution. Prefer formats with
    # audio already included, then MP4, then the highest bitrate.
    by_height: dict[int, dict] = {}
    for fmt in video_formats:
        height = int(fmt.get("height") or 0)
        if height <= 0:
            continue
        current = by_height.get(height)
        score = _format_score(fmt)
        if current is None or score > _format_score(current):
            by_height[height] = fmt

    if not by_height:
        # Fallback to a small set of distinct format IDs when no resolution is exposed.
        selected = video_formats[:8]
    else:
        selected = [by_height[h] for h in sorted(by_height)]

    title = str(info.get("title") or "Video")
    webpage_url = str(info.get("webpage_url") or url)
    extractor_key = str(info.get("extractor_key") or match.key)

    sources: list[VideoSource] = []
    for fmt in selected:
        format_id = str(fmt.get("format_id") or "")
        direct_url = str(fmt.get("url") or "")
        if not format_id or not direct_url:
            continue

        width = int(fmt.get("width") or 0)
        height = int(fmt.get("height") or 0)
        ext = str(fmt.get("ext") or "mp4").upper()
        has_audio = str(fmt.get("acodec") or "none").lower() != "none"

        headers = fmt.get("http_headers") if isinstance(fmt.get("http_headers"), dict) else {}
        user_agent = str(headers.get("User-Agent") or "") or None
        referer = str(headers.get("Referer") or webpage_url) or None
        origin_header = str(headers.get("Origin") or "") or None

        audio_url = None
        if not has_audio and best_audio is not None:
            audio_url = str(best_audio.get("url") or "") or None

        if has_audio:
            selector = format_id
        elif ext.lower() == "mp4":
            selector = (
                f"{format_id}+bestaudio[ext=m4a]/"
                f"{format_id}+bestaudio/{format_id}"
            )
        elif ext.lower() == "webm":
            selector = (
                f"{format_id}+bestaudio[ext=webm]/"
                f"{format_id}+bestaudio/{format_id}"
            )
        else:
            selector = f"{format_id}+bestaudio/{format_id}"

        sources.append(
            VideoSource(
                url=direct_url,
                kind=ext,
                origin=f"yt-dlp · {extractor_key} · formato {format_id}",
                referer=referer,
                user_agent=user_agent,
                origin_header=origin_header,
                quality_hint=_quality_label(height),
                resolution_hint=f"{width}×{height}" if width and height else "—",
                protection="—",
                backend="yt-dlp",
                extractor_key=extractor_key,
                format_id=format_id,
                format_selector=selector,
                webpage_url=webpage_url,
                title=title,
                audio_url=audio_url,
            )
        )

    if not sources:
        raise RuntimeError("yt-dlp encontró el video, pero no formatos utilizables.")

    return match, sources


def download_specialized(
    source: VideoSource,
    destination: str,
    *,
    progress: ProgressCallback | None = None,
    progress_details: ProgressDetailsCallback | None = None,
    cancelled: CancelCallback | None = None,
) -> None:
    if source.backend != "yt-dlp" or not source.webpage_url:
        raise RuntimeError("La fuente no pertenece a un extractor yt-dlp.")

    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        prefix="electro-vid-webext-ytdlp-",
        dir=str(target.parent),
    ) as temp_dir:
        temp_path = Path(temp_dir)
        outtmpl = str(temp_path / "download.%(ext)s")

        def hook(data: dict) -> None:
            if cancelled and cancelled():
                raise SpecializedDownloadCancelled("Descarga cancelada.")

            status = data.get("status")
            if status != "downloading":
                return

            downloaded = int(data.get("downloaded_bytes") or 0)
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            total_int = int(total) if isinstance(total, (int, float)) and total > 0 else None
            percent = int(downloaded * 100 / total_int) if total_int else -1

            if progress and percent >= 0:
                progress(min(99, max(0, percent)))

            if progress_details:
                progress_details(
                    {
                        "mode": "yt-dlp",
                        "percent": percent,
                        "bytes": downloaded,
                        "total_bytes": total_int,
                        "speed_bps": data.get("speed"),
                        "eta_seconds": data.get("eta"),
                    }
                )

        options = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "format": source.format_selector or source.format_id or "bestvideo+bestaudio/best",
            "outtmpl": outtmpl,
            "merge_output_format": "mp4",
            "progress_hooks": [hook],
            "socket_timeout": 30,
            "overwrites": True,
        }

        try:
            with YoutubeDL(options) as ydl:
                ydl.download([source.webpage_url])
        except SpecializedDownloadCancelled:
            raise
        except Exception as exc:
            if cancelled and cancelled():
                raise SpecializedDownloadCancelled("Descarga cancelada.") from exc
            raise RuntimeError(f"yt-dlp no pudo descargar el formato seleccionado: {exc}") from exc

        candidates = [
            path
            for path in temp_path.iterdir()
            if path.is_file() and not path.name.endswith((".part", ".ytdl"))
        ]
        if not candidates:
            raise RuntimeError("yt-dlp terminó, pero no produjo un archivo final.")

        result = max(candidates, key=lambda path: path.stat().st_size)
        if result.stat().st_size <= 0:
            raise RuntimeError("yt-dlp produjo un archivo vacío.")

        if target.exists():
            target.unlink()
        shutil.move(str(result), str(target))

        if progress:
            progress(100)


def _format_score(fmt: dict) -> tuple[int, int, float, int]:
    has_audio = int(str(fmt.get("acodec") or "none").lower() != "none")
    is_mp4 = int(str(fmt.get("ext") or "").lower() == "mp4")
    bitrate = float(fmt.get("tbr") or fmt.get("vbr") or 0)
    size = int(fmt.get("filesize") or fmt.get("filesize_approx") or 0)
    return has_audio, is_mp4, bitrate, size


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
    if height >= 240:
        return "240p"
    return f"{height}p" if height > 0 else "—"
