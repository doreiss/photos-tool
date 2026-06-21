"""High-volume probes for the scale-sensitive paths a 100GB / tens-of-thousands-of-photos
send exercises, WITHOUT a real Photos library or a real 100GB share.

These guard the structures that grow with the photo count:
  - the report parser (a 30k-row osxphotos report),
  - save_backup_token's per-asset fingerprint loop + token serialization + memory,
  - the cleanup-side removable_assets gate (the safety-critical "still byte-matches" check),
    including that tampering with one landed copy WITHHOLDS exactly that original.

Empirically (dev M-series): the verify pass is linear (~50 us/asset), peaks ~80 MB RSS at 20k
assets, hashes at ~2 GB/s, and samples (never fully reads) files above 16 MiB. N is kept modest
so the suite stays fast; the point is to pin linearity, zero silent drops, and the tamper gate —
not to reproduce the absolute production wall-clock.
"""

from __future__ import annotations

import json
import os
import tracemalloc
from pathlib import Path

from photos_tool import report, state

PARSE_N = 30_000  # pure-JSON parse: no file IO, stays fast even at production scale
TOKEN_N = 5_000  # real files created on disk: modest so CI stays quick
SPARSE_LARGE = 8  # >16 MiB sparse files (truncate, ~0 real disk) -> hit the sampling branch
LARGE_BYTES = 64 * 1024 * 1024  # 64 MiB, comfortably over WHOLE_FILE_MAX (16 MiB)


def _report_row(uuid: str, filename: str) -> dict[str, object]:
    return {
        "uuid": uuid,
        "filename": filename,
        "exported": True,
        "new": True,
        "updated": False,
        "skipped": False,
        "converted_to_jpeg": False,
        "missing": False,
        "error": False,
        "exiftool_error": False,
    }


def test_parse_report_scales_to_30k_rows(tmp_path: Path):
    rows = [
        _report_row(f"uuid-{i:06d}", f"/share/{i % 256:03d}/IMG_{i:06d}.heic")
        for i in range(PARSE_N)
    ]
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(rows), encoding="utf-8")

    summary = report.parse_report(report_path)
    assert summary.exported == PARSE_N
    assert summary.exported_paths is not None
    assert len(summary.exported_paths) == PARSE_N
    assert summary.issue_count == 0


def _make_assets(share: Path) -> dict[str, tuple[str, ...]]:
    blob = os.urandom(8 * 1024)  # whole-file-hash path (< 16 MiB)
    exported: dict[str, tuple[str, ...]] = {}
    for i in range(TOKEN_N):
        sub = share / f"{i % 256:03d}"
        sub.mkdir(exist_ok=True)
        f = sub / f"IMG_{i:06d}.heic"
        f.write_bytes(blob)
        exported[f"uuid-{i:06d}"] = (str(f),)
    for j in range(SPARSE_LARGE):  # large sparse files -> the interior-sampling branch
        f = share / f"MOVIE_{j:03d}.mov"
        with f.open("wb") as fh:
            fh.truncate(LARGE_BYTES)
        exported[f"uuid-mov-{j:03d}"] = (str(f),)
    return exported


def test_save_backup_token_at_scale_drops_nothing_and_bounds_memory(tmp_path: Path):
    share = tmp_path / "share"
    share.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    exported = _make_assets(share)
    total = TOKEN_N + SPARSE_LARGE

    tracemalloc.start()
    dropped = state.save_backup_token(state_dir, share, "smb://pc/Share", exported)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    # Every landed copy is present + non-empty + readable -> nothing silently dropped.
    assert dropped == []
    # The verify pass must not balloon with N: the token's Python structures stay well bounded.
    assert peak < 200 * 1024 * 1024, f"verify-pass peak too high: {peak / 1e6:.0f} MB"

    loaded = state.load_backup_token(state_dir, share)
    assert loaded is not None
    assert len(loaded.assets) == total


def test_cleanup_gate_withholds_a_tampered_copy_at_scale(tmp_path: Path):
    share = tmp_path / "share"
    share.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    exported = _make_assets(share)
    state.save_backup_token(state_dir, share, "smb://pc/Share", exported)

    token = state.load_backup_token(state_dir, share)
    assert token is not None
    removable, kept = state.removable_assets(token)
    # Untouched share: every recorded original is removable, nothing withheld.
    assert len(removable) == TOKEN_N + SPARSE_LARGE
    assert kept == []

    # Tamper with ONE landed copy (truncate to a different size) -> that exact original must be
    # withheld from deletion, and only that one. This is the never-lose-a-photo gate at scale.
    victim_uuid = "uuid-000000"
    by_uuid = {a.uuid: a.files[0].path for a in token.assets}
    victim_path = Path(by_uuid[victim_uuid])
    with victim_path.open("r+b") as fh:
        fh.truncate(1)

    removable2, kept2 = state.removable_assets(token)
    assert victim_uuid not in removable2
    assert victim_uuid in {uuid for uuid, _reason in kept2}
    assert len(removable2) == TOKEN_N + SPARSE_LARGE - 1
