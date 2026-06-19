"""Opt-in removal of just-exported originals from the Photos library.

This is the one intentionally destructive feature, so it is fail-closed. It only
runs after a clean reconciliation, and even then deletes an original only when its
copy is verified to exist on the share right now (``select_removable`` — a clean
reconcile alone is not enough, because a re-run reports already-known assets as
"skipped" even if their copies were since deleted) and no two assets collide on one
destination filename. It deletes exactly those UUIDs, aborts if any does not resolve
in Photos, and uses PhotoKit (not AppleScript, which cannot delete media items on
recent macOS) so deletions land in Recently Deleted, recoverable for ~30 days.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from .reconcile import Reconciliation, Status
from .report import ReportSummary


class RemoveError(RuntimeError):
    """Raised when originals cannot be safely removed."""


@dataclass(frozen=True)
class RemoveResult:
    requested: int
    deleted: int
    dry_run: bool


def gate_cleanup(reconciliation: Reconciliation, report: ReportSummary) -> tuple[bool, str]:
    """Decide whether it is safe to remove the exported originals (pure)."""
    if not reconciliation.ok or reconciliation.status is not Status.OK:
        return False, "the export did not reconcile cleanly (some items were skipped or missing)"
    if report.exported_uuids is None:
        return False, "the export report had no UUIDs, so assets cannot be matched safely"
    if not report.exported_uuids:
        return False, "no exported assets to remove"
    return True, ""


def build_local_identifiers(uuids: Iterable[str]) -> list[str]:
    """Map osxphotos UUIDs to PhotoKit local identifiers (``<uuid>/L0/001``)."""
    return [f"{uuid}/L0/001" for uuid in sorted({u for u in uuids if u})]


def select_removable(
    report: ReportSummary,
    exists: Callable[[str], bool],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Pick the UUIDs whose backup is verifiably on the share *right now* (pure).

    A clean reconciliation is not enough: a re-run reports already-known assets as
    ``skipped`` even if their copies were since deleted from the share, and two
    assets can collide on one destination filename. So an original is only eligible
    for deletion when every destination copy currently exists AND no copy is shared
    with another asset. Returns ``(removable_uuids, [(uuid, reason_kept)])``.
    """
    paths_by_uuid = report.exported_paths or {}
    owners: dict[str, set[str]] = {}
    for uuid, paths in paths_by_uuid.items():
        for path in paths:
            owners.setdefault(path, set()).add(uuid)

    removable: list[str] = []
    kept: list[tuple[str, str]] = []
    for uuid in sorted(report.exported_uuids or []):
        paths = paths_by_uuid.get(uuid, ())
        if not paths:
            kept.append((uuid, "no destination path was recorded"))
        elif any(len(owners.get(path, ())) > 1 for path in paths):
            kept.append((uuid, "shares a destination filename with another photo"))
        elif not all(exists(path) for path in paths):
            kept.append((uuid, "its copy is not on the share"))
        else:
            removable.append(uuid)
    return removable, kept


def remove_originals(
    uuids: Iterable[str],
    *,
    dry_run: bool = False,
    max_delete: int = 500,
) -> RemoveResult:
    """Move the given assets to Recently Deleted via PhotoKit (fail-closed)."""
    local_ids = build_local_identifiers(uuids)
    if not local_ids:
        return RemoveResult(requested=0, deleted=0, dry_run=dry_run)
    if len(local_ids) > max_delete:
        raise RemoveError(
            f"refusing to remove {len(local_ids)} assets at once (cap is {max_delete}); "
            "raise [remove].max_delete if this is expected"
        )

    photos = _import_photos()
    _require_authorization(photos)

    fetch = photos.PHAsset.fetchAssetsWithLocalIdentifiers_options_(local_ids, None)
    if fetch.count() != len(local_ids):
        # Fail closed: never delete a partial/ambiguous set.
        raise RemoveError(
            f"only {fetch.count()} of {len(local_ids)} exported assets resolved in Photos; "
            "aborting without deleting anything"
        )

    if dry_run:
        return RemoveResult(requested=len(local_ids), deleted=0, dry_run=True)

    deleted = _perform_delete(photos, fetch)
    return RemoveResult(requested=len(local_ids), deleted=deleted, dry_run=False)


def _import_photos() -> Any:  # pragma: no cover - requires macOS PhotoKit
    try:
        import Photos  # pyright: ignore[reportMissingImports]
    except ImportError as exc:
        raise RemoveError(
            "PhotoKit (pyobjc) is unavailable; --remove-originals only works on macOS"
        ) from exc
    return Photos


def _require_authorization(photos: Any) -> None:  # pragma: no cover - requires TCC grant
    level = photos.PHAccessLevelReadWrite
    status = photos.PHPhotoLibrary.authorizationStatusForAccessLevel_(level)
    if status != photos.PHAuthorizationStatusAuthorized:
        raise RemoveError(
            "photos-tool is not authorized to modify the Photos library. Grant Photos access "
            "to the app that runs photos-tool in System Settings > Privacy & Security > Photos, "
            "then retry."
        )


def _perform_delete(photos: Any, fetch: Any) -> int:  # pragma: no cover - requires PhotoKit
    count = fetch.count()

    def changes() -> None:
        photos.PHAssetChangeRequest.deleteAssets_(fetch)

    ok, error = photos.PHPhotoLibrary.sharedPhotoLibrary().performChangesAndWait_error_(
        changes, None
    )
    if not ok:
        raise RemoveError(f"Photos refused the delete: {error}")
    return count
