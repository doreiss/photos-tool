# photos-tool safety fix plan (from triage+design workflow wf_2b95fb6a-6d1)

Status: DESIGN COMPLETE, awaiting owner sign-off on open questions, then implement.

## Verdict: auditing the audit changed the picture

- **F1 (auditor's flagship "blocker") is FALSE.** osxphotos 0.76.1 gates skip-vs-copy on a real
  `dest.exists()+size+mtime` signature (`cmp_file_sig`, fileutil.py:649-665) and **self-heals a wiped
  share** — re-exports as `new=true exported=true` (photoexporter.py:1374-1377). No DB-driven false-success,
  no deletion escalation. REJECT, no change.
- **F6 is FALSE for 0.76.1.** An absent Live-Photo component is an explicit `missing` row
  (photoexporter.py:391) → reconcile fails closed → no EXIT_OK → no token. REJECT.
- **The genuine exposure is entirely in our OWN delete gate**, which is strictly weaker than osxphotos's
  own skip signature.
- **`fingerprint` is NOT in the export report** (sync-only concept) → identity must be **self-computed from
  the landed copy on the share**. Also: report.py:34,40 wrongly list `fingerprint`/`live_photo` in
  EXPECTED_REPORT_COLUMNS (they never appear) — drop them; do NOT add to REQUIRED.
- **osxphotos exits 0 even on a failed/truncated copy** → success must gate on report error/missing columns
  + our own post-copy content check, never on exit code.

## Elegant core (one rule closes F3+F5+F7+F17; F4-post-backup)

A delete is authorized only if, for EVERY file of the asset, **the exact bytes we backed up are provably
still on the share.**

> **F4 (truncation) — accurate scope (corrected after adversarial review):** the content gate catches a
> copy truncated/corrupted *after* backup (re-verify before delete). A copy truncated *during* export is
> caught a different way: a failed copy (ENOSPC, dropped SMB write) becomes an osxphotos **error row** →
> `reconcile` returns SKIPPED (`missing>0`) → `EXIT_RECONCILE` → **no token is written**, so nothing is
> deletable. The content gate does NOT independently re-derive a source size at save time. The only residual
> is the exotic case where an SMB server ACKs a short write with no error — same class as the accepted
> interior-corruption residual, and undetectable without a source size the osxphotos report does not carry. Content-addressed identity = `size + SHA-256(first 64 KiB + last 64 KiB) + mtime_ns`,
computed against the landed copy at save AND re-verified before delete. All-or-nothing per asset: any
missing/empty/unreadable/non-absolute constituent drops the ENTIRE uuid (never a partial). head+tail+size
is O(128 KiB) per file regardless of size — catches same-size overwrite, boundary corruption, truncation,
without re-hashing tens of GB of video over SMB. size>0 floor falls out for free.

## token v2 (SCHEMA_VERSION 1→2)

- `BackupFile(path:str, size:int, mtime_ns:int, content:str)` replaces the `(path,size)` tuple;
  `BackupAsset.files: tuple[BackupFile,...]`, never empty.
- `_fingerprint(path) -> (size, mtime_ns, content)`: `st = path.stat()`; raise OSError on non-regular/absent;
  raise ValueError on `size==0` (empty never recorded → closes F3 at save); sha256 over head `read(HASH_SPAN)`
  then, if `size>HASH_SPAN`, `seek(max(size-HASH_SPAN, len(head)))` + tail `read(HASH_SPAN)`. `HASH_SPAN=64*1024`.
- **SAVE rule (F5):** per uuid, on ANY non-absolute path or `_fingerprint` failure → set a `broken` reason,
  **BREAK + skip the whole uuid** (collect into returned `skipped: list[(uuid,reason)]`); append the asset only
  if every file fingerprinted. (The critical diff vs today: `continue` → `break`+drop-whole-asset.)
- **RE-VERIFY rule (F7/F4/F17):** `_matches(f: BackupFile)` recomputes `_fingerprint` and returns
  `size==f.size and mtime_ns==f.mtime_ns and content==f.content`. `removable_assets` keeps its cross-asset
  path-collision guard; changes its `all()` to `all(_matches(f) for f in asset.files)`.
- **SERIALIZATION:** `_to_dict` emits files as 4-key dicts; `_from_dict` requires all four with correct
  types (str,int,int,str) else rejects.
- **MIGRATION:** `load_backup_token` already returns None on schema mismatch → a v1 token reads as "no
  pending backup" (fail-safe). Add a one-line cleanup notice ("a backup from an older version cannot be
  auto-cleaned; re-run send") when the file parses but schema differs. No v1-parse/data-migration code.

## Implementation plan (file-by-file)

1. **state.py** — bump SCHEMA_VERSION; add `HASH_SPAN`, `import stat`; add `BackupFile`; add `_fingerprint`.
2. **state.py** — rewrite `save_backup_token`: break+drop whole asset on failure, return `skipped` list.
3. **state.py** — `_matches(f: BackupFile)` content re-verify; update `removable_assets` + `_to_dict`/`_from_dict`.
4. **state.py** — `reveal_paths(token, removable: set[str])`: only removable assets, FIRST file that `_matches`,
   de-duplicated (closes F16).
5. **cli.py** — `_cmd_send` after `summarize()`: `if missing_expected_columns(report): print refuse; return EXIT_PREFLIGHT` (F9), before any token write.
6. **cli.py** — token-write site: capture `skipped = save_backup_token(...)`; if skipped, print visible stderr
   ("N photo(s) were not fully verified on the share and will NOT be offered for cleanup; re-run send") (F5 visibility).
7. **cli.py** — add `_on_boot_volume(path)` (st_dev vs `/`); in the smb-empty branch of `_ensure_destination_ready`
   return an error when the resolved destination (or its parent) is on the boot volume (F8). Applies to send AND cleanup.
8. **cli.py** — cleanup-last call site: `reveal_paths(token, set(removable))` (F16 wiring).
9. **report.py** — `summarize_rows`: append path only when `not missing and not error and (_truthy(exported) or _truthy(skipped))` (critic-positive-flag); drop `fingerprint`/`live_photo` from EXPECTED_REPORT_COLUMNS.
10. **tests/fakebin/tool.py** — when no explicit `report` key, DERIVE report rows from files actually written,
    with distinct per-file byte content (not the constant 4-byte "fake") (closes F14 — gate invariant becomes testable).

## Tests to add
F14 (report==disk synthesized); F5 (Live Photo MOV absent → whole uuid dropped + kept); F5 visibility (stderr line);
F3 (0-byte truncation kept; `_fingerprint` raises on 0-byte); F7/F4/F17 (same-size different content, and mtime-only change → kept);
F9 (missing column → EXIT_PREFLIGHT, no token); F8 (boot-volume st_dev refused for send+cleanup; non-boot passes);
F16 (reveal returns only removable verified file); schema migration (v1 token → no-token + "older version" notice);
marker-gated real-Mac PhotoKit round-trip (F15/F32, skipped in CI).

## FIX NOW (10): F5, F3, F7, F4, F17, F8, F9, F16, F14, critic-positive-flag

## REJECT: F1 (false), F6 (false), full-file hash (slow/over-engineered), osxphotos-fingerprint (not in report), F12 (export timeout — kills valid long iCloud exports; killed export fails closed anyway).

## DEFER (separate batches, honoring "few moving parts"): F2/F18 (multi-Mac — content gate shrinks to near-impossibility), F19 (send DEST), F20 (cleanup lock), F26/F27 (schema/digest orphan — fail-safe), F10 (version assert — partly redundant with F9), F22/F24 (availability), F11/F23 (PII/hygiene), F25/F21/GUI-stdout (UX), Tier3 doc-drift + misc.

## Risks
head+tail+size+mtime is defense-in-depth not cryptographic (an interior same-size edit outside the first/last
64 KiB with preserved mtime is undetected — accepted altitude); cleanup re-reads 128 KiB/file over SMB (bounded
latency); exact mtime_ns equality could false-"changed" if an SMB server alters mtime (fail-safe: keeps original);
schema bump orphans a backup pending across upgrade (fail-safe, re-run send); the fake change may touch scenarios
relying on constant "fake" bytes; st_dev boot check assumes the Photos library is on the boot volume.
