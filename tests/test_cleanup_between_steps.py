from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from photos_tool import cli, state
from test_send_fake_tools import _row_at, scenario, write_config


def _config_for(tmp_path: Path, mount: Path, *, subpath: str = "") -> Path:
    """Like write_config but lets a test point two configs at different mounts.

    The state dirs are shared (tmp_path/state), so two destinations exercise the
    per-destination token keying rather than two isolated state trees.
    """
    config = tmp_path / f"config-{mount.name}.toml"
    config.write_text(
        f"""
[destination]
smb_url = "smb://pc/FamilyPhotos"
mount_point = {str(mount)!r}
subpath = {subpath!r}

[export]
directory_template = "{{created.year}}/{{created.mm}}"
filename_template = "{{original_name}}"
download_missing = true
use_photokit = false
retry = 3

[copies]
jpeg = false
jpeg_quality = 0.9
mp4 = false
mp4_crf = 20

[state]
exportdb_dir = {str(tmp_path / "state" / "exportdb")!r}
log_dir = {str(tmp_path / "state" / "logs")!r}
""",
        encoding="utf-8",
    )
    return config


def _backup_one(tmp_path: Path, mount: Path, fake_tools, uuid: str = "a"):
    """Record a one-asset backup landing at <mount>/2024/01/<uuid>.heic.

    Returns (config_path, tools). The harness can only be installed once per test, so
    later "between steps" mutations work on the live filesystem / fake state file rather
    than re-installing a new scenario.
    """
    config = write_config(tmp_path, mount)
    rel = f"2024/01/{uuid}.heic"
    tools = fake_tools(
        scenario(
            mount,
            selected=1,
            report=[_row_at(uuid, mount, rel)],
            files=[rel],
        )
    )
    assert cli.main(["send", "--config", str(config)]) == 0
    return config, tools


def _rewrite_scenario(tools, new_scenario: dict[str, Any]) -> None:
    """Swap the live scenario JSON the fake tools read on each call.

    The fake-tool harness can only be *installed* once per test (its bin/ dir is created
    non-idempotently), but every fake reads PHOTOS_TOOL_FAKE_SCENARIO fresh on each
    invocation. Rewriting that file is how a test changes external reality BETWEEN steps
    (e.g. a second backup to another destination, or the share going unmounted) without a
    second install.
    """
    tools.scenario_path.write_text(json.dumps(new_scenario), encoding="utf-8")


def _set_mounted(tools, mounted: bool) -> None:
    """Force the fake `mount`/`osascript` state file to a mounted/unmounted reading."""
    tools.state_path.write_text(json.dumps({"mounted": mounted}), encoding="utf-8")


def _fake_remove(seen: dict[str, Any]):
    from photos_tool.remove import RemoveResult

    def fake_remove(uuids, *, dry_run=False, max_delete=500):
        ids = sorted(uuids)
        seen["uuids"] = ids
        return RemoveResult(requested=len(ids), deleted=len(ids), dry_run=dry_run)

    return fake_remove


# --- 1. share copy DELETED between backup and cleanup -----------------------------------------


def test_cleanup_keeps_original_when_copy_deleted_after_backup(tmp_path, fake_tools, capsys):
    """Guards: a backup copy deleted from the share between send and cleanup must NOT
    authorize deleting the original (the size/presence re-check, not the token alone)."""
    mount = tmp_path / "share"
    mount.mkdir()
    config, _tools = _backup_one(tmp_path, mount, fake_tools)
    capsys.readouterr()

    (mount / "2024" / "01" / "a.heic").unlink()

    assert cli.main(["cleanup-last", "--config", str(config), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["count"] == 0

    # And a real (non-json) cleanup must remove nothing and keep the token's authority gone.
    assert cli.main(["cleanup-last", "--config", str(config), "--yes"]) == 0
    captured = capsys.readouterr()
    assert "nothing to remove" in captured.out.lower()


# --- 2. share copy TRUNCATED / size-changed between backup and cleanup -------------------------


def test_cleanup_keeps_original_when_copy_truncated_after_backup(tmp_path, fake_tools, capsys):
    """Guards: a copy whose bytes changed (truncated/replaced) after the recorded size
    must be treated as not-backed-up; the original is kept."""
    mount = tmp_path / "share"
    mount.mkdir()
    config, _tools = _backup_one(tmp_path, mount, fake_tools)
    capsys.readouterr()

    copy = mount / "2024" / "01" / "a.heic"
    assert copy.stat().st_size == 4  # "fake"
    copy.write_text("x", encoding="utf-8")  # size 1 != recorded 4

    assert cli.main(["cleanup-last", "--config", str(config), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["count"] == 0


def test_cleanup_keeps_original_when_copy_grows_after_backup(tmp_path, fake_tools, capsys):
    """Guards: a copy that GREW (e.g. another Mac overwrote with a larger same-named file)
    is a size mismatch too and must keep the original — not just shrinkage is suspicious."""
    mount = tmp_path / "share"
    mount.mkdir()
    config, _tools = _backup_one(tmp_path, mount, fake_tools)
    capsys.readouterr()

    copy = mount / "2024" / "01" / "a.heic"
    copy.write_text("fake-but-bigger", encoding="utf-8")  # > recorded 4 bytes

    assert cli.main(["cleanup-last", "--config", str(config), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["count"] == 0


def test_cleanup_keeps_original_when_copy_same_size_different_content(tmp_path, fake_tools, capsys):
    """Guards (end-to-end): a same-size overwrite with the ORIGINAL mtime restored — so size
    and mtime both still match — is still caught by the head/tail content hash. The original
    is kept; this exercises content discrimination through the real send->cleanup pipeline."""
    import os

    mount = tmp_path / "share"
    mount.mkdir()
    config, _tools = _backup_one(tmp_path, mount, fake_tools)
    capsys.readouterr()

    copy = mount / "2024" / "01" / "a.heic"
    st = copy.stat()
    assert st.st_size == 4  # "fake"
    copy.write_text("FAKE", encoding="utf-8")  # same 4 bytes, different content
    os.utime(copy, ns=(st.st_atime_ns, st.st_mtime_ns))  # restore mtime: ONLY content differs

    assert cli.main(["cleanup-last", "--config", str(config), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["count"] == 0


# --- 3. backup to A, then backup to B, then cleanup acts on the CONFIGURED dest only ----------


def test_cleanup_acts_only_on_configured_destination_never_mixes(tmp_path, fake_tools, capsys):
    """Guards: per-destination token keying. A backup to A then a backup to B must leave
    two independent tokens; cleanup on B removes B's uuids only and never touches A's."""
    mount_a = tmp_path / "shareA"
    mount_a.mkdir()
    mount_b = tmp_path / "shareB"
    mount_b.mkdir()

    config_a = _config_for(tmp_path, mount_a)
    config_b = _config_for(tmp_path, mount_b)

    tools = fake_tools(
        scenario(
            mount_a,
            selected=1,
            report=[_row_at("a", mount_a, "2024/01/a.heic")],
            files=["2024/01/a.heic"],
        )
    )
    assert cli.main(["send", "--config", str(config_a)]) == 0

    # Second backup, different destination. Swap the live scenario rather than re-installing.
    _rewrite_scenario(
        tools,
        scenario(
            mount_b,
            selected=1,
            report=[_row_at("b", mount_b, "2024/01/b.heic")],
            files=["2024/01/b.heic"],
        ),
    )
    assert cli.main(["send", "--config", str(config_b)]) == 0
    capsys.readouterr()

    log_dir = tmp_path / "state" / "logs"
    # Both tokens coexist and are keyed to their own destination.
    token_a = state.load_backup_token(log_dir, mount_a)
    token_b = state.load_backup_token(log_dir, mount_b)
    assert token_a is not None and [x.uuid for x in token_a.assets] == ["a"]
    assert token_b is not None and [x.uuid for x in token_b.assets] == ["b"]

    seen: dict[str, Any] = {}
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(cli, "remove_originals", _fake_remove(seen))
    try:
        assert cli.main(["cleanup-last", "--config", str(config_b), "--yes"]) == 0
    finally:
        monkeypatch.undo()
    captured = capsys.readouterr()

    # Cleanup on B removed exactly B's asset, never A's.
    assert seen["uuids"] == ["b"]
    assert "Moved 1" in captured.out
    # A's token is untouched and still removable; B's is consumed.
    assert state.load_backup_token(log_dir, mount_a) is not None
    assert state.load_backup_token(log_dir, mount_b) is None


# --- 4. share UNMOUNTED / not writable at cleanup time -> abort, delete nothing ----------------


def test_cleanup_aborts_when_share_unmounted_and_unremountable(tmp_path, fake_tools, capsys):
    """Guards: cleanup must re-verify the share is mounted+writable before deleting. If the
    share is gone and cannot be remounted, it aborts (EXIT_PREFLIGHT) and keeps the token."""
    mount = tmp_path / "share"
    mount.mkdir()
    config, tools = _backup_one(tmp_path, mount, fake_tools)
    capsys.readouterr()

    # The share is now unmounted AND the remount fails. Swap the live scenario (the fakes
    # read it fresh each call) and force the mount-state file to "unmounted".
    _rewrite_scenario(tools, scenario(mount, initial_mounted=False, mount_fail=True))
    _set_mounted(tools, False)

    seen: dict[str, Any] = {}
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(cli, "remove_originals", _fake_remove(seen))
    try:
        rc = cli.main(["cleanup-last", "--config", str(config), "--yes"])
    finally:
        monkeypatch.undo()
    captured = capsys.readouterr()

    assert rc == cli.EXIT_PREFLIGHT
    assert "preflight error" in captured.err
    assert "uuids" not in seen  # nothing was deleted
    # Token survives so a later, healthy cleanup can still run.
    assert state.load_backup_token(tmp_path / "state" / "logs", mount) is not None


def test_cleanup_aborts_when_mount_present_but_not_writable(tmp_path, fake_tools, capsys):
    """Guards: a mounted-but-read-only share (e.g. permissions revoked) must also abort
    cleanup rather than silently 'succeed' against an unwritable volume."""
    mount = tmp_path / "share"
    mount.mkdir()
    config, tools = _backup_one(tmp_path, mount, fake_tools)
    capsys.readouterr()

    # mount still reports the path (state stays mounted), but the directory is gone, so the
    # write-probe in is_writable fails -> ensure_mounted raises "mounted but not writable".
    import shutil

    _set_mounted(tools, True)
    shutil.rmtree(mount)

    seen: dict[str, Any] = {}
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(cli, "remove_originals", _fake_remove(seen))
    try:
        rc = cli.main(["cleanup-last", "--config", str(config), "--yes"])
    finally:
        monkeypatch.undo()
    captured = capsys.readouterr()

    assert rc == cli.EXIT_PREFLIGHT
    assert "preflight error" in captured.err
    assert "uuids" not in seen
    assert state.load_backup_token(tmp_path / "state" / "logs", mount) is not None


# --- 5. token CONSUMED, then cleanup-last re-run -> nothing to clean up, never re-offers -------


def test_cleanup_second_run_after_consume_says_nothing_and_never_reoffers(
    tmp_path, fake_tools, capsys
):
    """Guards: once a batch is deleted the token is consumed; a re-run must report that
    there is no backup to clean (EXIT_USAGE) and never re-offer the same originals."""
    mount = tmp_path / "share"
    mount.mkdir()
    config, _tools = _backup_one(tmp_path, mount, fake_tools)
    capsys.readouterr()

    seen: dict[str, Any] = {}
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(cli, "remove_originals", _fake_remove(seen))
    try:
        assert cli.main(["cleanup-last", "--config", str(config), "--yes"]) == 0
        assert seen["uuids"] == ["a"]
        capsys.readouterr()

        # Second run: the token is consumed. The copy is STILL on the share, proving the
        # guard is the token, not just file presence — it must not re-offer the delete.
        seen.clear()
        rc = cli.main(["cleanup-last", "--config", str(config), "--yes"])
    finally:
        monkeypatch.undo()
    captured = capsys.readouterr()

    assert rc == cli.EXIT_USAGE
    assert "No backup recorded" in captured.err
    assert "uuids" not in seen  # never re-offered for deletion

    # The --json GUI path on a consumed token must also error, not show a stale count.
    rc_json = cli.main(["cleanup-last", "--config", str(config), "--json"])
    assert rc_json == cli.EXIT_USAGE
    assert "No backup recorded" in capsys.readouterr().err


# --- 6. second send while one is "running": per-destination flock ------------------------------


def test_second_send_blocks_only_its_own_destination_not_others(tmp_path, fake_tools, capsys):
    """Guards: the send lock is per-destination. Holding A's lock blocks a second send to A,
    but a send to a DIFFERENT destination B is unaffected (locks must not be global)."""
    mount_a = tmp_path / "shareA"
    mount_a.mkdir()
    mount_b = tmp_path / "shareB"
    mount_b.mkdir()
    config_a = _config_for(tmp_path, mount_a)
    config_b = _config_for(tmp_path, mount_b)

    # Start pointed at A (so A's mount check passes) — A's send will hit the held lock.
    tools = fake_tools(
        scenario(
            mount_a,
            selected=1,
            report=[_row_at("a", mount_a, "2024/01/a.heic")],
            files=["2024/01/a.heic"],
        )
    )

    exportdb_dir = tmp_path / "state" / "exportdb"
    held_a = cli._acquire_destination_lock(exportdb_dir, mount_a)
    assert held_a is not None
    try:
        # A second send to A is rejected while A's lock is held, AFTER its preflight passes —
        # so the rejection is the flock, not a failed mount/selection check.
        rc_a = cli.main(["send", "--config", str(config_a)])
        captured_a = capsys.readouterr()
        # Repoint reality at B and send to B: a different destination must NOT be blocked.
        _rewrite_scenario(
            tools,
            scenario(
                mount_b,
                selected=1,
                report=[_row_at("b", mount_b, "2024/01/b.heic")],
                files=["2024/01/b.heic"],
            ),
        )
        rc_b = cli.main(["send", "--config", str(config_b)])
    finally:
        held_a.close()
    captured = capsys.readouterr()

    assert rc_a == cli.EXIT_PREFLIGHT
    assert "already running" in captured_a.err
    assert rc_b == cli.EXIT_OK
    assert "All 1 selected" in captured.out
    exports = [
        e["argv"] for e in tools.log() if e["tool"] == "osxphotos" and e["argv"][:1] == ["export"]
    ]
    assert len(exports) == 1  # only B's export ran; A never started


# --- 7. config repoint (smb_url changed) between backup and cleanup -> refuse -------------------


def test_cleanup_refuses_when_smb_url_repointed_after_backup(tmp_path, fake_tools, capsys):
    """Guards: if the configured share URL changed since the backup, the recorded copies may
    live on a different physical NAS; cleanup must refuse rather than trust same-named files."""
    mount = tmp_path / "share"
    mount.mkdir()
    config, tools = _backup_one(tmp_path, mount, fake_tools)
    capsys.readouterr()

    # Repoint the config's smb_url to a different server while mount_point/files are unchanged.
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            'smb_url = "smb://pc/FamilyPhotos"',
            'smb_url = "smb://other-nas/FamilyPhotos"',
        ),
        encoding="utf-8",
    )
    # The share path is unchanged and still mounted; only the configured smb_url moved.
    _set_mounted(tools, True)

    seen: dict[str, Any] = {}
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(cli, "remove_originals", _fake_remove(seen))
    try:
        rc = cli.main(["cleanup-last", "--config", str(config), "--yes"])
    finally:
        monkeypatch.undo()
    captured = capsys.readouterr()

    assert rc == cli.EXIT_PREFLIGHT
    assert "share has changed" in captured.err
    assert "uuids" not in seen  # nothing deleted
    assert state.load_backup_token(tmp_path / "state" / "logs", mount) is not None
