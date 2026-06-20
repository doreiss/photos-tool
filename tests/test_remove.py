from __future__ import annotations

import pytest

from photos_tool import remove
from photos_tool.remove import RemoveError, RemoveResult, build_local_identifiers


def test_build_local_identifiers_sorts_dedups_and_drops_empty():
    assert build_local_identifiers(["b", "a", "a", ""]) == ["a/L0/001", "b/L0/001"]


class _FakeAsset:
    def __init__(self, local_id: str) -> None:
        self._local_id = local_id

    def localIdentifier(self) -> str:
        return self._local_id


class _FakeFetch:
    def __init__(self, assets: list[_FakeAsset]) -> None:
        self._assets = assets

    def count(self) -> int:
        return len(self._assets)

    def objectAtIndex_(self, index: int) -> _FakeAsset:
        return self._assets[index]


class _FakePhotos:
    """A minimal stand-in for the PhotoKit module."""

    def __init__(self, *, resolves: int, wrong_id: bool = False, delete_ok: bool = True) -> None:
        self._resolves = resolves
        self._wrong_id = wrong_id
        self._delete_ok = delete_ok
        self.deleted: list[object] = []
        outer = self

        class PHAsset:
            @staticmethod
            def fetchAssetsWithLocalIdentifiers_options_(ids, _opts):
                n = outer._resolves
                if outer._wrong_id:
                    assets = [_FakeAsset("00000000/L0/001") for _ in range(n)]
                else:
                    assets = [_FakeAsset(ids[i]) for i in range(min(n, len(ids)))]
                return _FakeFetch(assets)

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
    fake = _FakePhotos(resolves=2)
    _patch_photos(monkeypatch, fake)

    result = remove.remove_originals(["a", "b"])

    assert result == RemoveResult(requested=2, deleted=2, dry_run=False)
    assert len(fake.deleted) == 1


def test_remove_originals_fails_closed_on_count_mismatch(monkeypatch):
    fake = _FakePhotos(resolves=1)  # only one of two requested resolves
    _patch_photos(monkeypatch, fake)

    with pytest.raises(RemoveError, match="resolved in Photos"):
        remove.remove_originals(["a", "b"])
    assert fake.deleted == []


def test_remove_originals_aborts_when_photos_returns_an_unrequested_asset(monkeypatch):
    fake = _FakePhotos(resolves=2, wrong_id=True)  # count matches, identities do not
    _patch_photos(monkeypatch, fake)

    with pytest.raises(RemoveError, match="unexpected asset"):
        remove.remove_originals(["a", "b"])
    assert fake.deleted == []


def test_remove_originals_dry_run_deletes_nothing(monkeypatch):
    fake = _FakePhotos(resolves=2)
    _patch_photos(monkeypatch, fake)

    result = remove.remove_originals(["a", "b"], dry_run=True)

    assert result == RemoveResult(requested=2, deleted=0, dry_run=True)
    assert fake.deleted == []


def test_remove_originals_enforces_cap():
    with pytest.raises(RemoveError, match="cap"):
        remove.remove_originals([f"u{i}" for i in range(10)], max_delete=5)


def test_remove_originals_empty_is_noop():
    assert remove.remove_originals([]) == RemoveResult(requested=0, deleted=0, dry_run=False)
