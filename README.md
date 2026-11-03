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
- `--exiftool` embeds EXIF / GPS / dates.
- By default everything is made **Windows-viewable**: photos become JPEG and videos become H.264
  MP4 (via macOS's built-in `avconvert` — no ffmpeg), so any Windows PC opens them as-is.
- A clean, selected-vs-exported reconciliation by UUID catches silently-skipped
  iCloud photos and reports them (exit code 3) instead of a false success.

### What lands on the share

Each Mac writes to its **own** subfolder (named after the Mac) so photos from two
iPhones can't collide on names like `IMG_0001`. There are two formats, chosen once in
*"Set up connection…"* (or `[export] format` in the config):

**`viewable` (the default)** — everything opens on a stock Windows PC:

```
<share>/<this-mac>/2024/06/IMG_0001.jpeg          # a photo (the edited version, if edited)
<share>/<this-mac>/2024/09/IMG_0003.mp4           # a video, as H.264 MP4
```

- **Photos** become one JPEG each — the **edited** version when there is one — with no Live Photo
  motion or RAW. Date, GPS, keywords and people are embedded.
- **Videos** become one H.264 MP4 each (the edited version when there is one) at their original
  resolution: HEVC/HDR clips are converted with Apple's hardware encoder, H.264 clips are
  repackaged losslessly. Capture date, GPS and camera are copied back onto the MP4. Each MP4 is
  stamped with its Photos ID, so re-sending skips videos already on the share and two different
  clips with the same name can never be mistaken for each other.
- Cleanup verifies and deletes the Mac originals **against these copies**, so this format
  deliberately trades the original files — and Live Photo motion, HDR/depth, and Photos' edit
  history — for files any PC can open. JPEG/MP4 are typically larger than HEIC/HEVC.
- A video edited in Photos *after* it was sent gets its MP4 remade on the next send. Each send
  reads your selection once, so clicking around in Photos mid-send can't change what is sent.

**`original`** — the untouched Apple files (the archive):

```
<share>/<this-mac>/2024/06/IMG_0001.heic          # pristine Apple originals
<share>/<this-mac>/2024/08/IMG_0002.heic + .mov   # Live Photo pair
<share>/<this-mac>/compat/2024/06/IMG_0001.jpeg   # optional Windows-friendly mirror
<share>/<this-mac>/compat/2024/09/VID_0003.mp4    #   (point Windows Explorer here)
```

The optional `compat/` mirror (`[copies] jpeg/mp4 = true`) adds a JPEG for every still and an H.264
MP4 for every standalone HEVC video, while the main tree stays original. Windows needs Microsoft's
HEIF/HEVC extensions to open the originals themselves.

## Install

### A. Family Mac — the no-Terminal `.app` (recommended)

The menu-bar `.app` **bundles its own exiftool** (so it needs no Homebrew) and self-reinvokes its
own signed binary. Build + sign + install it with the stable signing identity so the macOS grants
persist across rebuilds — full steps in **[docs/app-install.md](docs/app-install.md)**:

```bash
./packaging/create-codesign-cert.sh   # once, so grants persist across rebuilds
./scripts/build-app.sh --install      # bundles exiftool, signs, installs to /Applications
```

### B. Developer / CLI (pip / uv)

```bash
brew install exiftool          # required for the CLI path (the .app bundles its own)

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
- **Terminal:** `photos-tool send` (the format and any copies are config settings, set once).

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
backup, never automatic. The format and JPEG/MP4 copies are config-only (set once at setup),
so the menu exposes no per-run toggles.

Either way it only acts on a clean backup, deletes **exactly** the batch's photos and
**only those re-verified present and non-empty on the share**, aborts if any don't
resolve, and moves them to **Recently Deleted (recoverable ~30 days)** via PhotoKit
(one-time **Photos** permission grant; macOS shows its own "Delete N?" confirmation).

### Residual risk: initial-write integrity

This tool verifies every backup copy by **content hash** (SHA-256 — whole-file for files
≤16 MiB, sampled windows for larger videos). That is strictly stronger than osxphotos, which
compares only file size and modification time and is **blind to same-size content corruption**.
The hash catches **all** corruption that happens *after* a copy lands on the PC (it's re-checked
before any deletion).

The one case it cannot catch is a corruption that occurs **during the very first write** to the
share, before the copy is fingerprinted — that copy is recorded as its own (corrupt) baseline, so
later checks see it as unchanged. For this to cause loss, a write would have to be corrupted in a
way that (a) defeats SMB3 packet signing and TCP checksums, (b) preserves the exact file length,
and (c) is reported by the OS as a complete, successful write — any size change or short write
instead produces an error and the original is **not** eligible for deletion. The only realistic
carrier is failing RAM or a bad cable (which would usually corrupt other work visibly too).

**Why it's accepted, not coded around:** even then, loss is neither silent nor permanent — deletion
is opt-in and a separate step, you can open the copied photos on the PC first (the menu bar reveals
representative files), and originals stay recoverable in Photos → Recently Deleted for ~30 days. The
net risk is **smaller than the PC's own disk silently bit-rotting the sole copy** over time, which
no backup tool can prevent. Every code mitigation considered (researched in depth) was either
near-inert under this tool's metadata-embedding defaults or would itself risk *refusing valid
backups* — worse than the rare, recoverable gap it would close.

## What CI proves (and what it can't)

CI runs on GitHub's macOS runners, which are genuinely Apple Silicon (arm64):

- ✅ The package, `osxphotos`, and `exiftool` install and run on a clean arm64 Mac,
  and the built wheel installs cleanly (the `uv tool install` path).
- ✅ The wrapper logic — config, report parsing, command construction, SMB checks,
  conversion selection, the UUID reconciliation, the cleanup gate, and the GUI's
  exit-code mapping — is unit-tested and type-checked.
- ✅ The full `send` pipeline runs with fake `osxphotos`/`avconvert`/`exiftool`/`mount`/
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
- [x] Windows-viewable format by default: JPEG photos + H.264 MP4 videos via macOS's built-in
      `avconvert` (no ffmpeg), stamped with their Photos ID for safe re-sends.
- [x] Per-Mac subpath so several Macs share one Windows folder safely.
- [x] Opt-in Mac-side cleanup (move exported originals to Recently Deleted).
- [x] 📷 menu-bar app; macOS Shortcut + hotkey; documented manual smoke test.
- [x] PyPI Trusted-Publishing release + clean-install CI.
- [x] No-Terminal `.app` bundle of the menu-bar app (PyInstaller; `scripts/build-app.sh`).
      Self-reinvokes so osxphotos/PhotoKit run under the app's own TCC identity; declares the
      Photos/Automation usage descriptions.
- [x] `.app` bundles its own exiftool (script + Perl lib, run via the system perl; fetched
      at build, verified by sha256) so it needs **no Homebrew** on the target Mac.
- [x] Optional stable self-signed signing identity (`packaging/create-codesign-cert.sh`) so
      the macOS grants survive rebuilds; `build-app.sh` auto-uses it when present.
- [ ] Bundle a fully self-contained exiftool (PAR `pp`) so it survives Apple removing the
      system Perl in a future macOS.

## License

MIT — see [LICENSE](LICENSE).
