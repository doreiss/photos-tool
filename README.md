# photos-tool

[![ci](https://github.com/doreiss/photos-tool/actions/workflows/ci.yml/badge.svg)](https://github.com/doreiss/photos-tool/actions/workflows/ci.yml)

Send selected photos and videos from the Apple Photos app on a Mac to a Windows PC
on the same home network — with full metadata, Live Photos, dedup, and album/date
folders. Built for family use, not the public.

> Status: CLI implementation with portable fake-tool coverage. The real Mac +
> Windows path is verified by the manual smoke test in `docs/manual-test.md`.

## How it works

The hard part — getting photos *out* of Apple Photos with everything intact — is done
by [osxphotos](https://github.com/RhetTbull/osxphotos). The transfer is a built-in
Windows file share. This project is the thin, safe wrapper that ties them together:

```
Photos app (select) -> hotkey/Shortcut -> photos-tool -> osxphotos export -> mounted SMB share -> Windows folders
```

- `osxphotos export --selected` reads whatever is highlighted in Photos right now.
- `--update` makes it incremental and resumable (never re-copies what's already sent).
- `--exiftool` embeds EXIF / GPS / dates; Live Photos and videos export by default.
- Optional JPEG/MP4 copies for Windows machines without the HEIC/HEVC codecs.

See `docs/windows-setup.md`, `docs/manual-test.md`, and
`docs/implementation-audit.md` for the operator setup, the one manual test CI
cannot perform, and the current evidence checklist.

## What CI proves (and what it can't)

CI runs on GitHub's macOS runners, which are genuinely Apple Silicon (arm64). It gives
real confidence that this works on a **generic Apple Silicon MacBook**:

- ✅ The package, `osxphotos`, and `exiftool` install and run on a clean arm64 Mac.
- ✅ The wrapper logic — config parsing, report parsing, command construction,
  SMB checks, conversion selection, and the selected-vs-exported reconciliation
  that catches silently-skipped iCloud photos — is unit-tested and type-checked.
- ✅ The full `send` orchestration runs in CI with fake `osxphotos`, `ffmpeg`,
  `ffprobe`, `exiftool`, `mount`, and `osascript` binaries on PATH.
- ✅ Marker-gated real-tool tests run on macOS when `ffmpeg`/`exiftool` are present.

It deliberately does **not** claim to test the end-to-end export, because a CI runner
has no Photos library, no GUI selection, and no Full Disk Access. That step is verified
by a documented manual smoke test on a real Mac.

## Requirements

- Apple Silicon Mac, macOS 13+ (developed on macOS 26).
- Python 3.10+.
- `osxphotos==0.76.1` and `exiftool` (`osxphotos` is pinned by this package on
  macOS; `brew install exiftool` installs the metadata tool).
- `ffmpeg` only if you want MP4 video copies (`brew install ffmpeg`).

## Develop

```bash
scripts/check.sh        # creates .venv, runs ruff + pyright + pytest (mirrors CI)
photos-tool check       # verify the external tools are installed
photos-tool init --non-interactive --smb-url smb://192.168.1.50/FamilyPhotos --mount-point /Volumes/FamilyPhotos
photos-tool install-shortcut
photos-tool doctor      # diagnose tools, permissions, share, and iCloud risk
photos-tool send --dry-run
photos-tool send
photos-tool send --jpeg --mp4
photos-tool sanitize-report ~/.local/state/photos-tool/logs/REPORT.json tests/fixtures/report_real_sanitized.json
photos-tool plan /Volumes/FamilyPhotos          # print the export command (runs nothing)
photos-tool plan /Volumes/FamilyPhotos --album "Summer Trip" --jpeg
```

Before bumping `osxphotos`, rerun the manual smoke test. Its report format is part
of the safety contract.

## Before the first real run (the traps that silently lose photos)

1. Turn off iCloud "Optimize Mac Storage" (Photos → Settings → iCloud →
   "Download Originals to this Mac") and let it finish — otherwise you export
   low-res placeholders.
2. Grant Full Disk Access to the terminal/app that launches `osxphotos`.
3. On Windows, share a folder to an authenticated user account, not guest
   (Windows 11 24H2 disables guest shares and requires SMB signing).
4. To view HEIC/HEVC on Windows, install the free HEIF + paid ($0.99) HEVC
   extensions, or use VLC — or send JPEG/MP4 copies.

## Roadmap

- [x] Project scaffold + Apple Silicon CI.
- [x] Tool detection, export-command builder, count reconciliation (tested).
- [x] `send` command: run the export, parse the osxphotos report, reconcile counts.
- [x] Optional JPEG (osxphotos parallel `compat/` export) and MP4 (ffmpeg) compatibility copies with
      metadata copy-through.
- [x] macOS Shortcut + hotkey trigger documentation; documented manual smoke test.
- [ ] Optional menu-bar launcher.

## License

MIT — see [LICENSE](LICENSE).
