from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from photos_tool.smb import SmbError, ensure_mounted, is_mounted, is_writable


def completed(stdout: str = "", returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(["fake"], returncode, stdout, stderr)


def test_is_mounted_parses_mount_output(tmp_path: Path):
    mount_point = tmp_path / "FamilyPhotos"

    def run(cmd: list[str], **_kwargs: Any):
        assert cmd == ["mount"]
        return completed(f"//photos@pc/FamilyPhotos on {mount_point} (smbfs, nodev)\n")

    assert is_mounted(mount_point, run=run)


def test_is_writable_uses_touch_probe(tmp_path: Path):
    assert is_writable(tmp_path)
    assert not is_writable(tmp_path / "missing")


def test_is_writable_returns_false_when_touch_fails(tmp_path: Path):
    readonly = tmp_path / "readonly"
    readonly.mkdir()
    readonly.chmod(0o555)
    try:
        assert not is_writable(readonly)
    finally:
        readonly.chmod(0o755)


def test_ensure_mounted_runs_osascript_then_validates(tmp_path: Path):
    mount_point = tmp_path / "FamilyPhotos"
    mount_point.mkdir()
    mounted = False
    calls: list[list[str]] = []

    def run(cmd: list[str], **_kwargs: Any):
        nonlocal mounted
        calls.append(cmd)
        if cmd == ["mount"]:
            output = f"//photos@pc/FamilyPhotos on {mount_point} (smbfs)\n" if mounted else ""
            return completed(output)
        if cmd[:2] == ["osascript", "-e"]:
            mounted = True
            return completed()
        raise AssertionError(cmd)

    ensure_mounted("smb://pc/FamilyPhotos", mount_point, run=run)

    assert any(call[:2] == ["osascript", "-e"] for call in calls)


def test_ensure_mounted_escapes_applescript_string(tmp_path: Path):
    mount_point = tmp_path / "FamilyPhotos"
    mount_point.mkdir()
    mounted = False
    osascript: list[str] = []

    def run(cmd: list[str], **_kwargs: Any):
        nonlocal mounted
        if cmd == ["mount"]:
            output = f"//photos@pc/FamilyPhotos on {mount_point} (smbfs)\n" if mounted else ""
            return completed(output)
        if cmd[:2] == ["osascript", "-e"]:
            osascript.extend(cmd)
            mounted = True
            return completed()
        raise AssertionError(cmd)

    ensure_mounted('smb://pc/Family"Photos', mount_point, run=run)

    assert osascript[-1] == 'mount volume "smb://pc/Family\\"Photos"'


def test_ensure_mounted_raises_when_mounted_but_not_writable(tmp_path: Path):
    mount_point = tmp_path / "missing"

    def run(cmd: list[str], **_kwargs: Any):
        assert cmd == ["mount"]
        return completed(f"//photos@pc/FamilyPhotos on {mount_point} (smbfs)\n")

    try:
        ensure_mounted("smb://pc/FamilyPhotos", mount_point, run=run)
    except SmbError as exc:
        assert "not writable" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected SmbError")
