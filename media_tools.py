from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


class MediaToolError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolPaths:
    ffmpeg: str
    ffprobe: str
    exiftool: str


def discover_tools() -> ToolPaths:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    exiftool = shutil.which("exiftool")
    missing = [
        name
        for name, value in (
            ("ffmpeg", ffmpeg),
            ("ffprobe", ffprobe),
            ("exiftool", exiftool),
        )
        if not value
    ]
    if missing:
        raise MediaToolError(
            "Missing required tools on PATH: " + ", ".join(missing)
        )
    return ToolPaths(ffmpeg=ffmpeg, ffprobe=ffprobe, exiftool=exiftool)


def file_size(path: Path) -> int:
    return path.stat().st_size


def _run(command: list[str]) -> None:
    creationflags = 0
    startupinfo = None
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)

    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        creationflags=creationflags,
        startupinfo=startupinfo,
    )
    if completed.returncode == 0:
        return

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


def transcode_video(source: Path, candidate: Path, tools: ToolPaths) -> str:
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
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c:v",
        "hevc_qsv",
        "-preset",
        "veryfast",
        "-global_quality",
        "34",
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
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c:v",
        "libx265",
        "-preset",
        "medium",
        "-crf",
        "30",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(candidate),
    ]
    _run(fallback_command)
    return "libx265"
