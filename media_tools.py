from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


class MediaToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolPaths:
    ffmpeg: str
    ffprobe: str
    exiftool: str


def discover_tools(
    *,
    require_ffmpeg: bool = True,
    require_ffprobe: bool = True,
    require_exiftool: bool = True,
) -> ToolPaths:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    exiftool = shutil.which("exiftool")
    missing = [
        name
        for name, value in (
            ("ffmpeg", ffmpeg if require_ffmpeg else "ok"),
            ("ffprobe", ffprobe if require_ffprobe else "ok"),
            ("exiftool", exiftool if require_exiftool else "ok"),
        )
        if not value
    ]
    if missing:
        raise MediaToolError("Missing required tools on PATH: " + ", ".join(missing))
    return ToolPaths(ffmpeg=ffmpeg or "", ffprobe=ffprobe or "", exiftool=exiftool or "")


def file_size(path: Path) -> int:
    return path.stat().st_size


def _run_process(command: list[str]) -> subprocess.CompletedProcess[str]:
    creationflags = 0
    startupinfo = None
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)

    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        creationflags=creationflags,
        startupinfo=startupinfo,
    )


def _run(command: list[str]) -> None:
    completed = _run_process(command)
    if completed.returncode == 0:
        return

    stderr = (completed.stderr or "").strip()
    stdout = (completed.stdout or "").strip()
    detail = stderr or stdout or "Unknown ffmpeg/exiftool failure"
    tail = "\n".join(detail.splitlines()[-10:])
    raise MediaToolError(tail)


def _run_capture(command: list[str]) -> str:
    completed = _run_process(command)
    if completed.returncode == 0:
        return (completed.stdout or "").strip()

    stderr = (completed.stderr or "").strip()
    stdout = (completed.stdout or "").strip()
    detail = stderr or stdout or "Unknown ffmpeg/exiftool failure"
    tail = "\n".join(detail.splitlines()[-10:])
    raise MediaToolError(tail)


def transcode_image(source: Path, candidate: Path, tools: ToolPaths) -> None:
    _run(
        [
            tools.ffmpeg,
            "-noautorotate",
            "-y",
            "-i",
            str(source),
            "-q:v",
            "4",
            str(candidate),
        ]
    )
    _run(
        [
            tools.exiftool,
            "-overwrite_original",
            "-TagsFromFile",
            str(source),
            "-all:all",
            str(candidate),
        ]
    )


def probe_video_duration(source: Path, tools: ToolPaths) -> float:
    output = _run_capture(
        [
            tools.ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(source),
        ]
    )
    try:
        duration = float(output)
    except ValueError as exc:
        raise MediaToolError(f"Could not parse video duration for {source.name}.") from exc
    if duration <= 0:
        raise MediaToolError(f"Video duration was not positive for {source.name}.")
    return duration


def probe_media_resolution(source: Path, tools: ToolPaths) -> tuple[int, int]:
    output = _run_capture(
        [
            tools.ffprobe,
            "-v",
            "error",
            "-select_streams",
            "V:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            str(source),
        ]
    )
    parts = output.split("x", 1)
    if len(parts) != 2:
        raise MediaToolError(f"Could not parse media resolution for {source.name}.")
    try:
        width = int(parts[0].strip())
        height = int(parts[1].strip())
    except ValueError as exc:
        raise MediaToolError(f"Could not parse media resolution for {source.name}.") from exc
    if width <= 0 or height <= 0:
        raise MediaToolError(f"Media resolution was not positive for {source.name}.")
    return width, height


def video_has_attached_thumbnail(source: Path, tools: ToolPaths) -> bool:
    output = _run_capture(
        [
            tools.ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v",
            "-show_entries",
            "stream_disposition=attached_pic",
            "-of",
            "json",
            str(source),
        ]
    )
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise MediaToolError(f"Could not inspect video thumbnails for {source.name}.") from exc

    return any(
        stream.get("disposition", {}).get("attached_pic") == 1
        for stream in payload.get("streams", [])
    )


def _probe_video_stream_count(source: Path, tools: ToolPaths) -> int:
    output = _run_capture(
        [
            tools.ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(source),
        ]
    )
    stream_count = len([line for line in output.splitlines() if line.strip()])
    if stream_count == 0:
        raise MediaToolError(f"No video stream found in {source.name}.")
    return stream_count


def add_video_thumbnail(source: Path, candidate: Path, tools: ToolPaths) -> tuple[int, int]:
    """Write an MP4 copy with a generated attached-picture stream.

    The caller supplies a distinct destination and decides when to promote it. All
    intermediate image and mux files live in an automatically cleaned temp folder.
    """
    if source.resolve() == candidate.resolve():
        raise ValueError("The thumbnail output must be different from the source video.")
    if candidate.exists():
        raise ValueError(f"Thumbnail output already exists: {candidate}")

    width, height = probe_media_resolution(source, tools)
    thumbnail_width = max(1, width // 10)
    thumbnail_height = max(1, height // 10)
    video_stream_count = _probe_video_stream_count(source, tools)

    candidate.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="second-cut-thumbnail-") as temp_dir:
        temp_root = Path(temp_dir)
        thumbnail_path = temp_root / "thumbnail.png"
        muxed_path = temp_root / "with-thumbnail.mp4"

        _run(
            [
                tools.ffmpeg,
                "-y",
                "-i",
                str(source),
                "-map",
                "0:V:0",
                "-vf",
                f"thumbnail=300,scale={thumbnail_width}:{thumbnail_height}",
                "-frames:v",
                "1",
                str(thumbnail_path),
            ]
        )
        _run(
            [
                tools.ffmpeg,
                "-y",
                "-i",
                str(source),
                "-i",
                str(thumbnail_path),
                "-map",
                "0",
                "-map",
                "1:v:0",
                "-map_metadata",
                "0",
                "-c",
                "copy",
                f"-c:v:{video_stream_count}",
                "png",
                f"-disposition:v:{video_stream_count}",
                "attached_pic",
                f"-metadata:s:v:{video_stream_count}",
                "title=Cover",
                f"-metadata:s:v:{video_stream_count}",
                "comment=Cover (front)",
                str(muxed_path),
            ]
        )
        shutil.move(str(muxed_path), str(candidate))

    return thumbnail_width, thumbnail_height


def transcode_video(source: Path, candidate: Path, tools: ToolPaths, preset: str, quality: int) -> str:
    qsv_command = [
        tools.ffmpeg,
        "-y",
        "-i",
        str(source),
        "-movflags",
        "use_metadata_tags",
        "-map_metadata",
        "0",
        "-map",
        "0:V:0",
        "-map",
        "0:a:0?",
        "-c:v",
        "hevc_qsv",
        "-preset",
        preset,
        "-global_quality",
        str(quality),
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(candidate),
    ]
    try:
        _run(qsv_command)
        return "hevc_qsv"
    except MediaToolError:
        if candidate.exists():
            candidate.unlink()

    fallback_command = [
        tools.ffmpeg,
        "-y",
        "-i",
        str(source),
        "-movflags",
        "use_metadata_tags",
        "-map_metadata",
        "0",
        "-map",
        "0:V:0",
        "-map",
        "0:a:0?",
        "-c:v",
        "libx265",
        "-preset",
        preset,
        "-crf",
        str(quality),
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(candidate),
    ]
    _run(fallback_command)
    return "libx265"
