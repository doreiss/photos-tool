# External Adversarial Audit — photos-tool (received 2026-06-19)

> This is an EXTERNAL auditor's report, pasted verbatim. It is a set of CLAIMS to be
> verified against the actual current working tree, not ground truth. Treat docstrings,
> tests, and this document with equal skepticism. Line numbers may have drifted (the
> working tree has since changed: `state.reveal_path` was replaced by `state.reveal_paths`
> returning a list of ALL still-present copies; `gui_actions.CleanupQuery.reveal` is now a
> tuple; the cleanup `--json` `reveal` field is now a list; test count is 142).

## Overall verdict (auditor): needs-work — do not put `cleanup-last` in a family member's hands until the delete-gate fixes land.

The backup-only path is close to ship-with-fixes. The destructive `cleanup-last` path rests its
safety on a `(path, exists, st_size)` identity binding plus a success signal derived from
osxphotos's LOCAL export DB — neither strong enough. Root cause for Tier 0: the delete gate proves
a file of a recorded SIZE sits at a recorded PATH, and the backup-success signal trusts osxphotos's
report (driven by the local export DB) — neither ever confirms the BYTES on the share are this photo.

---

## TIER 0 — Silent photo loss

**F1 — blocker — Stale export DB + altered share → `send` reports success and writes a delete-authorizing token.**
`cli.py:310,328`, `report.py:170-178`, `state.py:84`
Day 1 send succeeds; per-destination export DB records every asset. Share later wiped/reformatted/
restored-from-different-backup while the local export DB is untouched. Day 2 re-select same photos
and send. `osxphotos --update` decides skip-vs-copy from its LOCAL DB, not the share → emits
non-missing/non-error `skipped` rows and writes nothing. report.py:173 counts each skipped row toward
its UUID's exported set before any presence check → exported==selected → reconcile OK → EXIT_OK.
CLI prints success, last-run.json records success, share holds nothing/stale data. Escalation: if a
same-named file still sits at the path, save_backup_token records ITS size, cleanup-last re-confirms
that size and deletes the original whose real backup is gone. False "backup complete" is UNCONDITIONAL
given stale DB + altered share; deletion escalation is CONDITIONAL on a same-name/same-size file present
(empty share => stat() raises => fails safe). Fix: before EXIT_OK, re-stat each exported_paths entry and
require every reconciled asset to have >=1 present copy of matching size; never treat a skipped row as
proof of a present share copy for the deletion gate; doctor check comparing DB-claimed exports to on-share
existence+size; document/automate export-DB reset when the destination tree is recreated.

**F2 — blocker — Two Macs sharing one tree: a same-size overwrite by Mac B authorizes Mac A to delete its original.**
`state.py:127-143`, `cli.py:132,684`, `config.py:81`
Per-Mac isolation rests on subpath = slug(socket.gethostname()). Apple default names collide
(two Macs → `Mac`/`MacBook-Air`); destination_path() identical; empty-subpath only warns. Mac A backs up
IMG_0001.HEIC size S, token records (path,S). Mac B writes a DIFFERENT IMG_0001.HEIC to the same path
(its DB has no record → overwrites). If the two HEICs share exact byte size (common same-model iPhone),
Mac A cleanup-last: smb_url matches, _matches(path,S) true → A's original deleted, share copy is now B's
unrelated photo. hostname is stored in token but never read by the gate (F18); smb_url check passes;
per-destination digest identical; collision guard only dedups paths WITHIN one token. A single st_size is
the sole barrier. Fix: bind gate to photo identity not size (record osxphotos fingerprint per file,
re-verify content before delete); per-Mac install-UUID marker in the subpath tree, refuse cleanup if a
foreign marker present; removable_assets refuse any asset whose token.hostname != current host; empty
subpath with smb_url set = hard error.

**F3 — high (blocker-adjacent) — Zero-byte share copy passes the gate.** (critic) `state.py:84,155-160`
`_matches` checks only is_file() and st_size==recorded. No size>0 floor → a recorded 0-byte copy (ENOSPC,
failed/empty SMB write osxphotos didn't flag) round-trips: 0 recorded, 0 confirmed → original deleted,
empty file remains. Falsifies README "present and non-empty" + GUI "confirmed on the share". Fix: require
st_size==size AND size>0; never record/treat-removable an asset whose constituents all landed 0 bytes;
fix README/GUI copy.

**F4 — high — A copy truncated DURING export records its truncated size as the verified backup.** `state.py:84,155-160`
Share fills / SMB drops mid-write; osxphotos finishes without flagging; token records truncated size;
cleanup confirms it and deletes original → corrupt copy. The advertised truncated-copy guard only covers
truncation AFTER backup (size binding is self-referential). Fix: verify each dest file against the source
asset's authoritative size/fingerprint BEFORE recording; at minimum re-stat twice with a delay.

**F5 — high — Multi-file asset narrowed to its present constituents, then the whole asset deleted.** `state.py:83-88`
Live Photo report lists HEIC+MOV but at token-save time only HEIC is on the share (MOV landed late/failed).
save_backup_token stat()s each path and silently `continue`s on OSError, recording the HEIC alone.
removable_assets re-verifies only the HEIC, marks the whole UUID removable, remove_originals deletes HEIC
AND MOV from Photos — motion file gone from both. Same for RAW+JPEG, edited+original. Fix: in
save_backup_token, ANY constituent whose stat() fails (or non-absolute) is FATAL for that asset — drop the
entire asset; never record a partial asset.

**F6 — high — Reconciliation is per-asset, so a silently-omitted constituent passes as fully backed up.** `report.py:170-178,198-203`
osxphotos emits only the HEIC row for a Live Photo and NO MOV row (not flagged, just absent). Reconcile
compares distinct exported UUIDs to selected ASSET count (both per-asset) → exported==selected → OK → token
→ cleanup deletes whole UUID, losing the unreported component. live_photo column is in EXPECTED_REPORT_COLUMNS
but read NOWHERE. (Flagged partial case IS safely caught — error/missing row forces SKIPPED.) Fix: read
live_photo, require a paired non-missing/non-error motion row before a live-photo UUID is removal-eligible;
removable_assets refuse structurally-incomplete assets; real-Mac test asserting Live Photo yields >=2 rows.

**F7 — high — Size-only identity binding (no content/mtime/fingerprint).** `state.py:139,155-160`
(path,size) is neither collision-resistant nor corruption-resistant; token records no mtime, so a
size-preserving post-backup overwrite, or an SMB server that commits size N but loses data blocks on unclean
unmount, is invisible. Last gate before an irreversible delete. Fix: record+recheck st_mtime (cheap) and
ideally a content hash (whole file, or head+tail+size) captured at save time; verify against source's
reported size/fingerprint.

**F8 — high — Empty `smb_url` accepts any writable local dir → backup lands on the boot disk, then originals deleted.** `cli.py:621-642`, `state.py:155-160`
mount_point=/Volumes/FamilyPhotos, smb_url="" (manual-mount/hand-edited). Share not mounted but
/Volumes/FamilyPhotos exists as an ordinary local dir (stale mountpoint after unclean unmount).
_ensure_destination_ready takes the smb-empty branch, sees a writable local path, returns ready with NO
mount verification. osxphotos writes "backup" to boot disk; reconcile passes; token records local paths;
cleanup-last re-verifies them right there and deletes originals. After 30 days the only copy is on the same
disk as the library. Fix: refuse cleanup if destination resolves to boot volume / same device as the Photos
library; for the destructive path require a verified network/removable mount.

**(critic, high/medium) exported_paths keys on (not missing and not error), never requiring a positive exported/skipped flag.** `report.py:170-178`
A row exported=False, skipped=False, missing=False, error=False, filename=<path> still contributes its path
to the token and can authorize a delete. Fix: require _truthy(exported) or _truthy(skipped) in addition to
not-missing/not-error. (Whether 0.76.1 ever emits such a row is a residual unknown.)

## TIER 1 — High

**F9 — high — A renamed/removed required report column only warns; it does not fail closed.** `report.py:158-165`, `cli.py:878-891`
osxphotos upgrade renames missing/error/exported → row.get('missing') None → _truthy False → unexported
cloud-only asset counts as exported → EXIT_OK + token. missing_expected_columns() detects the rename but its
only caller prints to stderr and never changes the exit code. Fix: missing required columns = hard
EXIT_PREFLIGHT / refuse the token, like the no-uuid case.

**F10 — high — No runtime osxphotos version assertion.** `tooling.py:53-71`, `pyproject.toml:13`
Pin is install-time only; nothing at runtime checks the version load-bearing report/flag/fingerprint behavior
depends on. Fix: assert the running osxphotos version (or capability probe) before first export.

**F11 — high — `sanitize-report` is allow-by-default denylist that leaks PII into committed fixtures.** `report.py:291-303`
Only keys with gps/latitude/longitude, uuid variants, containing path, or ending filename are touched.
original_name, keyword, exported_album, title, description, exiftool_warning pass verbatim — and the repo
commits sanitized reports to git. Fix: invert to allow-by-default-redact: keep only known-safe
boolean/count/status columns + hashed uuid + sanitized path; redact everything else incl. unrecognized.

**F12 — high — Export subprocess has no timeout; a stuck export wedges the menu-bar app forever.** `menubar.py:274-281`, `cli.py:206`, `osxphotos_runner.py:70`
_do_send and run_export pass timeout=None. A stalled PhotoKit/Automation prompt, iCloud download, or
half-dropped SMB mount blocks the worker indefinitely; _busy only clears when a result lands → every click
hits "Please wait", title stuck on working glyph, escape only via Quit (which orphans the child, F22). Fix:
configurable wall-clock watchdog that terminates the child process group and posts an error; and/or a Cancel
menu item holding the live Popen.

**F13 — high — Collision warning fires only on empty subpath, not on a colliding non-empty default.** `cli.py:132`
Guard is `if smb_url and not subpath:` — two Macs both defaulting subpath to `Mac` get NO warning while
sharing a tree. Fix: warn when subpath equals a generic/collision-prone default and when a foreign host
marker is found on the share.

**F14 — high — The delete-safety invariant is structurally untested.** `tests/fakebin/tool.py:74-84`, `ci.yml:96-110`
The gate rests on "report filename == file actually on disk", but the fake osxphotos writes the report and
the files from INDEPENDENT scenario keys (report vs files), so a test can pass with a report that disagrees
with disk — the real invariant is never asserted. No real osxphotos export runs in CI. Fix: make the fake
derive report rows from the files it actually writes; add one real-Mac end-to-end export→token→cleanup test.

**F15 — high — Zero CI coverage of the one destructive feature.** `remove.py:78-129`, `pyproject.toml:13-21`
Entire PhotoKit path is pragma: no cover; pyobjc/Photos isn't even a declared dependency. Green CI proves
nothing about deletion. Fix: marker-gated real-Mac test (even a single throwaway asset); declare pyobjc on macOS.

**F16 — high — Cleanup's "verify in Finder" can reveal a file that isn't one being deleted.** `state.py:146-152`, `menubar.py:226-249`
reveal_path returns the first token file that merely is_file() (no size check), across ALL assets incl. KEPT
ones. GUI shows "1 confirmed", opens a kept/unverified file, user confirms "they arrived", a DIFFERENT
asset's original is deleted. Fix: restrict reveal to the removable set; reveal one file per removable asset.
[NOTE: working tree now uses reveal_paths returning ALL present files across all assets — still not
restricted to removable; verify current behavior.]

**(critic, medium) GUI discards all send stdout/stderr, so a RECONCILE result never tells the user which photos were skipped.** `menubar.py:274-281`
_do_send routes both streams to DEVNULL; only the exit code survives → fixed "Some photos were skipped" with
no count/list; never reads last-run.json. Fix: surface the reconcile message/counts to the GUI.

## TIER 2 — Medium

- **F17 medium — Existence-only wiped-share defense is incidental** (`state.py:83-88`): "empty share is safe" is just stat() raising; a partially-restored/older/foreign same-size copy at the path defeats it.
- **F18 low→medium — Hostname stored in token but never consulted by the gate** (`state.py:90-96`): documented per-Mac protection does not exist.
- **F19 medium — `send DEST` positional bypasses config and desyncs from cleanup-last** (`cli.py:527,115,667`): per-run destination override backs up somewhere cleanup-last (config-only) never looks → strands a token.
- **F20 medium — `cleanup-last` holds no lock** (`cli.py:664-751`): a concurrent send can overwrite the token between cleanup's query and apply.
- **F21 medium — GUI `_busy` cleared before the blocking cleanup confirmation modals** (`menubar.py:190,226-249`): a click during the modal can hijack the flow and drop the confirmed cleanup_apply.
- **F22 medium — Quit during send orphans the osxphotos child** (`menubar.py:142,274-281`).
- **F23 medium — `.gitignore` blocks report*.csv but not report*.json** (`.gitignore:18-21`) — JSON is the format that carries the data.
- **F24 medium — No free-space preflight** (`osxphotos_runner.py:70`): drives the share to ENOSPC mid-batch (feeds F4).
- **F25 medium — `plan` builds ExportOptions via a second hand-written path that diverges from send** (`cli.py:79-93`) and exposes per-run flags contradicting config-only — plan can lie about what send will run.
- **F26 medium — Schema-version skew is indistinguishable from "no token"** (`state.py:105-109`): a future SCHEMA_VERSION bump makes a saved token silently unreadable (no migration), orphaning a pending cleanup.
- **F27 low — changing subpath/hostname after a backup re-keys the destination digest and orphans the token** (`config.py:75-83`, `state.py:53`).
- **F28 medium — Stale-export-DB recovery requires deleting an undocumented internal file** (`cli.py:336`): no command surfaces/repairs it (operational half of F1).
- **F29 medium — SMB size gate + mount parser tested only against local APFS tmp dirs / fabricated mount output** (`tests/test_cleanup_between_steps.py`, `smb.py`).
- **F30 medium — real-tools test uses sips for HEIC→JPEG but the product converts via osxphotos --convert-to-jpeg** (`test_real_tools.py`) — the actual compat-JPEG path has no real coverage.
- **F31 medium — the fake never models filename collisions / osxphotos auto-rename**, so the collision guard's real trigger is untested.
- **F32 medium — PhotoKit's `<uuid>/L0/001` convention is "validated" only by a fake that echoes back what it's given.**

## TIER 3 — Low / nit (condensed)

low: empty-filename row inflates exported count (report.py); GUI cleanup count drifts between query and apply
and isn't re-shown; state files written 0o644 (contain share paths, hostname, smb_url); OVER status treated
as success with a factually-wrong message + count-vs-export TOCTOU; is_authorization_error misses osxphotos's
real FDA strings; an exception in _drain/_handle (main-thread UI) is uncaught and can strand the glyph/busy
flag; send --dry-run consults the persistent export DB (nondeterministic); JSON dict-fallback can mis-ingest a
top-level metadata object as a photo row; _is_output_current uses mtime only (truncated-but-touched compat MP4
treated as current forever); transcode_to_mp4 writes in place (interrupted ffmpeg leaves a partial MP4 at the
real path); manual-test.md step 9 documents removed flags (send --remove-originals, removed.jsonl) the family
setup-helper will run and fail — same stale flag name survives in a remove.py:83 error string, and
implementation-audit.md never mentions the removal feature; dead/speculative surface (count_selected,
expand_path, run_export unused extra/timeout, ExportOptions.update/jpeg_ext); is_writable orphans a probe
dotfile on the append-only share if unlink fails (critic); album typo yields a raw osxphotos error instead of
the friendly message (critic); GUI reveal step is a silent no-op if open -R fails (critic).

nit: check proves only binaries-on-PATH (false readiness); CSV/JSON with a UTF-8 BOM mangles the first column;
validate_smb_url accepts control chars/newlines; atomic_write_json doesn't fsync the parent dir; doctor's
Optimize-Storage probe can misreport a TCC failure as iCloud risk; the real report fixture was run through the
sanitizer that overwrites the filename field the tests most rely on; the 4-state reconcile enum is over-modeled.

## One finding REFUTED on verification
The case/Unicode-normalization collision-guard concern (`state.py:127-143`) was refuted — on a
case/normalization-insensitive share, two recorded NFD/NFC paths collapsing to one file would be caught by
the existing collision guard or fail _matches (safe). The real filesystem-semantics risk is F7 (size-only).

## Genuine strengths the auditor verified (re-verify these too — could be overstated)
Post-backup tamper checks (deleted/truncated/grown copy) fail _matches and keep the original
(test_cleanup_between_steps.py); per-destination token keying never mixes shares; cleanup re-validates
mount+writability and refuses an smb_url repoint; token consumed after a successful delete; remove.py is
fail-closed (aborts unless PhotoKit resolves EXACTLY the requested UUIDs, positive per-asset re-check, routes
to Recently Deleted); token writes atomic+corruption-tolerant (pid-temp + fsync + os.replace; readers tolerate
JSONDecodeError); token saved only on EXIT_OK; --cleanup hard-blocked (append-only); flocks correctly scoped;
missing/error and no-uuid reports fail safe; destination digest stable across mount/unmount; preferences
config-only; threading contract holds (only main-thread callbacks touch rumps).

## Residual unknowns (decide from installed osxphotos 0.76.1 source — library is empty so can't run live)
1. Does `osxphotos --update` re-export a file the export DB thinks is current but is ABSENT/changed on the dest, or emit a skipped no-op? (sets F1 severity)
2. On ENOSPC, does osxphotos exit non-zero, or write a truncated/0-byte file and exit 0 with an unflagged row? (sets F3/F4)
3. Does a Live Photo always yield >=2 report rows, and what's emitted for an --update skip? (sets F5/F6)
4. Is `<uuid>/L0/001` the correct PhotoKit localIdentifier on 0.76.1? (sets F32/delete resolution)
5. Does the 0.76.1 report carry a usable per-file `fingerprint`, populated for exported files? (decides whether the content-addressed gate is free)
6. Do `query --selected --count` and the export report agree on the asset unit for bursts and RAW+JPEG pairs?

## Auditor's recommended highest-leverage fix
Make the delete gate CONTENT-ADDRESSED — record each landed file's osxphotos fingerprint (or a hash) at
backup time and re-verify BYTES, not size, before deletion — and DROP any asset with a missing/partial
constituent from the token entirely. Plus fail send CLOSED when required columns are absent (F9) and when
reconciled files aren't actually present on the share (F1). Turns "trusts the report" into "trusts the share."
