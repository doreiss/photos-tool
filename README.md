# photos-tool

[![ci](https://github.com/doreiss/photos-tool/actions/workflows/ci.yml/badge.svg)](https://github.com/doreiss/photos-tool/actions/workflows/ci.yml)

Send selected photos and videos from the Apple Photos app on a Mac to a Windows PC
on the same home network — with full metadata, Live Photos, dedup, and album/date
folders. Built so several family Macs can back up to one Windows share, by people
who don't touch a terminal.

> Status: CLI + menu-bar app with portable fake-tool coverage. The real Mac +
> Windows path is verified by the manual smoke test in `docs/manual-test.md`.

## How it works

The hard part — getting photos *out* of Apple Photos with everything intact — is done
by [osxphotos](https://github.com/RhetTbull/osxphotos). The transfer is a built-in
Windows file share. This project is the thin, safe wrapper that ties them together:

```
Photos app (select) -> menu-bar / hotkey -> photos-tool -> osxphotos export -> mounted SMB share -> Windows folders
```

- `osxphotos export --selected` reads whatever is highlighted in Photos right now.
- `--update` makes it incremental and resumable (never re-copies what's already sent).
- `--exiftool` embeds EXIF / GPS / dates; Live Photos and videos export by default.
- A clean, selected-vs-exported reconciliation by UUID catches silently-skipped
  iCloud photos and reports them (exit code 3) instead of a false success.

### What lands on the share

Each Mac writes to its **own** subfolder (named after the Mac) so photos from two
iPhones can't collide on names like `IMG_0001`:

```
<share>/<this-mac>/2024/06/IMG_0001.heic          # pristine Apple originals (the archive)
<share>/<this-mac>/2024/08/IMG_0002.heic + .mov   # Live Photo pair
<share>/<this-mac>/compat/2024/06/IMG_0001.jpeg   # optional Windows-friendly mirror
<share>/<this-mac>/compat/2024/09/VID_0003.mp4    #   (point Windows Explorer here)
```

The optional `compat/` tree (`--jpeg`/`--mp4`) is a fully Windows-openable mirror —
JPEG for every still, H.264 MP4 for every standalone video, no HEVC `.mov` — so a
Windows PC with no codecs can browse `compat/` while the main tree stays original.

## Install (each family Mac)

```bash
brew install exiftool          # required (metadata); add ffmpeg for MP4 copies:
brew install ffmpeg            # optional, only if you want --mp4

uv tool install photos-tool                 # recommended (or: pipx install photos-tool)
uv tool install 'photos-tool[gui]'          # ...with the 📷 menu-bar app
# bleeding edge: uv tool install 'git+https://github.com/doreiss/photos-tool'
```

`osxphotos` is pinned and installed automatically on macOS.

## Set up (once per Mac)

```bash
photos-tool init       # asks for the SMB URL, mount point, and a per-Mac subfolder
                       # (defaults to this Mac's name — keep it unless you have a reason)
```

Then, the three one-time macOS grants (all in `docs/windows-setup.md`):

1. **Finder → Connect to Server →** `smb://<pc>/<share>`, log in, and check
   "Remember this password in my keychain" (no password is ever stored by this tool).
2. **Full Disk Access** for the app that runs photos-tool (Terminal, or the menu-bar
   app), in System Settings → Privacy & Security. Quit and relaunch it after.
3. **Download Originals to this Mac** (Photos → Settings → iCloud) and let it finish,
   so you don't export low-res placeholders.

Then `photos-tool doctor` should be all green.

## Send

- **Menu bar (easiest):** run `photos-tool-menubar`, select photos in Photos, click
  📷 → **Send Selected Photos**. A notification tells you the result.
- **Hotkey:** `photos-tool install-shortcut` writes a launcher; put it in a one-action
  macOS Shortcut and bind a key. The notification maps the exit code to plain English.
- **Terminal:** `photos-tool send` (add `--jpeg --mp4` for the Windows-friendly mirror).

### Free space on the Mac after a backup (opt-in, recoverable)

Designed for "get photos off my Mac, but only once I'm sure they arrived." Backup and
delete are **decoupled** — send first, optionally verify the copies on the share (or on
Windows), then delete that batch's originals:

```bash
photos-tool send --album "Trip"        # 1. back up a batch (records it)
# 2. (optional) open the share / Windows and check the photos really arrived
photos-tool cleanup-last --dry-run     # 3a. preview what would be removed
photos-tool cleanup-last               # 3b. move that batch's originals to Recently Deleted
```

In the **menu-bar app**: turn on *"Offer cleanup after each backup"* to get a popup
after every ✓ (**Reveal on share… / Move to Recently Deleted / Not now**), or use
*"Clean up last backup…"* anytime. One-shot `send --remove-originals` also exists.

Either way it only acts on a clean backup, deletes **exactly** the batch's photos and
**only those re-verified present and non-empty on the share**, aborts if any don't
resolve, and moves them to **Recently Deleted (recoverable ~30 days)** via PhotoKit
(one-time **Photos** permission grant; macOS shows its own "Delete N?" confirmation).

## What CI proves (and what it can't)

CI runs on GitHub's macOS runners, which are genuinely Apple Silicon (arm64):

- ✅ The package, `osxphotos`, and `exiftool` install and run on a clean arm64 Mac,
  and the built wheel installs cleanly (the `uv tool install` path).
- ✅ The wrapper logic — config, report parsing, command construction, SMB checks,
  conversion selection, the UUID reconciliation, the cleanup gate, and the GUI's
  exit-code mapping — is unit-tested and type-checked.
- ✅ The full `send` pipeline runs with fake `osxphotos`/`ffmpeg`/`exiftool`/`mount`/
  `osascript` binaries, plus marker-gated real-tool tests on macOS.

It deliberately does **not** test the end-to-end export (no Photos library, no GUI
selection, no Full Disk Access on a runner). That is the manual smoke test on a real Mac.

## Develop

```bash
scripts/check.sh                          # ruff + pyright + pytest + actionlint + shellcheck
photos-tool plan /Volumes/Share           # print the exact osxphotos command (runs nothing)
```

Before bumping `osxphotos`, rerun the manual smoke test — its report format is part of
the safety contract (`tests/fixtures/report_real_sanitized.json` is a captured real one).

## Releasing to PyPI

Tag `vX.Y.Z` (matching `pyproject.toml`); `.github/workflows/release.yml` builds and
publishes via PyPI Trusted Publishing (OIDC, no token). One-time: register a pending
publisher on pypi.org (project `photos-tool`, owner `doreiss`, workflow `release.yml`,
environment `pypi`) before the first tag.

## Roadmap

- [x] Tool detection, export-command builder, UUID reconciliation.
- [x] `send`: export, parse the osxphotos report, reconcile, run log.
- [x] Coherent `compat/` mirror (JPEG stills + H.264 MP4s); pristine originals tree.
- [x] Per-Mac subpath so several Macs share one Windows folder safely.
- [x] Opt-in Mac-side cleanup (move exported originals to Recently Deleted).
- [x] 📷 menu-bar app; macOS Shortcut + hotkey; documented manual smoke test.
- [x] PyPI Trusted-Publishing release + clean-install CI.
- [ ] Signed/notarized `.app` bundle of the menu-bar app.

## License

MIT — see [LICENSE](LICENSE).
