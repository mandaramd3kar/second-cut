from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from media_tools import MediaToolError, ToolPaths, discover_tools, file_size, transcode_image, transcode_video

ARCHIVE_DIR_NAME = ".to-be-deleted"
ORIGINALS_DIR_NAME = "originals"
REJECTED_DIR_NAME = "rejected-generated"

IMAGE_EXTENSIONS = {".jpg", ".jpeg"}
VIDEO_EXTENSIONS = {".mp4", ".mov"}

LogFn = Callable[[str], None]
ProgressFn = Callable[["ProcessProgress"], None]


def _noop(_: str) -> None:
    return


@dataclass(frozen=True)
class WorkItem:
    source_path: Path
    media_kind: str


@dataclass(frozen=True)
class FileResult:
    source_path: Path
    media_kind: str
    action: str
    status: str
    detail: str
    archive_count: int = 0


@dataclass(frozen=True)
class ScanResult:
    root_path: Path
    work_items: list[WorkItem]
    image_count: int
    video_count: int
    archive_exists: bool


@dataclass(frozen=True)
class ProcessResult:
    root_path: Path
    image_count: int
    video_count: int
    scanned: int
    shrunk: int
    skipped: int
    failed: int
    archived: int
    results: list[FileResult] = field(default_factory=list)


@dataclass(frozen=True)
class ProcessProgress:
    phase: str
    image_count: int
    video_count: int
    scanned: int
    completed: int
    shrunk: int
    skipped: int
    failed: int
    archived: int
    latest_result: FileResult


@dataclass(frozen=True)
class DeleteResult:
    root_path: Path
    deleted: bool
    detail: str


def scan_root(root_path: str | Path) -> ScanResult:
    root = _validate_root(root_path)
    work_items: list[WorkItem] = []
    image_count = 0
    video_count = 0
    archive_exists = (root / ARCHIVE_DIR_NAME).exists()

    for directory, dirnames, filenames in os.walk(root):
        current_dir = Path(directory)
        dirnames[:] = [name for name in dirnames if name != ARCHIVE_DIR_NAME]

        for filename in filenames:
            path = current_dir / filename
            if _is_legacy_image_tmp(path) or _is_legacy_video_candidate(path):
                continue

            media_kind = _media_kind(path)
            if media_kind is None:
                continue

            work_items.append(WorkItem(source_path=path, media_kind=media_kind))
            if media_kind == "image":
                image_count += 1
            else:
                video_count += 1

    return ScanResult(
        root_path=root,
        work_items=sorted(work_items, key=lambda item: str(item.source_path).lower()),
        image_count=image_count,
        video_count=video_count,
        archive_exists=archive_exists,
    )


def process_root(
    root_path: str | Path,
    log: LogFn | None = None,
    progress: ProgressFn | None = None,
) -> ProcessResult:
    logger = log or _noop
    root = _validate_root(root_path)
    tools = discover_tools()

    logger(f"Selected root: {root}")
    logger("Resolving any legacy tmp-/new- leftovers first.")

    results: list[FileResult] = []
    skip_paths: set[Path] = set()

    for legacy_result, skip_path in _resolve_legacy_state(root, tools, logger):
        results.append(legacy_result)
        if skip_path is not None:
            skip_paths.add(skip_path)

    scan = scan_root(root)
    scanned = len(scan.work_items)
    completed = 0
    shrunk = 0
    skipped = 0
    failed = 0
    archived = 0

    for legacy_result in results:
        archived += legacy_result.archive_count
        if legacy_result.action == "failed":
            failed += 1
        if progress is not None:
            progress(
                ProcessProgress(
                    phase="cleanup",
                    image_count=scan.image_count,
                    video_count=scan.video_count,
                    scanned=scanned,
                    completed=completed,
                    shrunk=shrunk,
                    skipped=skipped,
                    failed=failed,
                    archived=archived,
                    latest_result=legacy_result,
                )
            )

    logger(f"Found {scan.image_count} image(s) and {scan.video_count} video(s) to consider.")

    for item in scan.work_items:
        if item.source_path in skip_paths:
            results.append(
                FileResult(
                    source_path=item.source_path,
                    media_kind=item.media_kind,
                    action="skipped",
                    status="ok",
                    detail="Already resolved from a legacy in-progress file.",
                    archive_count=0,
                )
            )
            skipped += 1
            completed += 1
            if progress is not None:
                progress(
                    ProcessProgress(
                        phase="process",
                        image_count=scan.image_count,
                        video_count=scan.video_count,
                        scanned=scanned,
                        completed=completed,
                        shrunk=shrunk,
                        skipped=skipped,
                        failed=failed,
                        archived=archived,
                        latest_result=results[-1],
                    )
                )
            continue

        try:
            result = _process_item(root, item, tools, logger)
        except Exception as exc:  # pragma: no cover - defensive UI safety
            result = FileResult(
                source_path=item.source_path,
                media_kind=item.media_kind,
                action="failed",
                status="error",
                detail=str(exc),
            )

        results.append(result)
        archived += result.archive_count
        if result.action == "shrunk":
            shrunk += 1
        elif result.action == "skipped":
            skipped += 1
        elif result.action == "failed":
            failed += 1
        completed += 1
        if progress is not None:
            progress(
                ProcessProgress(
                    phase="process",
                    image_count=scan.image_count,
                    video_count=scan.video_count,
                    scanned=scanned,
                    completed=completed,
                    shrunk=shrunk,
                    skipped=skipped,
                    failed=failed,
                    archived=archived,
                    latest_result=result,
                )
            )

    return ProcessResult(
        root_path=root,
        image_count=scan.image_count,
        video_count=scan.video_count,
        scanned=scanned,
        shrunk=shrunk,
        skipped=skipped,
        failed=failed,
        archived=archived,
        results=results,
    )


def delete_archive(root_path: str | Path) -> DeleteResult:
    root = _validate_root(root_path)
    archive_root = root / ARCHIVE_DIR_NAME
    if not archive_root.exists():
        return DeleteResult(root_path=root, deleted=False, detail="No .to-be-deleted folder found.")

    shutil.rmtree(archive_root)
    return DeleteResult(root_path=root, deleted=True, detail=f"Deleted {archive_root}")


def _validate_root(root_path: str | Path) -> Path:
    root = Path(root_path).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Folder not found: {root_path}")
    return root


def _media_kind(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        return "image"
    if suffix in VIDEO_EXTENSIONS:
        return "video"
    return None


def _is_legacy_image_tmp(path: Path) -> bool:
    return path.name.startswith("tmp-") and path.suffix.lower() in IMAGE_EXTENSIONS


def _parse_legacy_video_candidate(path: Path) -> tuple[Path, Path] | None:
    if not path.name.startswith("new-") or path.suffix.lower() != ".mp4":
        return None

    original_name = path.name[4:-4]
    if not original_name:
        return None

    original_suffix = Path(original_name).suffix.lower()
    if original_suffix not in VIDEO_EXTENSIONS:
        return None

    original_path = path.with_name(original_name)
    final_path = original_path if original_suffix == ".mp4" else original_path.with_suffix(".mp4")
    return original_path, final_path


def _is_legacy_video_candidate(path: Path) -> bool:
    return _parse_legacy_video_candidate(path) is not None


def _resolve_legacy_state(root: Path, tools: ToolPaths, log: LogFn) -> list[tuple[FileResult, Path | None]]:
    del tools  # kept for future extension; legacy resolution is size-based only
    resolved: list[tuple[FileResult, Path | None]] = []

    for directory, dirnames, filenames in os.walk(root):
        current_dir = Path(directory)
        dirnames[:] = [name for name in dirnames if name != ARCHIVE_DIR_NAME]

        for filename in sorted(filenames, key=str.lower):
            path = current_dir / filename

            if _is_legacy_image_tmp(path):
                resolved.extend(_resolve_legacy_image(root, path, log))
                continue

            parsed = _parse_legacy_video_candidate(path)
            if parsed is not None:
                resolved.extend(_resolve_legacy_video(root, path, parsed[0], parsed[1], log))

    return resolved


def _resolve_legacy_image(root: Path, tmp_path: Path, log: LogFn) -> list[tuple[FileResult, Path | None]]:
    active_path = tmp_path.with_name(tmp_path.name[4:])
    results: list[tuple[FileResult, Path | None]] = []

    if not active_path.exists():
        log(f"Restoring orphaned legacy original: {_display_path(root, tmp_path)}")
        tmp_path.replace(active_path)
        results.append(
            (
                FileResult(
                    source_path=active_path,
                    media_kind="image",
                    action="restored",
                    status="ok",
                    detail="Restored orphaned tmp file back to its normal filename.",
                    archive_count=0,
                ),
                None,
            )
        )
        return results

    tmp_size = file_size(tmp_path)
    active_size = file_size(active_path)

    if active_size < tmp_size:
        archive_path = _archive_path(root, ORIGINALS_DIR_NAME, tmp_path.relative_to(root))
        log(
            "Archiving verified legacy original: "
            f"{_display_path(root, tmp_path)} -> {_display_path(root, archive_path)}"
        )
        _move_to_destination(tmp_path, archive_path)
        results.append(
            (
                FileResult(
                    source_path=active_path,
                    media_kind="image",
                    action="archived",
                    status="ok",
                    detail="Moved verified tmp original into .to-be-deleted.",
                    archive_count=1,
                ),
                active_path,
            )
        )
        return results

    rejected_path = _archive_path(root, REJECTED_DIR_NAME, active_path.relative_to(root))
    log(
        "Rejecting oversized image output: "
        f"{_display_path(root, active_path)} -> {_display_path(root, rejected_path)}"
    )
    _move_to_destination(active_path, rejected_path)
    tmp_path.replace(active_path)
    results.append(
        (
            FileResult(
                source_path=active_path,
                media_kind="image",
                action="restored",
                status="ok",
                detail="Rejected larger image output and restored the legacy tmp original.",
                archive_count=1,
            ),
            None,
        )
    )
    return results


def _resolve_legacy_video(
    root: Path,
    candidate_path: Path,
    original_path: Path,
    final_path: Path,
    log: LogFn,
) -> list[tuple[FileResult, Path | None]]:
    results: list[tuple[FileResult, Path | None]] = []

    if not original_path.exists():
        if final_path.exists():
            rejected_path = _archive_path(root, REJECTED_DIR_NAME, final_path.relative_to(root))
            log(
                "Final target already exists, archiving orphaned candidate: "
                f"{_display_path(root, candidate_path)} -> {_display_path(root, rejected_path)}"
            )
            _move_to_destination(candidate_path, rejected_path)
            results.append(
                (
                    FileResult(
                        source_path=final_path,
                        media_kind="video",
                        action="archived",
                        status="ok",
                        detail="Archived orphaned new-* file because the final target already existed.",
                        archive_count=1,
                    ),
                    final_path,
                )
            )
            return results

        log(
            "Promoting orphaned new-* video to final name: "
            f"{_display_path(root, candidate_path)} -> {_display_path(root, final_path)}"
        )
        _move_to_destination(candidate_path, final_path)
        results.append(
            (
                FileResult(
                    source_path=final_path,
                    media_kind="video",
                    action="promoted",
                    status="ok",
                    detail="Promoted orphaned new-* file to its final filename.",
                    archive_count=0,
                ),
                final_path,
            )
        )
        return results

    if final_path != original_path and final_path.exists():
        rejected_path = _archive_path(root, REJECTED_DIR_NAME, final_path.relative_to(root))
        log(
            "Cannot replace MOV because target already exists, archiving candidate: "
            f"{_display_path(root, candidate_path)} -> {_display_path(root, rejected_path)}"
        )
        _move_to_destination(candidate_path, rejected_path)
        results.append(
            (
                FileResult(
                    source_path=original_path,
                    media_kind="video",
                    action="failed",
                    status="error",
                    detail="Kept original video because the final .mp4 name already existed.",
                    archive_count=1,
                ),
                None,
            )
        )
        return results

    candidate_size = file_size(candidate_path)
    original_size = file_size(original_path)

    if candidate_size < original_size:
        archive_path = _archive_path(root, ORIGINALS_DIR_NAME, original_path.relative_to(root))
        log(
            "Archiving verified legacy original video: "
            f"{_display_path(root, original_path)} -> {_display_path(root, archive_path)}"
        )
        _move_to_destination(original_path, archive_path)
        _move_to_destination(candidate_path, final_path)
        results.append(
            (
                FileResult(
                    source_path=final_path,
                    media_kind="video",
                    action="promoted",
                    status="ok",
                    detail="Promoted verified new-* video and archived the larger original.",
                    archive_count=1,
                ),
                final_path,
            )
        )
        return results

    rejected_path = _archive_path(root, REJECTED_DIR_NAME, final_path.relative_to(root))
    log(
        "Rejecting oversized legacy video output: "
        f"{_display_path(root, candidate_path)} -> {_display_path(root, rejected_path)}"
    )
    _move_to_destination(candidate_path, rejected_path)
    results.append(
        (
            FileResult(
                source_path=original_path,
                media_kind="video",
                action="archived",
                status="ok",
                detail="Archived larger new-* video and kept the original file.",
                archive_count=1,
            ),
            None,
        )
    )
    return results


def _process_item(root: Path, item: WorkItem, tools: ToolPaths, log: LogFn) -> FileResult:
    source_path = item.source_path
    if not source_path.exists():
        return FileResult(
            source_path=source_path,
            media_kind=item.media_kind,
            action="skipped",
            status="ok",
            detail="File no longer exists after legacy cleanup.",
            archive_count=0,
        )

    with tempfile.TemporaryDirectory(prefix="second-cut-") as temp_dir:
        temp_root = Path(temp_dir)

        if item.media_kind == "image":
            candidate_path = temp_root / source_path.name
            log(f"Shrinking image: {_display_path(root, source_path)}")
            try:
                transcode_image(source_path, candidate_path, tools)
            except MediaToolError as exc:
                return FileResult(
                    source_path=source_path,
                    media_kind=item.media_kind,
                    action="failed",
                    status="error",
                    detail=f"Image transcode failed: {exc}",
                    archive_count=0,
                )
            return _finalize_image(root, source_path, candidate_path, log)

        candidate_name = f"{source_path.stem}.mp4"
        candidate_path = temp_root / candidate_name
        log(f"Shrinking video: {_display_path(root, source_path)}")
        try:
            encoder = transcode_video(source_path, candidate_path, tools)
        except MediaToolError as exc:
            return FileResult(
                source_path=source_path,
                media_kind=item.media_kind,
                action="failed",
                status="error",
                detail=f"Video transcode failed: {exc}",
                archive_count=0,
            )
        return _finalize_video(root, source_path, candidate_path, encoder, log)


def _finalize_image(root: Path, source_path: Path, candidate_path: Path, log: LogFn) -> FileResult:
    candidate_size = file_size(candidate_path)
    source_size = file_size(source_path)

    if candidate_size >= source_size:
        rejected_path = _archive_path(root, REJECTED_DIR_NAME, source_path.relative_to(root))
        log(
            "Image not smaller, archiving candidate: "
            f"{_display_path(root, source_path)} -> {_display_path(root, rejected_path)}"
        )
        _move_to_destination(candidate_path, rejected_path)
        return FileResult(
            source_path=source_path,
            media_kind="image",
            action="skipped",
            status="ok",
            detail="Generated image was not smaller than the current file.",
            archive_count=1,
        )

    archive_path = _archive_path(root, ORIGINALS_DIR_NAME, source_path.relative_to(root))
    log(
        "Archiving image original: "
        f"{_display_path(root, source_path)} -> {_display_path(root, archive_path)}"
    )
    _move_to_destination(source_path, archive_path)
    _move_to_destination(candidate_path, source_path)
    return FileResult(
        source_path=source_path,
        media_kind="image",
        action="shrunk",
        status="ok",
        detail="Replaced image with a smaller version and archived the original.",
        archive_count=1,
    )


def _finalize_video(
    root: Path,
    source_path: Path,
    candidate_path: Path,
    encoder: str,
    log: LogFn,
) -> FileResult:
    final_path = source_path if source_path.suffix.lower() == ".mp4" else source_path.with_suffix(".mp4")

    if final_path != source_path and final_path.exists():
        rejected_path = _archive_path(root, REJECTED_DIR_NAME, final_path.relative_to(root))
        log(
            "Final .mp4 target already exists, archiving candidate: "
            f"{_display_path(root, source_path)} -> {_display_path(root, rejected_path)}"
        )
        _move_to_destination(candidate_path, rejected_path)
        return FileResult(
            source_path=source_path,
            media_kind="video",
            action="failed",
            status="error",
            detail="Could not replace MOV because the final .mp4 filename already existed.",
            archive_count=1,
        )

    candidate_size = file_size(candidate_path)
    source_size = file_size(source_path)

    if candidate_size >= source_size:
        rejected_path = _archive_path(root, REJECTED_DIR_NAME, final_path.relative_to(root))
        log(
            "Video not smaller, archiving candidate: "
            f"{_display_path(root, source_path)} -> {_display_path(root, rejected_path)}"
        )
        _move_to_destination(candidate_path, rejected_path)
        return FileResult(
            source_path=source_path,
            media_kind="video",
            action="skipped",
            status="ok",
            detail=f"Generated video was not smaller than the current file ({encoder}).",
            archive_count=1,
        )

    archive_path = _archive_path(root, ORIGINALS_DIR_NAME, source_path.relative_to(root))
    log(
        "Archiving video original: "
        f"{_display_path(root, source_path)} -> {_display_path(root, archive_path)}"
    )
    _move_to_destination(source_path, archive_path)
    _move_to_destination(candidate_path, final_path)
    return FileResult(
        source_path=final_path,
        media_kind="video",
        action="shrunk",
        status="ok",
        detail=f"Replaced video with a smaller MP4 and archived the original ({encoder}).",
        archive_count=1,
    )


def _archive_path(root: Path, category: str, relative_path: Path) -> Path:
    destination = root / ARCHIVE_DIR_NAME / category / relative_path
    return _unique_destination(destination)


def _unique_destination(path: Path) -> Path:
    if not path.exists():
        return path

    counter = 1
    while True:
        candidate = path.with_name(f"{path.stem}.{counter}{path.suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _move_to_destination(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() == destination.resolve():
        return
    shutil.move(str(source), str(destination))


def _display_path(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root))
    except ValueError:
        return path.name
