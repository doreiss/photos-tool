from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from photos_tool import osxphotos_runner
from photos_tool.plan import ExportOptions


def _completed(returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(["fake"], returncode, stdout, stderr)


def test_run_export_rejects_cleanup_flag(tmp_path: Path):
    with pytest.raises(ValueError, match="--cleanup"):
        osxphotos_runner.run_export(
            ExportOptions(destination="/Volumes/FamilyPhotos"),
            tmp_path / "report.json",
            extra=["--cleanup"],
        )


def test_run_export_creates_missing_destination(tmp_path: Path, monkeypatch):
    # osxphotos refuses a destination that does not exist; run_export must create it
    # (e.g. the compat/ subtree or a per-Mac subpath) before invoking osxphotos.
    dest = tmp_path / "share" / "compat"
    monkeypatch.setattr(osxphotos_runner, "_run", lambda cmd, timeout: _completed())

    result = osxphotos_runner.run_export(
        ExportOptions(destination=str(dest)), tmp_path / "report.json"
    )

    assert dest.is_dir()
    assert result.ok


def test_count_assets_treats_empty_selection_as_zero(monkeypatch):
    monkeypatch.setattr(
        osxphotos_runner,
        "_run",
        lambda cmd, timeout: _completed(
            1, stderr="--selected option used but no photos selected in Photos."
        ),
    )
    assert osxphotos_runner.count_assets(scope="selected") == 0


def test_count_assets_raises_on_other_query_errors(monkeypatch):
    monkeypatch.setattr(
        osxphotos_runner,
        "_run",
        lambda cmd, timeout: _completed(1, stderr="database is locked"),
    )
    with pytest.raises(osxphotos_runner.OsxphotosError):
        osxphotos_runner.count_assets(scope="selected")
