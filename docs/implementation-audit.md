# Implementation audit

This maps the handoff requirements to current evidence. It is intentionally separate
from the README so the remaining manual proof does not get lost.

## Implemented and locally verified

- `photos-tool check`, `plan`, `send`, `doctor`, and `init` are wired in the CLI.
- `photos-tool install-shortcut` writes a no-secrets launcher script for a macOS
  Shortcut and prints the exact command to put in the Shortcut action.
- `photos-tool sanitize-report` sanitizes live JSON/CSV reports for fixture capture,
  including raw UUIDs, paths, filename-like fields, and GPS/location fields.
- `send` performs tool preflight, SMB mount/write checks, Photos count preflight,
  real export, JSON report parsing, reconciliation, report persistence, run logging,
  optional JPEG compatibility export to `compat/`, and optional MP4 conversion.
- Exit codes match the Shortcut contract: `0`, `1`, `2`, `3`, `4`, and `5`.
- Report parsing supports JSON and CSV, boolean text variants, UUID-based asset
  reconciliation, missing/error counts, separate exiftool metadata errors, and
  report-shape warnings.
- `photos-tool plan` includes a local per-destination `--exportdb`, matching the
  `send` safety model instead of writing state onto the share.
- Local export DBs are hashed by destination and kept under
  `~/.local/state/photos-tool/exportdb` by default.
- Reports and run logs are kept under `~/.local/state/photos-tool/logs` by default.
- `--cleanup` is guarded against in the runner and asserted absent in fake-tool tests.
- External commands use list argv through `subprocess`; the package does not import
  the `osxphotos` Python API.
- SMB credentials are not stored in config or argv; mounting uses AppleScript and
  Keychain-backed Finder credentials, with SMB URL validation and AppleScript
  string escaping.
- MP4 conversion skips the JPEG `compat/` tree, maps filesystem walk/stat failures
  to exit `5`, and caches non-HEVC video signatures locally to avoid re-probing
  stable compatible videos on every run.
- `osxphotos` is pinned to `0.76.1` on macOS installs.

Local evidence:

```bash
scripts/check.sh
pytest -m "requires_sips or requires_exiftool or requires_ffmpeg"
```

Last local result: `77 passed, 2 skipped`; ruff, ruff format, pyright, actionlint,
and shellcheck all passed.

## Portable test coverage

- L0 pure tests cover config parsing, command construction, reconciliation,
  report parsing, report-shape helpers, report sanitization, SMB parsing/write
  probes, and video candidate classification/idempotency.
- L1 fake-tool tests cover success, missing rows, error rows, all-missing dry-run
  warning, empty selection, no-op/dedup, JPEG `compat/` pass, MP4 standalone-video
  conversion, Live Photo motion skip, auto-mount, stale/unwritable mount failure,
  Photos authorization failure, `--use-photokit`, `send --last-report`, corrupt
  run logs, `doctor`, and `--cleanup` absence.
- L2 marker-gated tests cover a tiny committed `sample.heic` fixture smoke-tested
  with `sips` plus real `exiftool` metadata verification, and generated HEVC ->
  H.264 MP4 conversion plus metadata copy-through where the real tools are
  installed. The product JPEG path remains the osxphotos manual smoke-test path.

## Still requires real environment proof

These cannot be proven inside CI or this workspace without a real Photos library,
Full Disk Access, and the Windows SMB share:

- Capture one real sanitized `osxphotos --report .json` fixture from a live run
  using `photos-tool sanitize-report`.
- Complete the manual smoke test in `docs/manual-test.md`.
- Confirm the real Windows share receives originals, Live Photo pairs, JPEG
  `compat/` copies, and standalone MP4 copies with metadata visible on Windows.
- Confirm the real iCloud Optimize Storage skip guard exits `3` on a cloud-only item.
- Confirm no resident process remains after a large real export.
