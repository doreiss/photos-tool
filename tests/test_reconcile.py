from __future__ import annotations

import pytest

from photos_tool.reconcile import Status, reconcile


def test_all_exported_is_ok():
    result = reconcile(selected=10, exported=10, missing=0)
    assert result.ok
    assert result.status is Status.OK


def test_missing_count_flags_a_skip():
    result = reconcile(selected=10, exported=9, missing=1)
    assert not result.ok
    assert result.status is Status.SKIPPED
    assert "Optimize Mac Storage" in result.message


def test_fewer_exported_than_selected_is_a_skip():
    result = reconcile(selected=10, exported=8, missing=0)
    assert not result.ok
    assert result.status is Status.SKIPPED


def test_empty_selection_is_not_ok():
    result = reconcile(selected=0, exported=0, missing=0)
    assert not result.ok
    assert result.status is Status.EMPTY


def test_more_files_than_assets_is_ok_and_explained():
    result = reconcile(selected=5, exported=9, missing=0)
    assert result.ok
    assert result.status is Status.OVER


def test_negative_counts_raise():
    with pytest.raises(ValueError):
        reconcile(selected=-1, exported=0, missing=0)
