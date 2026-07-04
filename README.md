# Second Cut

Simple desktop app for shrinking media folders without manual cleanup steps.

## What v1 does

- Scans a user-selected folder and every nested subfolder.
- Supports scan filters for images only, videos only, or both.
- Supports JPEG images: `.jpg`, `.jpeg`, in any case variant.
- Supports videos: `.mp4`, `.mov`, in any case variant.
- Qualifies images for processing only when they exceed the configured size threshold.
- Qualifies videos for processing only when their measured MB per 10 seconds exceeds the configured threshold.
- Ignores anything already inside `.to-be-deleted`.
- Replaces successful outputs in place so the visible files keep the real names.
- Moves originals and rejected generated files into `.to-be-deleted` instead of deleting them.
- Resolves old `tmp-...` and `new-...` leftovers from the shell-script workflow.

## Requirements

- Python 3.13+
- `ffmpeg` on `PATH`
- `ffprobe` on `PATH`
- `exiftool` on `PATH`
- Tkinter available in the local Python install

Platform notes:
- Windows is the primary tested target today.
- macOS and Linux now have packaging support as well, but media tool installation and GUI behavior still need validation on those platforms.

## Run

```powershell
python app.py
```

Advanced settings are available from the `Advanced...` button in the app. They stay hidden from the main screen by default and let you change:

- minimum image size threshold
- minimum video MB-per-10-seconds threshold
- video preset: `veryfast`, `fast`, `medium`, `slow`, `veryslow`
- video quality, defaulting to `34`

Video encoder notes:
- `hevc_qsv`: Intel Quick Sync HEVC hardware encoding through `ffmpeg`; usually the fastest option when supported by the machine.
- `libx265`: software HEVC encoding fallback; slower, but works when Quick Sync is unavailable.
- preset slider: faster presets usually finish sooner and compress less aggressively; slower presets usually take longer and may reduce size further.
- quality slider: lower values usually preserve more quality and create larger files; higher values usually compress more and may reduce quality more visibly.

## Packaging

Install PyInstaller once:

```powershell
python -m pip install pyinstaller
```

Build the packaged app on Windows:

```powershell
powershell -ExecutionPolicy Bypass -File .\packaging\build.ps1
```

Build the packaged app on macOS or Linux:

```sh
sh ./packaging/build.sh
```

The build output is created under `dist\`.

## Behavior notes

- A successful JPEG shrink archives the original under `.to-be-deleted\originals\...`.
- A successful MP4 shrink keeps the original filename.
- A successful MOV shrink keeps the same basename and normalizes the final kept file to `.mp4`.
- If a generated file is larger than the current file, the current file stays in place and the generated file is moved under `.to-be-deleted\rejected-generated\...`.
- If a legacy `tmp-...` image is present and the current image is larger, the app restores the `tmp-...` file back to the real filename before continuing.
- If a legacy `new-...` video is present and is smaller than the original, the app promotes it and archives the original automatically.

