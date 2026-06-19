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
script path printed by the command. The script sets a practical PATH and then runs
`photos-tool send`; it contains no passwords. Assign a keyboard shortcut. The
workflow is: select items in Photos, press the hotkey, and let the Shortcut
surface the exit code.

Expected exit codes:

| Code | Meaning |
| --- | --- |
| 0 | success |
| 1 | preflight failure: missing tool, share problem, or Photos permission problem |
| 2 | bad arguments or config |
| 3 | export ran but some assets were skipped or errored |
| 4 | nothing selected |
| 5 | MP4 conversion failed after export succeeded |

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
3. Run `photos-tool send`. Expected on Windows: files land under `<share>\<year>\<month>\...`; the Live Photo appears as a still plus `.MOV`; the standalone video is present.
4. Check metadata on the Mac copy or Windows copy with `exiftool -G1 -time:all -gps:all <file>`. Expected: photo dates/GPS and video QuickTime creation dates are present.
5. Run `photos-tool send --jpeg --mp4`. Expected: JPEG files appear under the parallel `compat` tree, the standalone HEVC video gets an `.mp4`, and the Live Photo motion clip is not transcoded.
6. Run `photos-tool send` again with the same selection. Expected: no duplicate archive files; the report shows skipped/current rows rather than new copies.
7. Select a known cloud-only item and run `photos-tool send`. Expected: exit code 3 and a clear message about skipped items and Download Originals.
8. During a larger export, confirm the Mac stays responsive and `photos-tool` leaves no resident process after it exits.

## Capturing the authoritative report fixture

After a successful real run, sanitize one persisted report before committing it:

```bash
latest_report=$(ls -t ~/.local/state/photos-tool/logs/*-original-report.json | head -n 1)
photos-tool sanitize-report "$latest_report" tests/fixtures/report_real_sanitized.json
```

Inspect the sanitized file before committing it. The sanitizer hashes filenames,
paths, and raw Photos UUIDs and redacts GPS/location fields; if anything personal
still appears, do not commit the fixture.
