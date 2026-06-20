# Manual end-to-end smoke test

This is the only layer that needs a real Photos library, Full Disk Access, and the real Windows SMB share.

## One-time Mac setup

1. Connect to the SMB share in Finder and save the password in Keychain.
2. Grant Full Disk Access to the app that launches `photos-tool`, usually Terminal.app or Shortcuts. Quit and relaunch it.
3. In Photos -> Settings -> iCloud, choose "Download Originals to this Mac" and let it finish before relying on exports.
4. Approve the first Automation prompt if `osxphotos --download-missing` asks to control Photos.
5. Be aware that on macOS 26, `osxphotos` may not read Shared Albums yet; use local library items for this smoke test.

## Shortcut trigger

Write a Shortcut-friendly launcher script:

```bash
photos-tool install-shortcut
```

Then create a macOS Shortcut with one "Run Shell Script" action that calls the
script path printed by the command. The script sets a practical PATH, runs
`photos-tool send`, and prints a one-line human status (for example
"✅ Photos sent." or "⚠️ Some photos were skipped…") that you can feed straight
into a Shortcut notification; it contains no passwords. Assign a keyboard
shortcut. The workflow is: select items in Photos, press the hotkey, and read the
notification. If you press the hotkey twice, the second run detects the first is
still going and exits without starting an overlapping export.

Expected exit codes:

| Code | Meaning |
| --- | --- |
| 0 | success |
| 1 | preflight failure: missing tool, share problem, or Photos permission problem |
| 2 | bad arguments or config |
| 3 | export ran but some assets were skipped or errored |
| 4 | nothing selected |
| 5 | a compatibility copy (JPEG or MP4) failed after the originals exported |

## Test album

Create a small album with about 10 items:

- one HEIC still
- one Live Photo
- one standalone HEVC video
- one item with GPS
- one cloud-only item if you can deliberately create one for the skip guard

## Steps

1. Run `photos-tool doctor`. Expected: required tools pass, the share is writable, Photos is readable, and any Optimize Storage risk is explicit.
2. Select the album items in Photos and run `photos-tool send --dry-run`. Expected: the selected count looks right, no files are written, and Optimize Storage warnings appear only for expected cloud-only items.
3. Run `photos-tool send`. Expected on Windows: files land under `<share>\<this-mac>\<year>\<month>\...` (note the per-Mac subfolder); the Live Photo appears as a still plus `.MOV`; the standalone video is present.
4. Check metadata on the Mac copy or Windows copy with `exiftool -G1 -time:all -gps:all <file>`. Expected: photo dates/GPS and video QuickTime creation dates are present.
5. Enable compatibility copies once in config (`photos-tool init --jpeg --mp4`, or set `[copies] jpeg = true` and `mp4 = true` in the TOML — copies are config-only, not per-run flags), then run `photos-tool send`. Expected: the `compat/` tree under your Mac's subfolder holds a `.jpeg` for every still and a `.mp4` for every standalone HEVC video, and **nothing else** — no `.heic`, no `.mov`. The main tree still holds only the originals. On Windows, every file under `compat/` opens without extra codecs.
6. Run `photos-tool send` again with the same selection. Expected: no duplicate archive files; the report shows skipped/current rows; MP4 copies report "already current".
7. Select a known cloud-only item and run `photos-tool send`. Expected: exit code 3 and a clear message about skipped items and Download Originals.
8. **Menu-bar app:** run `photos-tool-menubar`, select photos in Photos, click 📷 → Send Selected Photos. Expected: a notification ("Photos sent" / "Some photos were skipped" / "Nothing selected") matching the CLI exit code.
9. **Mac-side cleanup (opt-in, recoverable, a separate step from backup):** after a successful `photos-tool send`, run `photos-tool cleanup-last --dry-run`. Expected: it reports "Would move N backed-up original(s) to Recently Deleted … Nothing was deleted" (grant Photos access first if it says it is not authorized). Only originals whose backup copy is re-verified present **and byte-for-byte unchanged** on the share are eligible. Then, on a *throwaway* test batch, run `photos-tool cleanup-last` and confirm: the items move to Photos → Recently Deleted (recoverable ~30 days), the share copies remain intact, and a second `cleanup-last` reports "No backup recorded" (the token is consumed, so a batch can never be offered twice).
10. **Multi-Mac (if you have a second Mac):** install and `init` on a second Mac, confirm its photos land under a *different* `<share>\<other-mac>\...` subfolder, and that an `IMG_0001.heic` from each Mac coexists without overwriting.
11. During a larger export, confirm the Mac stays responsive and `photos-tool` leaves no resident process after it exits.

## Capturing the authoritative report fixture

photos-tool no longer leaves report copies on disk (they were write-only and leaked
GPS/paths). To refresh the fixture, run osxphotos directly to a throwaway report,
then sanitize it before committing:

```bash
osxphotos export /tmp/ptt-fixture --selected --report /tmp/ptt-report.json
photos-tool sanitize-report /tmp/ptt-report.json tests/fixtures/report_real_sanitized.json
```

Inspect the sanitized file before committing it. The sanitizer hashes filenames,
paths, and raw Photos UUIDs and redacts GPS/location fields; if anything personal
still appears, do not commit the fixture.
