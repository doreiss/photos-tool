from __future__ import annotations

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
    sizes = {asset.uuid: asset.files[0][1] for asset in token.assets}
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


def test_reveal_path_is_first_existing_file(tmp_path: Path):
    dest = tmp_path / "share"
    missing = dest / "gone.heic"
    present = _write(dest / "here.heic", "aaaa")
    state_dir = tmp_path / "state"
    state.save_backup_token(state_dir, dest, "smb://nas/share", {"u": (str(missing), str(present))})
    token = state.load_backup_token(state_dir, dest)
    assert token is not None
    assert state.reveal_path(token) == str(present)
