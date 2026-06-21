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

The optional `compat/` tree (enable JPEG/MP4 copies in config) is a fully Windows-openable mirror —
JPEG for every still, H.264 MP4 for every standalone video, no HEVC `.mov` — so a
Windows PC with no codecs can browse `compat/` while the main tree stays original.

## Install

### A. Family Mac — the no-Terminal `.app` (recommended)

The menu-bar `.app` **bundles its own exiftool** (so it needs no Homebrew) and self-reinvokes its
own signed binary. Build + sign + install it with the stable signing identity so the macOS grants
persist across rebuilds — full steps in **[docs/app-install.md](docs/app-install.md)**:

```bash
./packaging/create-codesign-cert.sh   # once, so grants persist across rebuilds
./scripts/build-app.sh --install      # bundles exiftool, signs, installs to /Applications
```

ffmpeg is optional (only for compatibility MP4 copies); install it with `brew install ffmpeg` if
you want those.

### B. Developer / CLI (pip / uv)

```bash
brew install exiftool          # required for the CLI path (the .app bundles its own)
brew install ffmpeg            # optional, only for MP4 video copies

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

Then the one-time macOS grants (details in `docs/windows-setup.md`):

1. **Finder → Connect to Server →** `smb://<pc>/<share>`, log in, and check
   "Remember this password in my keychain" (no password is ever stored by this tool).
2. **Full Disk Access** for the app that runs photos-tool — `photos-tool.app` for the
   menu-bar app (Terminal for the CLI) — in System Settings → Privacy & Security.
   Quit and relaunch it after.
3. **Download Originals to this Mac** (Photos → Settings → iCloud) and let it finish,
   so you don't export low-res placeholders.

The menu-bar app then asks for two more grants the first time it needs them, each via
macOS's own prompt and keyed to the app (so the osxphotos children inherit them):
**Automation → Photos** (the *"photos-tool" wants to control "Photos"* prompt) on the
first **Send Selected**, so it can read which photos you picked, and **Photos** on the
first **Clean up**, for the recoverable delete. The app declares
`NSAppleEventsUsageDescription`, without which macOS silently refuses the first grant.

Then `photos-tool doctor` should be all green.

## Send

- **Menu bar (easiest):** run `photos-tool-menubar`, select photos in Photos, click
  📷 → **Send Selected Photos**. The 📷 icon and the "Last backup" line show the result
  (it stays responsive while exporting; a second click is ignored until the first finishes).
- **Hotkey:** `photos-tool install-shortcut` writes a launcher; put it in a one-action
  macOS Shortcut and bind a key. The notification maps the exit code to plain English.
- **Terminal:** `photos-tool send` (JPEG/MP4 copies are config settings, set once at init).

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

In the **menu-bar app**: use *"Clean up last backup…"* anytime. It first reveals a
real backed-up file in Finder so you can confirm the photos arrived, then offers to
move that batch's originals to Recently Deleted — a deliberate, separate step from the
backup, never automatic. JPEG/MP4 copies and removal are config-only (set once at
init in the TOML), so the menu exposes no per-run toggles.

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
- [x] No-Terminal `.app` bundle of the menu-bar app (PyInstaller, ad-hoc signed;
      `scripts/build-app.sh`). Self-reinvokes so osxphotos/PhotoKit run under the app's
      own TCC identity; declares the Photos/Automation usage descriptions.
- [x] `.app` bundles its own exiftool (script + Perl lib, run via the system perl; fetched
      at build, verified by sha256) so it needs **no Homebrew** on the target Mac.
- [x] Optional stable self-signed signing identity (`packaging/create-codesign-cert.sh`) so
      the macOS grants survive rebuilds; `build-app.sh` auto-uses it when present.
- [ ] Bundle a fully self-contained exiftool (PAR `pp`) so it survives Apple removing the
      system Perl in a future macOS.

## License

MIT — see [LICENSE](LICENSE).
