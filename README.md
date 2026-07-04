# Second Cut

Simple Windows app for shrinking media folders without manual cleanup steps.

## What v1 does

- Scans a user-selected folder and every nested subfolder.
- Supports JPEG images: `.jpg`, `.jpeg`, in any case variant.
- Supports videos: `.mp4`, `.mov`, in any case variant.
- Ignores anything already inside `.to-be-deleted`.
- Replaces successful outputs in place so the visible files keep the real names.
- Moves originals and rejected generated files into `.to-be-deleted` instead of deleting them.
- Resolves old `tmp-...` and `new-...` leftovers from the shell-script workflow.

## Requirements

- Windows
- Python 3.13+
- `ffmpeg` on `PATH`
- `ffprobe` on `PATH`
- `exiftool` on `PATH`

## Run

```powershell
python app.py
```

## Packaging

Install PyInstaller once:

```powershell
python -m pip install pyinstaller
```

Build the packaged app:

```powershell
powershell -ExecutionPolicy Bypass -File .\packaging\build.ps1
```

The build output is created under `dist\`.

## Behavior notes

- A successful JPEG shrink archives the original under `.to-be-deleted\originals\...`.
- A successful MP4 shrink keeps the original filename.
- A successful MOV shrink keeps the same basename and normalizes the final kept file to `.mp4`.
- If a generated file is larger than the current file, the current file stays in place and the generated file is moved under `.to-be-deleted\rejected-generated\...`.
- If a legacy `tmp-...` image is present and the current image is larger, the app restores the `tmp-...` file back to the real filename before continuing.
- If a legacy `new-...` video is present and is smaller than the original, the app promotes it and archives the original automatically.

