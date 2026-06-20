"""The single delete-authorization record: one atomic, per-destination backup token.

A backup records exactly which originals were just sent and how big each landed
copy was. Cleanup later trusts ONLY this token, and only after re-verifying — for
every file — that a copy of the recorded size still sits on the share. That size
binding plus per-destination scoping is what stops a remounted/re-created share, a
config repoint, or another Mac's same-named ``IMG_0001.HEIC`` from authorizing the
deletion of an original whose real backup is gone.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class BackupAsset:
    uuid: str
    files: tuple[tuple[str, int], ...]  # (absolute destination path, size in bytes)


@dataclass(frozen=True)
class BackupToken:
    destination_root: str
    smb_url: str
    hostname: str
    timestamp: str
    assets: tuple[BackupAsset, ...]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hostname() -> str:
    return socket.gethostname().split(".")[0]


def _digest(destination: Path | str) -> str:
    canonical = str(Path(destination).expanduser().resolve(strict=False))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def token_path(state_dir: Path, destination: Path | str) -> Path:
    """One token file per destination, keyed like the export DB so destinations never mix."""
    return state_dir / f"backup-{_digest(destination)}.json"


def atomic_write_json(path: Path, payload: object) -> None:
    """Write JSON via temp-file + fsync + os.replace so a crash can't leave a torn file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def save_backup_token(
    state_dir: Path,
    destination: Path,
    smb_url: str,
    exported_paths: Mapping[str, tuple[str, ...]],
) -> None:
    """Record the just-sent batch, capturing each landed copy's current size."""
    assets: list[BackupAsset] = []
    for uuid, paths in sorted(exported_paths.items()):
        files: list[tuple[str, int]] = []
        for raw in paths:
            candidate = Path(raw)
            if not candidate.is_absolute():
                continue  # the delete gate only trusts absolute destination paths
            try:
                files.append((str(candidate), candidate.stat().st_size))
            except OSError:
                continue
        if files:
            assets.append(BackupAsset(uuid=uuid, files=tuple(files)))
    token = BackupToken(
        destination_root=str(destination),
        smb_url=smb_url,
        hostname=_hostname(),
        timestamp=_now_iso(),
        assets=tuple(assets),
    )
    atomic_write_json(token_path(state_dir, destination), _to_dict(token))


def load_backup_token(state_dir: Path, destination: Path) -> BackupToken | None:
    """Load the token for this destination, or ``None`` if missing/foreign/corrupt."""
    try:
        raw = json.loads(token_path(state_dir, destination).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        return None
    if raw.get("destination_root") != str(destination):
        return None  # token is for a different destination than the one configured now
    return _from_dict(raw)


def clear_backup_token(state_dir: Path, destination: Path) -> None:
    """Consume the token so the same batch can never be offered for deletion twice."""
    token_path(state_dir, destination).unlink(missing_ok=True)


def removable_assets(token: BackupToken) -> tuple[list[str], list[tuple[str, str]]]:
    """Partition the token's assets into (removable uuids, [(uuid, reason kept)]).

    An original is removable only if every recorded copy still exists on the share at
    the exact recorded size — catching deleted, truncated, or replaced copies.
    """
    removable: list[str] = []
    kept: list[tuple[str, str]] = []
    for asset in token.assets:
        if not asset.files:
            kept.append((asset.uuid, "no destination copy was recorded"))
            continue
        if all(_matches(path, size) for path, size in asset.files):
            removable.append(asset.uuid)
        else:
            kept.append((asset.uuid, "its copy is missing or changed on the share"))
    return removable, kept


def reveal_path(token: BackupToken) -> str:
    """The first still-present recorded file, for the GUI to reveal-and-select in Finder."""
    for asset in token.assets:
        for path, _size in asset.files:
            if Path(path).is_file():
                return path
    return token.destination_root


def _matches(path: str, size: int) -> bool:
    try:
        candidate = Path(path)
        return candidate.is_file() and candidate.stat().st_size == size
    except OSError:
        return False


def _to_dict(token: BackupToken) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "destination_root": token.destination_root,
        "smb_url": token.smb_url,
        "hostname": token.hostname,
        "timestamp": token.timestamp,
        "assets": [{"uuid": a.uuid, "files": [list(f) for f in a.files]} for a in token.assets],
    }


def _from_dict(raw: dict) -> BackupToken | None:
    assets: list[BackupAsset] = []
    for item in raw.get("assets") or []:
        if not isinstance(item, dict):
            return None
        files: list[tuple[str, int]] = []
        for entry in item.get("files") or []:
            if (
                isinstance(entry, list)
                and len(entry) == 2
                and isinstance(entry[0], str)
                and isinstance(entry[1], int)
            ):
                files.append((entry[0], entry[1]))
        assets.append(BackupAsset(uuid=str(item.get("uuid", "")), files=tuple(files)))
    return BackupToken(
        destination_root=str(raw.get("destination_root", "")),
        smb_url=str(raw.get("smb_url", "")),
        hostname=str(raw.get("hostname", "")),
        timestamp=str(raw.get("timestamp", "")),
        assets=tuple(assets),
    )
