"""SMB mount validation for macOS shares."""

from __future__ import annotations

import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path


class SmbError(RuntimeError):
    """Raised when the configured share cannot be mounted or written."""


Run = Callable[..., subprocess.CompletedProcess[str]]


def is_mounted(mount_point: Path, run: Run = subprocess.run) -> bool:
    result = run(["mount"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return False
    return _mount_output_has_path(result.stdout, mount_point)


def is_writable(mount_point: Path) -> bool:
    try:
        if not mount_point.exists() or not mount_point.is_dir():
            return False
        probe = mount_point / f".photos-tool-write-test-{uuid.uuid4().hex}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def ensure_mounted(
    smb_url: str,
    mount_point: Path,
    run: Run = subprocess.run,
) -> None:
    """Ensure ``mount_point`` is mounted and writable.

    The AppleScript mount path uses Keychain credentials; passwords are never
    passed in argv or stored in config.
    """
    if is_mounted(mount_point, run=run):
        if is_writable(mount_point):
            return
        raise SmbError(f"{mount_point} is mounted but not writable")

    if not smb_url:
        if is_writable(mount_point):
            return
        raise SmbError(f"{mount_point} is not mounted or writable")

    result = run(
        ["osascript", "-e", f"mount volume {_applescript_string(smb_url)}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise SmbError(
            f"could not mount {smb_url}: {detail or 'osascript failed'}. "
            "Connect once in Finder and save the password in Keychain."
        )

    if not is_mounted(mount_point, run=run):
        raise SmbError(
            f"{smb_url} did not appear at {mount_point}. macOS may have mounted it at a "
            "collision path such as /Volumes/Share-1; unmount duplicates and retry."
        )
    if not is_writable(mount_point):
        raise SmbError(f"{mount_point} mounted but is not writable")


def _mount_output_has_path(output: str, mount_point: Path) -> bool:
    # `mount` prints "<device> on <mount-point> (<options>)". Extract the mount-point
    # FIELD and compare it exactly, instead of substring-matching " on <target> " — a
    # substring can false-positive on a device/option string that merely contains the
    # target (e.g. another volume mounted at a path that embeds this one).
    target = str(mount_point)
    for line in output.splitlines():
        _, sep, rest = line.partition(" on ")
        if not sep:
            continue
        path = rest.rsplit(" (", 1)[0] if " (" in rest else rest
        if path.strip() == target:
            return True
    return False


def _applescript_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
