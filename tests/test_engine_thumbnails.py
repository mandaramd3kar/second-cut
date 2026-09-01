from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import engine
from engine import AppSettings, ScanItem
from media_tools import ToolPaths


class ThumbnailEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = ToolPaths(ffmpeg="ffmpeg", ffprobe="ffprobe", exiftool="exiftool")

    def test_thumbnail_setting_defaults_to_enabled(self) -> None:
        self.assertTrue(AppSettings().add_video_thumbnails)

    def test_standalone_pass_archives_original_and_cleans_temp_output(self) -> None:
        generated_paths: list[Path] = []

        def fake_add(source: Path, candidate: Path, _tools: ToolPaths) -> tuple[int, int]:
            generated_paths.append(candidate)
            candidate.write_bytes(b"video-with-thumbnail")
            return 128, 72

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "clip.mp4"
            source.write_bytes(b"original-video")

            with (
                patch.object(engine, "discover_tools", return_value=self.tools),
                patch.object(engine, "_video_file_metrics", return_value=(14, (1280, 720), 1.0)),
                patch.object(engine, "video_has_attached_thumbnail", return_value=False),
                patch.object(engine, "add_video_thumbnail", side_effect=fake_add),
            ):
                result = engine.add_thumbnails_root(root)

            self.assertEqual(1, result.thumbnails_added)
            self.assertEqual(b"video-with-thumbnail", source.read_bytes())
            self.assertEqual(b"original-video", (root / ".to-be-deleted" / "originals" / "clip.mp4").read_bytes())
            self.assertTrue(generated_paths)
            self.assertTrue(all(not path.parent.exists() for path in generated_paths))

    def test_compaction_postprocesses_video_when_enabled(self) -> None:
        def fake_transcode(
            source: Path,
            candidate: Path,
            _tools: ToolPaths,
            preset: str,
            quality: int,
        ) -> str:
            del preset, quality
            candidate.write_bytes(source.read_bytes()[:50])
            return "test-encoder"

        def fake_add(source: Path, candidate: Path, _tools: ToolPaths) -> tuple[int, int]:
            shutil.copyfile(source, candidate)
            candidate.write_bytes(candidate.read_bytes() + b"thumbnail")
            return 10, 10

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "clip.mp4"
            source.write_bytes(b"x" * 100)
            item = ScanItem(
                source_path=source,
                media_kind="video",
                size_bytes=100,
                resolution=(100, 100),
                duration_seconds=10.0,
                mb_per_10_seconds=1.0,
            )

            with (
                patch.object(engine, "transcode_video", side_effect=fake_transcode),
                patch.object(engine, "add_video_thumbnail", side_effect=fake_add),
                patch.object(engine, "probe_media_resolution", return_value=(100, 100)),
                patch.object(engine, "discover_tools", return_value=self.tools),
            ):
                result = engine._process_item(root, item, self.tools, AppSettings(), lambda _message: None)

            self.assertEqual("shrunk", result.action)
            self.assertTrue(result.thumbnail_added)
            self.assertIn("attached a thumbnail", result.detail)
            self.assertEqual(59, source.stat().st_size)

    def test_compaction_does_not_generate_thumbnail_when_disabled(self) -> None:
        def fake_transcode(
            source: Path,
            candidate: Path,
            _tools: ToolPaths,
            preset: str,
            quality: int,
        ) -> str:
            del preset, quality
            candidate.write_bytes(source.read_bytes()[:50])
            return "test-encoder"

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "clip.mp4"
            source.write_bytes(b"x" * 100)
            item = ScanItem(source_path=source, media_kind="video", size_bytes=100)

            with (
                patch.object(engine, "transcode_video", side_effect=fake_transcode),
                patch.object(engine, "add_video_thumbnail") as add_thumbnail,
                patch.object(engine, "probe_media_resolution", return_value=(100, 100)),
                patch.object(engine, "discover_tools", return_value=self.tools),
            ):
                result = engine._process_item(
                    root,
                    item,
                    self.tools,
                    AppSettings(add_video_thumbnails=False),
                    lambda _message: None,
                )

            self.assertEqual("shrunk", result.action)
            self.assertFalse(result.thumbnail_added)
            add_thumbnail.assert_not_called()


if __name__ == "__main__":
    unittest.main()
