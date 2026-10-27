from __future__ import annotations

import tempfile
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from electro_vid_webext.core import downloader, specialized


class FakeResponse:
    status = 200

    def __init__(self, body: bytes, declared_size: int) -> None:
        self.body = body
        self.headers = {
            "Content-Type": "video/mp4",
            "Content-Length": str(declared_size),
        }

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size: int) -> bytes:
        body, self.body = self.body, b""
        return body


def direct_download(destination: Path, *, cancelled=None) -> None:
    with patch.object(downloader, "prepare_video_for_publish", side_effect=lambda path, **_kwargs: path):
        downloader._download_direct(
            "https://example.invalid/video.mp4",
            str(destination),
            None,
            None,
            None,
            None,
            None,
            None,
            cancelled,
        )


class FakeYoutubeDL:
    extension = ".mp4"

    def __init__(self, options: dict) -> None:
        self.options = options

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def download(self, _urls: list[str]) -> int:
        path = Path(self.options["outtmpl"].replace("%(ext)s", self.extension[1:]))
        path.write_bytes(b"new video")
        return 0


class FakeFFmpegProcess:
    def __init__(self, command: list[str], **_kwargs) -> None:
        Path(command[-1]).write_bytes(b"new video")
        self.stdout = iter(())
        self.returncode = 0

    def poll(self) -> int:
        return 0

    def communicate(self):
        return "", ""


class FakeConversionProcess:
    def __init__(self, command: list[str], **_kwargs) -> None:
        Path(command[-1]).write_bytes(b"converted video")
        self.returncode = 0

    def communicate(self, timeout=None):
        return "", ""


class FakeSlowConversionProcess(FakeConversionProcess):
    def __init__(self, command: list[str], **kwargs) -> None:
        super().__init__(command, **kwargs)
        self.killed = False

    def communicate(self, timeout=None):
        if not self.killed:
            raise subprocess.TimeoutExpired("ffmpeg", timeout)
        return "", ""

    def kill(self) -> None:
        self.killed = True


def specialized_source():
    return SimpleNamespace(
        backend="yt-dlp",
        webpage_url="https://example.invalid/watch",
        format_selector="best",
        format_id="best",
    )


class DownloadSafetyTests(unittest.TestCase):
    def test_hevc_is_converted_before_publish(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "download.mp4"
            path.write_bytes(b"original video")
            probe = SimpleNamespace(
                returncode=0, stdout='{"streams": [{"codec_name": "hevc"}]}'
            )
            with patch.object(downloader.shutil, "which", side_effect=lambda name: name):
                with patch.object(downloader.subprocess, "run", return_value=probe):
                    with patch.object(downloader.subprocess, "Popen", FakeConversionProcess):
                        with patch.object(downloader, "validate_download") as validate:
                            ready = downloader.prepare_video_for_publish(path)
            self.assertEqual(path.read_bytes(), b"original video")
            self.assertEqual(ready.read_bytes(), b"converted video")
            validate.assert_called_once_with(ready)

    def test_h264_does_not_get_reencoded(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "download.mp4"
            path.write_bytes(b"original video")
            probe = SimpleNamespace(
                returncode=0, stdout='{"streams": [{"codec_name": "h264"}]}'
            )
            with patch.object(downloader.shutil, "which", return_value="ffprobe"):
                with patch.object(downloader.subprocess, "run", return_value=probe):
                    with patch.object(downloader.subprocess, "Popen") as popen:
                        self.assertEqual(downloader.prepare_video_for_publish(path), path)
            popen.assert_not_called()

    def test_conversion_can_be_cancelled_before_publish(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "download.mp4"
            path.write_bytes(b"original video")
            probe = SimpleNamespace(
                returncode=0, stdout='{"streams": [{"codec_name": "hevc"}]}'
            )
            checks = iter((False, True))
            processes = []

            def start(command, **kwargs):
                process = FakeSlowConversionProcess(command, **kwargs)
                processes.append(process)
                return process

            with patch.object(downloader.shutil, "which", side_effect=lambda name: name):
                with patch.object(downloader.subprocess, "run", return_value=probe):
                    with patch.object(downloader.subprocess, "Popen", side_effect=start):
                        with self.assertRaises(downloader.DownloadCancelled):
                            downloader.prepare_video_for_publish(
                                path, cancelled=lambda: next(checks)
                            )
            self.assertTrue(processes[0].killed)
            self.assertEqual(path.read_bytes(), b"original video")

    def test_specialized_format_prefers_h264_at_equal_quality(self) -> None:
        common = {"acodec": "aac", "ext": "mp4", "tbr": 1000}
        h264 = specialized._format_score({**common, "vcodec": "avc1.640028"})
        hevc = specialized._format_score({**common, "vcodec": "hev1", "tbr": 2000})
        self.assertGreater(h264, hevc)

    def test_conversion_failure_keeps_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "video.mp4"
            destination.write_bytes(b"old video")
            with patch.object(downloader, "urlopen", return_value=FakeResponse(b"new video", 9)):
                with patch.object(downloader, "validate_download"):
                    with patch.object(downloader, "prepare_video_for_publish", side_effect=RuntimeError("conversion failed")):
                        with self.assertRaisesRegex(RuntimeError, "conversion failed"):
                            downloader._download_direct(
                                "https://example.invalid/video.mp4", str(destination),
                                None, None, None, None, None, None, None,
                            )
            self.assertEqual(destination.read_bytes(), b"old video")

    def test_validation_rejects_wrong_size_without_calling_ffprobe(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "video.mp4"
            path.write_bytes(b"short")
            with patch.object(downloader.shutil, "which") as which:
                with self.assertRaisesRegex(RuntimeError, "Descarga incompleta"):
                    downloader.validate_download(path, expected_bytes=100)
            which.assert_not_called()

    def test_short_http_response_keeps_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "video.mp4"
            destination.write_bytes(b"old video")
            with patch.object(downloader, "urlopen", return_value=FakeResponse(b"short", 100)):
                with patch.object(downloader, "validate_download") as validate:
                    with self.assertRaisesRegex(RuntimeError, "Descarga incompleta"):
                        direct_download(destination)
            self.assertEqual(destination.read_bytes(), b"old video")
            self.assertEqual([path.name for path in Path(folder).iterdir()], ["video.mp4"])
            validate.assert_not_called()

    def test_cancel_before_publish_keeps_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "video.mp4"
            destination.write_bytes(b"old video")
            checks = iter((False, False, True))
            with patch.object(downloader, "urlopen", return_value=FakeResponse(b"new video", 9)):
                with patch.object(downloader, "validate_download"):
                    with self.assertRaises(downloader.DownloadCancelled):
                        direct_download(destination, cancelled=lambda: next(checks))
            self.assertEqual(destination.read_bytes(), b"old video")
            self.assertEqual([path.name for path in Path(folder).iterdir()], ["video.mp4"])

    def test_success_replaces_existing_file_after_validation(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "video.mp4"
            destination.write_bytes(b"old video")
            with patch.object(downloader, "urlopen", return_value=FakeResponse(b"new video", 9)):
                with patch.object(downloader, "validate_download") as validate:
                    direct_download(destination)
            self.assertEqual(destination.read_bytes(), b"new video")
            self.assertEqual([path.name for path in Path(folder).iterdir()], ["video.mp4"])
            validate.assert_called_once()

    def test_ffmpeg_success_publishes_only_after_validation(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "video.mp4"
            destination.write_bytes(b"old video")
            with patch.object(downloader.shutil, "which", return_value="ffmpeg"):
                with patch.object(downloader, "_select_best_hls_variant", return_value="https://example.invalid/video.mp4"):
                    with patch.object(downloader, "_probe_source_duration", return_value=None):
                        with patch.object(downloader.subprocess, "Popen", FakeFFmpegProcess):
                            with patch.object(downloader, "validate_download") as validate:
                                with patch.object(downloader, "prepare_video_for_publish", side_effect=lambda path, **_kwargs: path):
                                    downloader._download_with_ffmpeg(
                                        "https://example.invalid/video.mp4",
                                        str(destination),
                                        None, None, None, None, None, None, None,
                                    )
            self.assertEqual(destination.read_bytes(), b"new video")
            self.assertEqual([path.name for path in Path(folder).iterdir()], ["video.mp4"])
            validate.assert_called_once()

    def test_invalid_ytdlp_result_keeps_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "video.mp4"
            destination.write_bytes(b"old video")
            with patch.object(specialized, "YoutubeDL", FakeYoutubeDL):
                with patch.object(specialized, "_js_runtime_options", return_value={}):
                    with patch.object(specialized, "validate_download", side_effect=RuntimeError("invalid")):
                        with self.assertRaisesRegex(RuntimeError, "invalid"):
                            specialized.download_specialized(specialized_source(), str(destination))
            self.assertEqual(destination.read_bytes(), b"old video")
            self.assertEqual([path.name for path in Path(folder).iterdir()], ["video.mp4"])

    def test_valid_ytdlp_result_replaces_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "video.mp4"
            destination.write_bytes(b"old video")
            with patch.object(specialized, "YoutubeDL", FakeYoutubeDL):
                with patch.object(specialized, "_js_runtime_options", return_value={}):
                    with patch.object(specialized, "validate_download") as validate:
                        with patch.object(specialized, "prepare_video_for_publish", side_effect=lambda path, **_kwargs: path):
                            specialized.download_specialized(specialized_source(), str(destination))
            self.assertEqual(destination.read_bytes(), b"new video")
            self.assertEqual([path.name for path in Path(folder).iterdir()], ["video.mp4"])
            validate.assert_called_once()

    def test_locked_ytdlp_destination_keeps_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "video.mp4"
            destination.write_bytes(b"old video")
            with patch.object(specialized, "YoutubeDL", FakeYoutubeDL):
                with patch.object(specialized, "_js_runtime_options", return_value={}):
                    with patch.object(specialized, "validate_download"):
                        with patch.object(specialized, "prepare_video_for_publish", side_effect=lambda path, **_kwargs: path):
                            with patch.object(Path, "replace", side_effect=PermissionError("locked")):
                                with self.assertRaisesRegex(PermissionError, "locked"):
                                    specialized.download_specialized(specialized_source(), str(destination))
            self.assertEqual(destination.read_bytes(), b"old video")
            self.assertEqual([path.name for path in Path(folder).iterdir()], ["video.mp4"])

    def test_ytdlp_result_with_wrong_extension_is_not_published(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "video.mp4"
            destination.write_bytes(b"old video")
            with patch.object(specialized, "YoutubeDL", FakeYoutubeDL):
                with patch.object(FakeYoutubeDL, "extension", ".webm"):
                    with patch.object(specialized, "_js_runtime_options", return_value={}):
                        with patch.object(specialized, "validate_download"):
                            with self.assertRaisesRegex(RuntimeError, "mal etiquetado"):
                                specialized.download_specialized(specialized_source(), str(destination))
            self.assertEqual(destination.read_bytes(), b"old video")
            self.assertEqual([path.name for path in Path(folder).iterdir()], ["video.mp4"])


if __name__ == "__main__":
    unittest.main()
