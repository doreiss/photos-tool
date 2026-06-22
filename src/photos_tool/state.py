"""The single delete-authorization record: one atomic, per-destination backup token.

A backup records, for every file it just sent, a content fingerprint of the copy
that actually landed on the share — its size, its modification time, and a SHA-256
of the first and last 64 KiB. Cleanup later trusts ONLY this token, and only after
re-computing that same fingerprint for every file and confirming it still matches.
That content binding (not a bare byte-count) plus per-destination scoping is what
stops a remounted/re-created share, a config repoint, a same-size overwrite, a
truncated/empty copy, or another Mac's same-named ``IMG_0001.HEIC`` from authorizing
the deletion of an original whose real backup is gone.

A backup record is ALL-OR-NOTHING per asset: if any one file of a multi-file asset
(Live Photo HEIC+MOV, RAW+JPEG, edited+original) cannot be fully fingerprinted at
save time, the ENTIRE asset is dropped from the token — never a partial record —
because the delete removes the whole asset (all its files) at once.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# 1 -> 2 added the content-addressed token; 2 -> 3 widened the fingerprint to hash the WHOLE
# file under WHOLE_FILE_MAX and to sample interior windows above it (closing the head/tail-only
# "middle blind spot"). An older-schema token is read as "no pending backup" (load returns None)
# so its originals are never offered for deletion — the safe direction; the user re-runs send.
SCHEMA_VERSION = 3

# Fingerprint strategy. Photos and short clips (the overwhelming majority) are hashed in FULL —
# no blind spot at all. Only files larger than this are sampled (hashing tens of GB of video over
# SMB twice — at save and at cleanup — would be unworkable), and even then we read the head, the
# tail, and several deterministic interior windows so a same-size, same-mtime middle corruption
# no longer slips through unseen. _matches still checks size and mtime_ns separately.
HASH_SPAN = 64 * 1024
WHOLE_FILE_MAX = 16 * 1024 * 1024  # hash the entire file at/under 16 MiB
INTERIOR_SAMPLES = 4  # interior HASH_SPAN windows for larger files (besides head + tail)


@dataclass(frozen=True)
class BackupFile:
    """A single landed copy on the share and the fingerprint we will re-verify."""

    path: str  # absolute destination path
    size: int  # bytes
    mtime_ns: int  # st_mtime_ns of the landed copy
    content: str  # sha256 of the copy's content (whole file <=16 MiB, else sampled windows)


@dataclass(frozen=True)
class BackupAsset:
    uuid: str
    files: tuple[BackupFile, ...]  # never empty: an asset with an unverifiable file is dropped


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
    """Write JSON via temp-file + fsync + os.replace so a crash can't leave a torn file.

    The file is created 0600: the token records share paths, hostname, and the SMB URL.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        os.fchmod(fh.fileno(), 0o600)
        json.dump(payload, fh, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _fingerprint(path: Path) -> tuple[int, int, str]:
    """Content fingerprint of a landed copy: (size, mtime_ns, sha256 of its content).

    Files at/under ``WHOLE_FILE_MAX`` are hashed in full; larger files are sampled at the head,
    the tail, and ``INTERIOR_SAMPLES`` deterministic interior windows (offsets derived purely
    from ``size``, so save-time and cleanup-time fingerprints are always comparable).

    Raises ``OSError`` if the path is absent or not a regular file, and ``ValueError`` for an
    empty (0-byte) file — so an empty copy is NEVER recorded as a backup and can never authorize
    a deletion (the README's "present and non-empty" promise, enforced rather than documented).
    """
    st = path.stat()  # OSError if absent
    if not stat.S_ISREG(st.st_mode):
        raise OSError(f"not a regular file: {path}")
    size = st.st_size
    if size <= 0:
        raise ValueError(f"refusing to record an empty file: {path}")
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        if size <= WHOLE_FILE_MAX:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                hasher.update(chunk)
        else:
            # head, INTERIOR_SAMPLES interior windows, tail — evenly spaced across the file at
            # offsets that depend only on `size`, so the same windows are read every time.
            windows = INTERIOR_SAMPLES + 2
            last = size - HASH_SPAN
            for i in range(windows):
                offset = min(last * i // (windows - 1), last)
                fh.seek(offset)
                hasher.update(fh.read(HASH_SPAN))
    return size, st.st_mtime_ns, hasher.hexdigest()


def save_backup_token(
    state_dir: Path,
    destination: Path,
    smb_url: str,
    exported_paths: Mapping[str, tuple[str, ...]],
) -> list[tuple[str, str]]:
    """Record the just-sent batch by content-fingerprinting each landed copy.

    Returns the list of ``(uuid, reason)`` assets that were DROPPED because at least
    one of their files could not be fully fingerprinted (missing/empty/unreadable, or
    a non-absolute path). A dropped asset is never offered for deletion — the caller
    should surface the count so a partially-landed batch is visible, not silent.
    """
    assets: list[BackupAsset] = []
    skipped: list[tuple[str, str]] = []
    for uuid, paths in sorted(exported_paths.items()):
        files: list[BackupFile] = []
        reason = ""
        for raw in paths:
            candidate = Path(raw)
            if not candidate.is_absolute():
                reason = "a destination path was not absolute"
                break  # all-or-nothing: drop the whole asset
            try:
                size, mtime_ns, content = _fingerprint(candidate)
            except (OSError, ValueError):
                reason = "a backup copy was missing, empty, or unreadable on the share"
                break  # all-or-nothing: drop the whole asset
            files.append(BackupFile(str(candidate), size, mtime_ns, content))
        if not reason and not files:
            reason = "osxphotos reported no destination copy"
        if reason:
            skipped.append((uuid, reason))
            continue
        assets.append(BackupAsset(uuid=uuid, files=tuple(files)))
    token = BackupToken(
        destination_root=str(destination),
        smb_url=smb_url,
        hostname=_hostname(),
        timestamp=_now_iso(),
        assets=tuple(assets),
    )
    atomic_write_json(token_path(state_dir, destination), _to_dict(token))
    return skipped


def load_backup_token(state_dir: Path, destination: Path) -> BackupToken | None:
    """Load the token for this destination, or ``None`` if missing/foreign/corrupt/old."""
    try:
        raw = json.loads(token_path(state_dir, destination).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or not _is_current_schema(raw):
        return None
    if raw.get("destination_root") != str(destination):
        return None  # token is for a different destination than the one configured now
    return _from_dict(raw)


def stale_token_exists(state_dir: Path, destination: Path) -> bool:
    """True if a token file is present but from an incompatible schema version.

    Lets cleanup tell "a backup from an older version" apart from "no backup at all"
    (both load as ``None``), so the user gets an actionable message instead of silence.
    """
    try:
        raw = json.loads(token_path(state_dir, destination).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(raw, dict) and not _is_current_schema(raw)


def clear_backup_token(state_dir: Path, destination: Path) -> None:
    """Consume the token so the same batch can never be offered for deletion twice."""
    token_path(state_dir, destination).unlink(missing_ok=True)


def removable_assets(token: BackupToken) -> tuple[list[str], list[tuple[str, str]]]:
    """Partition the token's assets into (removable uuids, [(uuid, reason kept)]).

    An original is removable only if EVERY recorded copy still fingerprints identically
    on the share — size, mtime, and head/tail content — catching deleted, truncated,
    emptied, same-size-overwritten, or corrupted copies — AND no recorded copy path is
    shared with another asset. The collision guard matters because one physical file on
    the share can be the backup of only ONE original; if two assets recorded the same
    path, deleting both would lose the original whose copy that file is not.
    """
    owners: dict[str, set[str]] = {}
    for asset in token.assets:
        for f in asset.files:
            owners.setdefault(f.path, set()).add(asset.uuid)

    removable: list[str] = []
    kept: list[tuple[str, str]] = []
    for asset in token.assets:
        if not asset.files:
            kept.append((asset.uuid, "no destination copy was recorded"))
        elif any(len(owners.get(f.path, ())) > 1 for f in asset.files):
            kept.append((asset.uuid, "shares a destination filename with another photo"))
        elif all(_matches(f) for f in asset.files):
            removable.append(asset.uuid)
        else:
            kept.append((asset.uuid, "its copy is missing or changed on the share"))
    return removable, kept


def reveal_paths(token: BackupToken, removable: set[str]) -> list[str]:
    """One still-verified file per REMOVABLE asset, for the GUI to reveal in Finder.

    Restricted to the assets that will actually be deleted (and re-checked with the
    same content gate the delete uses), so the human "did they arrive?" spot-check can
    never show a file that is being kept while a different one is deleted.
    """
    seen: set[str] = set()
    paths: list[str] = []
    for asset in token.assets:
        if asset.uuid not in removable:
            continue
        for f in asset.files:
            if f.path not in seen and _matches(f):
                seen.add(f.path)
                paths.append(f.path)
                break  # one representative file per removable asset
    return paths


def _matches(f: BackupFile) -> bool:
    """True iff the copy on the share still fingerprints exactly as recorded."""
    try:
        size, mtime_ns, content = _fingerprint(Path(f.path))
    except (OSError, ValueError):
        return False
    return size == f.size and mtime_ns == f.mtime_ns and content == f.content


def _to_dict(token: BackupToken) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "destination_root": token.destination_root,
        "smb_url": token.smb_url,
        "hostname": token.hostname,
        "timestamp": token.timestamp,
        "assets": [
            {
                "uuid": a.uuid,
                "files": [
                    {
                        "path": f.path,
                        "size": f.size,
                        "mtime_ns": f.mtime_ns,
                        "content": f.content,
                    }
                    for f in a.files
                ],
            }
            for a in token.assets
        ],
    }


def _from_dict(raw: dict) -> BackupToken | None:
    assets: list[BackupAsset] = []
    raw_assets = raw.get("assets")
    for item in raw_assets if isinstance(raw_assets, list) else []:
        if not isinstance(item, dict):
            return None
        files: list[BackupFile] = []
        malformed = False
        raw_files = item.get("files")
        for entry in raw_files if isinstance(raw_files, list) else []:
            if _valid_file_entry(entry):
                files.append(
                    BackupFile(
                        path=entry["path"],
                        size=entry["size"],
                        mtime_ns=entry["mtime_ns"],
                        content=entry["content"],
                    )
                )
            else:
                malformed = True
                break
        # Drop a corrupt or empty asset entirely: it can then never be removable
        # (fail-safe — the original is kept), and a partial file list can never
        # under-verify a multi-file asset.
        if malformed or not files:
            continue
        assets.append(BackupAsset(uuid=str(item.get("uuid", "")), files=tuple(files)))
    return BackupToken(
        destination_root=str(raw.get("destination_root", "")),
        smb_url=str(raw.get("smb_url", "")),
        hostname=str(raw.get("hostname", "")),
        timestamp=str(raw.get("timestamp", "")),
        assets=tuple(assets),
    )


def _is_current_schema(raw: dict) -> bool:
    # Require an exact int match: a JSON float 2.0 (== 2 in Python) or bool must NOT read
    # as the current schema — a token written by anything but this version is treated as old.
    version = raw.get("schema_version")
    return isinstance(version, int) and not isinstance(version, bool) and version == SCHEMA_VERSION


def _valid_file_entry(entry: object) -> bool:
    # bool is an int subclass; reject a JSON true/false masquerading as size/mtime.
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("path"), str)
        and isinstance(entry.get("size"), int)
        and not isinstance(entry.get("size"), bool)
        and isinstance(entry.get("mtime_ns"), int)
        and not isinstance(entry.get("mtime_ns"), bool)
        and isinstance(entry.get("content"), str)
    )
