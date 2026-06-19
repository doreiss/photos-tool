from __future__ import annotations

from pathlib import Path

import pytest

from photos_tool import osxphotos_runner
from photos_tool.plan import ExportOptions


def test_run_export_rejects_cleanup_flag(tmp_path: Path):
    with pytest.raises(ValueError, match="--cleanup"):
        osxphotos_runner.run_export(
            ExportOptions(destination="/Volumes/FamilyPhotos"),
            tmp_path / "report.json",
            extra=["--cleanup"],
        )
