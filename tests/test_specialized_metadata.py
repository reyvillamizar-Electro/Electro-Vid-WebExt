from __future__ import annotations

import unittest
from unittest.mock import patch

from electro_vid_webext.core import specialized
from electro_vid_webext.ui.main_window import MetadataWorker


class FakeYoutubeDL:
    def __init__(self, _options: dict) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def extract_info(self, _url: str, download=False) -> dict:
        return {
            "title": "Example",
            "webpage_url": "https://example.invalid/video",
            "duration": 12.4,
            "formats": [
                {"url": "https://example.invalid/1024.mp4", "format_id": "one",
                 "width": 576, "height": 1024, "ext": "mp4",
                 "vcodec": "h265", "acodec": "aac", "filesize": 1_000_000},
                {"url": "https://example.invalid/1280.mp4", "format_id": "two",
                 "width": 720, "height": 1280, "ext": "mp4",
                 "vcodec": "avc1.640028", "acodec": "aac",
                 "filesize_approx": 2_000_000},
                {"url": "https://example.invalid/1920.mp4", "format_id": "three",
                 "width": 1080, "height": 1920, "ext": "mp4",
                 "vcodec": "hev1.1.6", "acodec": "none", "tbr": 1000},
                {"url": "https://example.invalid/audio.m4a", "format_id": "audio",
                 "vcodec": "none", "acodec": "aac", "filesize": 100_000},
            ],
        }


class SpecializedMetadataTests(unittest.TestCase):
    def test_ytdlp_fields_populate_duration_codec_and_size_hints(self) -> None:
        match = specialized.ExtractorMatch(key="TikTok", name="TikTok")
        with patch.object(specialized, "detect_specific_extractor", return_value=match):
            with patch.object(specialized, "_js_runtime_options", return_value={}):
                with patch.object(specialized, "YoutubeDL", FakeYoutubeDL):
                    _, sources = specialized.extract_specialized_sources(
                        "https://example.invalid/video"
                    )

        self.assertEqual([source.duration_hint for source in sources], ["00:12"] * 3)
        self.assertEqual(
            [source.codec_hint for source in sources],
            ["H.265 / HEVC", "H.264 / AVC", "H.265 / HEVC"],
        )
        self.assertEqual(sources[0].size_hint, "976.6 KB")
        self.assertTrue(sources[1].size_hint.startswith("≈ "))
        self.assertTrue(sources[2].size_hint.startswith("≈ "))

    def test_ytdlp_rows_use_extractor_metadata_without_probing_temporary_url(self) -> None:
        match = specialized.ExtractorMatch(key="TikTok", name="TikTok")
        with patch.object(specialized, "detect_specific_extractor", return_value=match):
            with patch.object(specialized, "_js_runtime_options", return_value={}):
                with patch.object(specialized, "YoutubeDL", FakeYoutubeDL):
                    _, sources = specialized.extract_specialized_sources(
                        "https://example.invalid/video"
                    )
        with patch("electro_vid_webext.ui.main_window.read_media_metadata") as remote_probe:
            metadata = MetadataWorker._read_source(sources[0])
        remote_probe.assert_not_called()
        self.assertEqual(metadata.duration, "00:12")
        self.assertEqual(metadata.codec, "H.265 / HEVC")
        self.assertEqual(metadata.size, "976.6 KB")


if __name__ == "__main__":
    unittest.main()
