from __future__ import annotations

import pytest

from photos_tool import remove
from photos_tool.reconcile import reconcile
from photos_tool.remove import (
    RemoveError,
    RemoveResult,
    build_local_identifiers,
    gate_cleanup,
    select_removable,
)
from photos_tool.report import ReportSummary


def _summary(
    uuids: set[str] | None,
    *,
    missing: int = 0,
    error: int = 0,
    paths: dict[str, tuple[str, ...]] | None = None,
) -> ReportSummary:
    n = len(uuids) if uuids else 0
    if uuids is not None and paths is None:
        paths = {u: (f"/share/{u}.heic",) for u in uuids}
    return ReportSummary(
        total_files=n + missing + error,
        exported=n,
        new=n,
        updated=0,
        skipped=0,
        converted=0,
        missing=missing,
        error=error,
        exported_uuids=frozenset(uuids) if uuids is not None else None,
        exported_paths=paths,
    )


def test_select_removable_only_returns_assets_whose_copy_is_on_the_share():
    report = _summary({"a", "b"}, paths={"a": ("/share/a.heic",), "b": ("/share/b.heic",)})

    removable, kept = select_removable(report, lambda p: p == "/share/a.heic")  # b's copy is gone

    assert removable == ["a"]
    assert [u for u, _ in kept] == ["b"]


def test_select_removable_skips_filename_collisions():
    report = _summary({"a", "b"}, paths={"a": ("/share/IMG.heic",), "b": ("/share/IMG.heic",)})

    removable, kept = select_removable(report, lambda _p: True)

    assert removable == []
    assert sorted(u for u, _ in kept) == ["a", "b"]


def test_select_removable_returns_all_when_present_and_distinct():
    report = _summary({"a", "b"})

    removable, kept = select_removable(report, lambda _p: True)

    assert removable == ["a", "b"]
    assert kept == []


def test_build_local_identifiers_sorts_dedups_and_drops_empty():
    assert build_local_identifiers(["b", "a", "a", ""]) == ["a/L0/001", "b/L0/001"]


def test_gate_allows_clean_export_with_uuids():
    report = _summary({"a", "b"})
    recon = reconcile(selected=2, exported=2, missing=0)
    allowed, reason = gate_cleanup(recon, report)
    assert allowed
    assert reason == ""


def test_gate_blocks_when_reconciliation_not_ok():
    report = _summary({"a"}, missing=1)
    recon = reconcile(selected=2, exported=1, missing=1)  # SKIPPED
    allowed, reason = gate_cleanup(recon, report)
    assert not allowed
    assert "reconcile" in reason


def test_gate_blocks_when_no_uuids():
    report = _summary(None)
    recon = reconcile(selected=2, exported=2, missing=0)
    allowed, reason = gate_cleanup(recon, report)
    assert not allowed
    assert "UUID" in reason


class _FakeFetch:
    def __init__(self, n: int) -> None:
        self._n = n

    def count(self) -> int:
        return self._n


class _FakePhotos:
    """A minimal stand-in for the PhotoKit module."""

    def __init__(self, fetch_count: int, delete_ok: bool = True) -> None:
        self._fetch_count = fetch_count
        self._delete_ok = delete_ok
        self.deleted: list[object] = []
        outer = self

        class PHAsset:
            @staticmethod
            def fetchAssetsWithLocalIdentifiers_options_(ids, _opts):
                return _FakeFetch(outer._fetch_count)

        class PHAssetChangeRequest:
            @staticmethod
            def deleteAssets_(fetch):
                outer.deleted.append(fetch)

        class _Library:
            def performChangesAndWait_error_(self, changes, _err):
                changes()
                return (outer._delete_ok, None if outer._delete_ok else "refused")

        class PHPhotoLibrary:
            @staticmethod
            def sharedPhotoLibrary():
                return _Library()

        self.PHAsset = PHAsset
        self.PHAssetChangeRequest = PHAssetChangeRequest
        self.PHPhotoLibrary = PHPhotoLibrary


def _patch_photos(monkeypatch, photos: _FakePhotos) -> None:
    monkeypatch.setattr(remove, "_import_photos", lambda: photos)
    monkeypatch.setattr(remove, "_require_authorization", lambda _p: None)


def test_remove_originals_deletes_when_every_uuid_resolves(monkeypatch):
    fake = _FakePhotos(fetch_count=2)
    _patch_photos(monkeypatch, fake)

    result = remove.remove_originals(["a", "b"])

    assert result == RemoveResult(requested=2, deleted=2, dry_run=False)
    assert len(fake.deleted) == 1


def test_remove_originals_fails_closed_on_count_mismatch(monkeypatch):
    # Only one of the two requested local identifiers resolves -> abort, delete nothing.
    fake = _FakePhotos(fetch_count=1)
    _patch_photos(monkeypatch, fake)

    with pytest.raises(RemoveError, match="resolved in Photos"):
        remove.remove_originals(["a", "b"])
    assert fake.deleted == []


def test_remove_originals_dry_run_deletes_nothing(monkeypatch):
    fake = _FakePhotos(fetch_count=2)
    _patch_photos(monkeypatch, fake)

    result = remove.remove_originals(["a", "b"], dry_run=True)

    assert result == RemoveResult(requested=2, deleted=0, dry_run=True)
    assert fake.deleted == []


def test_remove_originals_enforces_cap():
    with pytest.raises(RemoveError, match="cap"):
        remove.remove_originals([f"u{i}" for i in range(10)], max_delete=5)


def test_remove_originals_empty_is_noop():
    assert remove.remove_originals([]) == RemoveResult(requested=0, deleted=0, dry_run=False)
