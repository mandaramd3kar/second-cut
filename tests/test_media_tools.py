from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import media_tools
from media_tools import ToolPaths


class VideoThumbnailToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tools = ToolPaths(ffmpeg="ffmpeg", ffprobe="ffprobe", exiftool="exiftool")

    def test_detects_attached_thumbnail(self) -> None:
        payload = '{"streams":[{"disposition":{"attached_pic":0}},{"disposition":{"attached_pic":1}}]}'
        with patch.object(media_tools, "_run_capture", return_value=payload):
            self.assertTrue(media_tools.video_has_attached_thumbnail(Path("video.mp4"), self.tools))

    def test_thumbnail_intermediates_are_removed(self) -> None:
        commands: list[list[str]] = []
        temp_roots: set[Path] = set()

        def fake_run(command: list[str]) -> None:
            commands.append(command)
            if command[0] == self.tools.ffmpeg:
                output_path = Path(command[-1])
                temp_roots.add(output_path.parent)
                output_path.write_bytes(b"generated")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.mp4"
            candidate = root / "candidate.mp4"
            source.write_bytes(b"source")

            with (
                patch.object(media_tools, "probe_media_resolution", return_value=(1920, 1080)),
                patch.object(media_tools, "_probe_video_stream_count", return_value=1),
                patch.object(media_tools, "_run", side_effect=fake_run),
            ):
                dimensions = media_tools.add_video_thumbnail(source, candidate, self.tools)

            self.assertEqual((192, 108), dimensions)
            self.assertEqual(b"generated", candidate.read_bytes())
            self.assertIn("thumbnail=300,scale=192:108", commands[0])
            self.assertIn("-c:v:1", commands[1])
            self.assertIn("-disposition:v:1", commands[1])
            self.assertTrue(temp_roots)
            self.assertTrue(all(not path.exists() for path in temp_roots))

    @unittest.skipUnless(
        shutil.which("ffmpeg") and shutil.which("ffprobe"),
        "media command-line tools are not installed",
    )
    def test_real_tools_attach_cover_without_hiding_main_video_stream(self) -> None:
        tools = media_tools.discover_tools(require_exiftool=False)
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.mp4"
            candidate = root / "candidate.mp4"
            media_tools._run(
                [
                    tools.ffmpeg,
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=size=320x180:rate=30",
                    "-t",
                    "1",
                    "-c:v",
                    "mpeg4",
                    "-pix_fmt",
                    "yuv420p",
                    "-metadata",
                    "creation_time=2020-01-02T03:04:05Z",
                    str(source),
                ]
            )

            dimensions = media_tools.add_video_thumbnail(source, candidate, tools)

            self.assertEqual((32, 18), dimensions)
            self.assertTrue(media_tools.video_has_attached_thumbnail(candidate, tools))
            self.assertEqual((320, 180), media_tools.probe_media_resolution(candidate, tools))
            creation_time = media_tools._run_capture(
                [
                    tools.ffprobe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format_tags=creation_time",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(candidate),
                ]
            )
            self.assertTrue(creation_time.startswith("2020-01-02T03:04:05"))
            self.assertFalse(any(root.glob("*_original")))


if __name__ == "__main__":
    unittest.main()
