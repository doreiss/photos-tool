from __future__ import annotations

from pathlib import Path

import pytest

from photos_tool.reconcile import Status
from photos_tool.report import (
    ReportError,
    ReportSummary,
    missing_expected_columns,
    parse_report,
    sanitize_report,
    summarize,
    summarize_rows,
    unexpected_columns,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_json_report_counts_uuid_assets():
    report = parse_report(FIXTURES / "report_ok.json")

    assert report.total_files == 3
    assert report.exported == 2
    assert report.new == 2
    assert report.skipped == 1
    assert report.missing == 0
    assert report.error == 0
    assert report.exported_uuids == frozenset({"asset-1", "asset-2", "asset-3"})

    reconciliation = summarize(report, selected_assets=3)
    assert reconciliation.ok
    assert reconciliation.status is Status.OK


def test_missing_json_report_flags_icloud_skip():
    report = parse_report(FIXTURES / "report_missing.json")
    reconciliation = summarize(report, selected_assets=2)

    assert report.missing == 1
    assert not reconciliation.ok
    assert reconciliation.status is Status.SKIPPED
    assert "Optimize Mac Storage" in reconciliation.message


def test_uuidless_report_refuses_to_reconcile():
    # A report with no uuid column cannot be safely reconciled; we fail loudly rather
    # than fall back to a looser count-based model that could mask silent loss.
    report = parse_report(FIXTURES / "report.csv")

    assert report.total_files == 2
    assert report.exported == 1
    assert report.skipped == 1
    assert report.exported_uuids is None

    with pytest.raises(ReportError, match="cannot safely reconcile"):
        summarize(report, selected_assets=2)


def test_exiftool_error_does_not_count_as_asset_loss(tmp_path: Path):
    path = tmp_path / "report.json"
    path.write_text(
        """
[
  {
    "uuid": "asset-1",
    "filename": "/Volumes/FamilyPhotos/IMG_0001.HEIC",
    "exported": true,
    "new": true,
    "updated": false,
    "skipped": false,
    "missing": false,
    "error": false,
    "exiftool_error": true
  }
]
""",
        encoding="utf-8",
    )

    report = parse_report(path)
    reconciliation = summarize(report, selected_assets=1)

    assert report.error == 0
    assert report.exiftool_error == 1
    assert reconciliation.ok


def test_uuidless_report_with_unaccounted_rows_still_refuses(tmp_path: Path):
    path = tmp_path / "report.csv"
    path.write_text(
        "filename,exported,skipped,missing,error\n"
        "/Volumes/FamilyPhotos/IMG_0001.HEIC,False,False,False,False\n",
        encoding="utf-8",
    )

    report = parse_report(path)

    with pytest.raises(ReportError, match="cannot safely reconcile"):
        summarize(report, selected_assets=1)


def test_string_zero_float_is_false(tmp_path: Path):
    path = tmp_path / "report.csv"
    path.write_text(
        "filename,exported,skipped,missing,error\n"
        "/Volumes/FamilyPhotos/IMG_0001.HEIC,1,0.0,0.0,0.0\n",
        encoding="utf-8",
    )

    report = parse_report(path)

    assert report.skipped == 0
    assert report.missing == 0
    assert report.error == 0


def test_bare_dict_json_report_is_rejected(tmp_path: Path):
    path = tmp_path / "report.json"
    path.write_text('{"filename": "/x", "exported": true}', encoding="utf-8")

    try:
        parse_report(path)
    except ValueError as exc:
        assert "row list" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ReportError")


def test_report_shape_helpers_detect_missing_and_extra_columns():
    report = parse_report(FIXTURES / "report_ok.json")

    assert "uuid" not in missing_expected_columns(report)
    assert "filename" not in missing_expected_columns(report)
    assert unexpected_columns(report) == frozenset()


def test_report_shape_allows_known_osxphotos_column_prefixes():
    report = ReportSummary(
        total_files=1,
        exported=1,
        new=1,
        updated=0,
        skipped=0,
        converted=0,
        missing=0,
        error=0,
        columns=frozenset(
            {"filename", "exported", "missing", "error", "sidecar_json", "aae_rendered"}
        ),
    )

    assert unexpected_columns(report) == frozenset()


def test_sanitize_json_report_removes_paths_and_raw_uuids(tmp_path: Path):
    source = tmp_path / "raw.json"
    target = tmp_path / "sanitized.json"
    source.write_text(
        """
[
  {
    "uuid": "E7A88C5B-1234-4321-9876-ABCDEF123456",
    "filename": "/Users/example/Pictures/Photos Library.photoslibrary/originals/IMG_0001.HEIC",
    "original_filename": "Private Birthday Name.HEIC",
    "GPSLatitude": 61.2181,
    "GPSLongitude": -149.9003,
    "exported": true,
    "missing": false
  }
]
""",
        encoding="utf-8",
    )

    sanitize_report(source, target)
    text = target.read_text(encoding="utf-8")

    assert "E7A88C5B" not in text
    assert "/Users/example" not in text
    assert "IMG_0001" not in text
    assert "Private Birthday Name" not in text
    assert "61.2181" not in text
    assert "149.9003" not in text
    assert "uuid-" in text
    assert "/sanitized/file-" in text
    assert ".HEIC" in text
    assert "<redacted>" in text


def test_sanitize_csv_report_removes_paths_and_raw_uuids(tmp_path: Path):
    source = tmp_path / "raw.csv"
    target = tmp_path / "sanitized.csv"
    source.write_text(
        "uuid,filename,GPSLatitude,exported,missing\n"
        "real-uuid,/Users/example/Pictures/IMG_0001.HEIC,61.2181,True,False\n",
        encoding="utf-8",
    )

    sanitize_report(source, target)
    text = target.read_text(encoding="utf-8")

    assert "real-uuid" not in text
    assert "/Users/example" not in text
    assert "IMG_0001" not in text
    assert "61.2181" not in text
    assert "/sanitized/file-" in text
    assert ".HEIC" in text
    assert "<redacted>" in text


def test_authoritative_real_report_parses_without_warnings():
    # Captured from a real osxphotos 0.76.1 export (5 files: 2 stills, 1 video, and a
    # Live Photo whose .heic + .mov share one uuid -> 4 distinct assets).
    report = parse_report(FIXTURES / "report_real_sanitized.json")

    assert report.total_files == 5
    assert report.exported == 5
    assert report.exported_uuids is not None
    assert len(report.exported_uuids) == 4
    # No spurious column warnings against the real 0.76.1 schema.
    assert unexpected_columns(report) == frozenset()
    assert missing_expected_columns(report) == frozenset()

    reconciliation = summarize(report, selected_assets=4)
    assert reconciliation.ok
    assert reconciliation.status is Status.OK


def test_touched_only_row_contributes_no_exported_path():
    # A row osxphotos "touched" (metadata/date only) but did NOT export and did NOT skip must
    # contribute no path: its original's bytes were not written this run, so it must never become
    # removable. Guards report.py's `(row_exported or row_skipped)` gate against being loosened
    # back to "not missing and not error" (which would authorize deleting an un-written original).
    rows = [
        {
            "uuid": "ABC-123",
            "filename": "IMG_0001.HEIC",
            "exported": "",  # not exported
            "skipped": "",  # not already-present
            "missing": "",
            "error": "",
        }
    ]
    summary = summarize_rows(rows)
    # uuid present, but nothing positively on the share => no removable path for this asset.
    assert summary.exported_paths == {}
    assert "ABC-123" not in (summary.exported_uuids or frozenset())
