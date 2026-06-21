from __future__ import annotations

import json
import os
from pathlib import Path

from photos_tool import state


def _write(path: Path, data: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")
    return path


def test_save_and_load_round_trips_with_recorded_sizes(tmp_path: Path):
    dest = tmp_path / "share" / "MacA"
    a = _write(dest / "2024" / "06" / "a.heic", "aaaa")  # 4 bytes
    b = _write(dest / "2024" / "07" / "b.heic", "bb")  # 2 bytes
    state_dir = tmp_path / "state"

    state.save_backup_token(state_dir, dest, "smb://nas/share", {"ua": (str(a),), "ub": (str(b),)})
    token = state.load_backup_token(state_dir, dest)

    assert token is not None
    assert token.destination_root == str(dest)
    assert token.smb_url == "smb://nas/share"
    sizes = {asset.uuid: asset.files[0].size for asset in token.assets}
    assert sizes == {"ua": 4, "ub": 2}


def test_load_returns_none_for_a_different_destination(tmp_path: Path):
    dest = tmp_path / "share" / "MacA"
    f = _write(dest / "a.heic", "aaaa")
    state_dir = tmp_path / "state"
    state.save_backup_token(state_dir, dest, "smb://nas/share", {"ua": (str(f),)})

    # A token keyed for MacA must not be returned for MacB.
    assert state.load_backup_token(state_dir, tmp_path / "share" / "MacB") is None


def test_removable_requires_present_copy_at_recorded_size(tmp_path: Path):
    dest = tmp_path / "share"
    present = _write(dest / "present.heic", "aaaa")
    changed = _write(dest / "changed.heic", "aaaa")
    missing = _write(dest / "missing.heic", "aaaa")
    state_dir = tmp_path / "state"
    state.save_backup_token(
        state_dir,
        dest,
        "smb://nas/share",
        {"present": (str(present),), "changed": (str(changed),), "missing": (str(missing),)},
    )
    # Tamper AFTER the token recorded each size, as a wiped/replaced share would.
    changed.write_text("x", encoding="utf-8")  # size 1 != recorded 4
    missing.unlink()

    token = state.load_backup_token(state_dir, dest)
    assert token is not None
    removable, kept = state.removable_assets(token)

    assert removable == ["present"]
    assert sorted(uuid for uuid, _ in kept) == ["changed", "missing"]


def test_clear_consumes_the_token(tmp_path: Path):
    dest = tmp_path / "share"
    f = _write(dest / "a.heic", "aaaa")
    state_dir = tmp_path / "state"
    state.save_backup_token(state_dir, dest, "smb://nas/share", {"u": (str(f),)})

    assert state.load_backup_token(state_dir, dest) is not None
    state.clear_backup_token(state_dir, dest)
    assert state.load_backup_token(state_dir, dest) is None


def test_load_rejects_corrupt_or_unversioned_token(tmp_path: Path):
    dest = tmp_path / "share"
    state_dir = tmp_path / "state"
    _write(state.token_path(state_dir, dest), "{not json")
    assert state.load_backup_token(state_dir, dest) is None

    _write(state.token_path(state_dir, dest), '{"schema_version": 999}')
    assert state.load_backup_token(state_dir, dest) is None


def test_reveal_paths_lists_one_verified_copy_per_removable_asset(tmp_path: Path):
    dest = tmp_path / "share"
    a = _write(dest / "2024" / "08" / "a.heic", "aaaa")
    b = _write(dest / "2024" / "09" / "b.mov", "bbbb")
    state_dir = tmp_path / "state"
    state.save_backup_token(state_dir, dest, "smb://nas/share", {"u1": (str(a),), "u2": (str(b),)})
    token = state.load_backup_token(state_dir, dest)
    assert token is not None
    removable, _ = state.removable_assets(token)
    # One representative file per removable asset, across both date folders.
    assert state.reveal_paths(token, set(removable)) == [str(a), str(b)]

    # A copy that changes drops its asset from removable, so it is no longer revealed.
    a.write_text("zzzz-bigger", encoding="utf-8")
    removable, _ = state.removable_assets(token)
    assert state.reveal_paths(token, set(removable)) == [str(b)]


def test_reveal_excludes_present_but_kept_assets(tmp_path: Path):
    # F16: a present-but-KEPT asset (here two uuids colliding on one path) must never be
    # revealed as "your photos arrived" while a different asset is the one being deleted.
    dest = tmp_path / "share"
    shared = _write(dest / "shared.heic", "aaaa")
    solo = _write(dest / "solo.heic", "bbbb")
    state_dir = tmp_path / "state"
    state.save_backup_token(
        state_dir,
        dest,
        "smb://nas/share",
        {"u1": (str(shared),), "u2": (str(shared),), "solo": (str(solo),)},
    )
    token = state.load_backup_token(state_dir, dest)
    assert token is not None
    removable, kept = state.removable_assets(token)
    assert removable == ["solo"]
    assert {uuid for uuid, _ in kept} == {"u1", "u2"}
    assert state.reveal_paths(token, set(removable)) == [str(solo)]


def test_empty_copy_is_never_recorded_or_removable(tmp_path: Path):
    # F3: a 0-byte copy must not round-trip into a deletion authorization.
    dest = tmp_path / "share"
    empty = _write(dest / "empty.heic", "")  # 0 bytes
    state_dir = tmp_path / "state"
    skipped = state.save_backup_token(state_dir, dest, "smb://nas/share", {"u": (str(empty),)})
    assert [uuid for uuid, _ in skipped] == ["u"]  # dropped with a reason
    token = state.load_backup_token(state_dir, dest)
    assert token is not None and token.assets == ()
    assert state.removable_assets(token)[0] == []


def test_partial_multi_file_asset_is_dropped_whole(tmp_path: Path):
    # F5 (the one true blocker): a Live Photo whose MOV did not land must drop the WHOLE
    # asset — never record HEIC-only — because the delete removes the whole asset at once.
    dest = tmp_path / "share"
    heic = _write(dest / "2024" / "08" / "live.heic", "aaaa")
    mov_missing = dest / "2024" / "08" / "live.mov"  # never written
    state_dir = tmp_path / "state"
    skipped = state.save_backup_token(
        state_dir, dest, "smb://nas/share", {"live": (str(heic), str(mov_missing))}
    )
    assert [uuid for uuid, _ in skipped] == ["live"]
    token = state.load_backup_token(state_dir, dest)
    assert token is not None and token.assets == ()  # not even the HEIC is recorded
    assert state.removable_assets(token)[0] == []


def test_same_size_different_content_keeps_original(tmp_path: Path):
    # F7: a size-preserving overwrite (a different photo of identical length, even with the
    # original mtime restored) must NOT be mistaken for the recorded backup.
    dest = tmp_path / "share"
    f = _write(dest / "a.heic", "aaaa")  # 4 bytes
    state_dir = tmp_path / "state"
    state.save_backup_token(state_dir, dest, "smb://nas/share", {"u": (str(f),)})
    token = state.load_backup_token(state_dir, dest)
    assert token is not None
    st = f.stat()
    f.write_text("bbbb", encoding="utf-8")  # same 4 bytes, different content
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))  # restore mtime: ONLY content differs
    removable, kept = state.removable_assets(token)
    assert removable == []
    assert [uuid for uuid, _ in kept] == ["u"]


def test_mtime_only_change_keeps_original(tmp_path: Path):
    # F7: a rewritten copy with the same bytes but a newer mtime is treated as changed.
    dest = tmp_path / "share"
    f = _write(dest / "a.heic", "aaaa")
    state_dir = tmp_path / "state"
    state.save_backup_token(state_dir, dest, "smb://nas/share", {"u": (str(f),)})
    token = state.load_backup_token(state_dir, dest)
    assert token is not None
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))  # newer mtime, same bytes
    assert state.removable_assets(token)[0] == []


def test_v1_token_reads_as_no_backup_but_is_detectably_stale(tmp_path: Path):
    # Migration: a v1 (2-tuple) token must read as "no pending backup" (fail-safe), yet be
    # distinguishable from a truly-absent token so cleanup can say "re-run send".
    dest = tmp_path / "share"
    state_dir = tmp_path / "state"
    v1 = {
        "schema_version": 1,
        "destination_root": str(dest),
        "smb_url": "smb://nas/share",
        "hostname": "old",
        "timestamp": "t",
        "assets": [{"uuid": "u", "files": [["/share/a.heic", 4]]}],
    }
    _write(state.token_path(state_dir, dest), json.dumps(v1))
    assert state.load_backup_token(state_dir, dest) is None
    assert state.stale_token_exists(state_dir, dest) is True
    # A genuinely absent token is not "stale".
    assert state.stale_token_exists(state_dir, tmp_path / "elsewhere") is False


def test_token_file_is_written_owner_only_0600(tmp_path: Path):
    # The token records share paths, hostname, and the SMB URL — keep it owner-only.
    dest = tmp_path / "share"
    f = _write(dest / "a.heic", "aaaa")
    state_dir = tmp_path / "state"
    state.save_backup_token(state_dir, dest, "smb://nas/share", {"u": (str(f),)})
    assert (state.token_path(state_dir, dest).stat().st_mode & 0o777) == 0o600


def test_corrupt_token_with_nonlist_assets_or_files_loads_safely(tmp_path: Path):
    # A hand-crafted/corrupt token whose assets/files is a truthy non-iterable must NOT crash
    # load_backup_token (which only catches OSError/JSONDecodeError) — it must fail safe.
    dest = tmp_path / "share"
    state_dir = tmp_path / "state"
    for bad in (
        {"assets": 7},
        {"assets": [{"uuid": "u", "files": 5}]},
        {"assets": [{"uuid": "u", "files": True}]},
    ):
        payload = {"schema_version": state.SCHEMA_VERSION, "destination_root": str(dest), **bad}
        _write(state.token_path(state_dir, dest), json.dumps(payload))
        token = state.load_backup_token(state_dir, dest)  # must not raise
        assert token is None or token.assets == ()  # no under-verified asset survives


def test_reveal_rechecks_each_file_not_just_the_removable_set(tmp_path: Path):
    # reveal_paths must re-verify with _matches, not trust the passed set blindly: build the
    # removable set from an unchanged token, THEN mutate a removable file -> it is omitted.
    dest = tmp_path / "share"
    a = _write(dest / "a.heic", "aaaa")
    b = _write(dest / "b.heic", "bbbb")
    state_dir = tmp_path / "state"
    state.save_backup_token(state_dir, dest, "smb://nas/share", {"ua": (str(a),), "ub": (str(b),)})
    token = state.load_backup_token(state_dir, dest)
    assert token is not None
    removable, _ = state.removable_assets(token)
    assert set(removable) == {"ua", "ub"}
    a.write_text("aaaa-changed", encoding="utf-8")  # changes AFTER removable was computed
    assert state.reveal_paths(token, set(removable)) == [str(b)]


def test_fingerprint_hashes_whole_file_under_threshold(tmp_path):
    # A file bigger than head+tail (the old hash's blind spot) but under WHOLE_FILE_MAX is now
    # hashed in FULL: a single middle-byte change is caught even with size + mtime preserved.
    import os

    f = tmp_path / "clip.mov"
    data = bytearray(b"\x00" * (state.HASH_SPAN * 4))  # 256 KiB: > head+tail, < WHOLE_FILE_MAX
    f.write_bytes(bytes(data))
    size, mtime_ns, content = state._fingerprint(f)
    bf = state.BackupFile(str(f), size, mtime_ns, content)
    assert state._matches(bf)

    data[len(data) // 2] ^= 0xFF  # flip one MIDDLE byte (a head+tail hash would miss this)
    f.write_bytes(bytes(data))
    os.utime(f, ns=(mtime_ns, mtime_ns))  # restore mtime: only the interior content differs
    assert not state._matches(bf)


def test_fingerprint_samples_interior_windows_of_large_files(tmp_path):
    # Above WHOLE_FILE_MAX the file is sampled, but at interior windows (not just head+tail): a
    # corruption in a middle window — same size, same mtime — is now caught.
    import os

    f = tmp_path / "big.mov"
    size = state.WHOLE_FILE_MAX + state.HASH_SPAN * 8
    f.write_bytes(b"\x00" * size)
    s, mtime_ns, content = state._fingerprint(f)
    bf = state.BackupFile(str(f), s, mtime_ns, content)
    assert state._matches(bf)

    last = size - state.HASH_SPAN
    interior_offset = last * 2 // (state.INTERIOR_SAMPLES + 1)  # inside a sampled middle window
    with open(f, "r+b") as fh:
        fh.seek(interior_offset + 128)
        fh.write(b"\xff")
    os.utime(f, ns=(mtime_ns, mtime_ns))
    assert not state._matches(bf)
