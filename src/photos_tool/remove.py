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

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


class RemoveError(RuntimeError):
    """Raised when originals cannot be safely removed."""


@dataclass(frozen=True)
class RemoveResult:
    requested: int
    deleted: int
    dry_run: bool


def build_local_identifiers(uuids: Iterable[str]) -> list[str]:
    """Map osxphotos UUIDs to PhotoKit local identifiers (``<uuid>/L0/001``)."""
    return [f"{uuid}/L0/001" for uuid in sorted({u for u in uuids if u})]


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
    # Positively verify Photos resolved exactly the assets we asked for — the count
    # match alone could coincidentally hold if the localIdentifier convention shifted.
    requested_uuids = {lid.split("/", 1)[0] for lid in local_ids}
    for index in range(fetch.count()):
        resolved = str(fetch.objectAtIndex_(index).localIdentifier()).split("/", 1)[0]
        if resolved not in requested_uuids:
            raise RemoveError("Photos resolved an unexpected asset; aborting without deleting")

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
    import threading

    level = photos.PHAccessLevelReadWrite
    status = photos.PHPhotoLibrary.authorizationStatusForAccessLevel_(level)

    if status == photos.PHAuthorizationStatusNotDetermined:
        # Actually request access — this is what shows the system prompt and registers
        # the launching app in System Settings > Privacy & Security > Photos. Just
        # checking the status (as before) never prompts, so the app never appears there.
        done = threading.Event()
        result: dict[str, int] = {}

        def handler(new_status: int) -> None:
            result["status"] = new_status
            done.set()

        photos.PHPhotoLibrary.requestAuthorizationForAccessLevel_handler_(level, handler)
        done.wait(timeout=180)
        status = result.get("status", status)

    if status != photos.PHAuthorizationStatusAuthorized:
        raise RemoveError(
            "photos-tool is not authorized to modify the Photos library. Approve the Photos "
            "prompt when it appears; if it does not, enable the app that runs photos-tool under "
            "System Settings > Privacy & Security > Photos (a packaged app shows the toggle), or "
            "reset with: tccutil reset Photos"
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
