"""Parse osxphotos export reports and reconcile them against selected assets."""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .reconcile import Reconciliation, reconcile


class ReportError(ValueError):
    """Raised when an export report cannot be parsed."""


EXPECTED_REPORT_COLUMNS = {
    "aae_rendered",
    "aae_sidecar",
    "aae_updated",
    "datetime",
    "description_sidecar",
    "downloaded",
    "error",
    "exif_updated",
    "exiftool_error",
    "exiftool_warning",
    "exported",
    "external_edit",
    "filename",
    "fingerprint",
    "info_sidecar",
    "jpeg_ext",
    "jpeg_path",
    "json_sidecar",
    "keyword",
    "live_photo",
    "missing",
    "new",
    "original",
    "original_name",
    "original_path",
    "photo",
    "portrait",
    "preview",
    "preview_path",
    "sidecar_error",
    "sidecar_json",
    "sidecar_xmp",
    "skipped",
    "touch_file_error",
    "touched",
    "updated",
    "use_photokit",
    "user",
    "uuid",
    "converted_to_jpeg",
    "xmp_sidecar",
    "cleanup_deleted_directory",
    "cleanup_deleted_file",
    "exported_album",
    "extended_attributes_skipped",
    "extended_attributes_written",
}

REQUIRED_REPORT_COLUMNS = {
    "filename",
    "exported",
    "missing",
    "error",
}

EXPECTED_REPORT_PREFIXES = (
    "aae_",
    "sidecar_",
    "user_",
    "cleanup_",
    "extended_attributes_",
)


@dataclass(frozen=True)
class ReportSummary:
    total_files: int
    exported: int
    new: int
    updated: int
    skipped: int
    converted: int
    missing: int
    error: int
    exiftool_error: int = 0
    exiftool_warning: int = 0
    exported_uuids: frozenset[str] | None = None
    columns: frozenset[str] = frozenset()

    @property
    def issue_count(self) -> int:
        return self.missing + self.error


def parse_report(path: Path) -> ReportSummary:
    suffix = path.suffix.lower()
    if suffix == ".json":
        rows = _load_json_rows(path)
    elif suffix == ".csv":
        rows = _load_csv_rows(path)
    else:
        raise ReportError(f"unsupported report format for {path}; use .json or .csv")
    return summarize_rows(rows)


def sanitize_report(source: Path, target: Path) -> None:
    """Write a privacy-preserving copy of an osxphotos JSON or CSV report."""
    suffix = source.suffix.lower()
    if suffix == ".json":
        raw = _load_json_value(source)
        sanitized = _sanitize_value(raw)
        with target.open("w", encoding="utf-8") as fh:
            json.dump(sanitized, fh, indent=2, sort_keys=True)
            fh.write("\n")
        return
    if suffix == ".csv":
        rows = _load_csv_rows(source)
        fieldnames = list(rows[0].keys()) if rows else []
        with target.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(_sanitize_row(row))
        return
    raise ReportError(f"unsupported report format for {source}; use .json or .csv")


def summarize_rows(rows: Iterable[dict[str, Any]]) -> ReportSummary:
    row_list = list(rows)
    columns: set[str] = set()
    exported_uuids: set[str] = set()
    uuid_present = False
    exported = new = updated = skipped = converted = missing = error = 0
    exiftool_error = exiftool_warning = 0

    for row in row_list:
        columns.update(str(key) for key in row)
        row_missing = _truthy(row.get("missing"))
        row_error = _truthy(row.get("error"))
        exported += int(_truthy(row.get("exported")))
        new += int(_truthy(row.get("new")))
        updated += int(_truthy(row.get("updated")))
        skipped += int(_truthy(row.get("skipped")))
        converted += int(_truthy(row.get("converted_to_jpeg")))
        missing += int(row_missing)
        error += int(row_error)
        exiftool_error += int(_truthy(row.get("exiftool_error")))
        exiftool_warning += int(_truthy(row.get("exiftool_warning")))

        uuid = row.get("uuid")
        if uuid not in (None, ""):
            uuid_present = True
            if not row_missing and not row_error:
                exported_uuids.add(str(uuid))

    return ReportSummary(
        total_files=len(row_list),
        exported=exported,
        new=new,
        updated=updated,
        skipped=skipped,
        converted=converted,
        missing=missing,
        error=error,
        exiftool_error=exiftool_error,
        exiftool_warning=exiftool_warning,
        exported_uuids=frozenset(exported_uuids) if uuid_present else None,
        columns=frozenset(columns),
    )


def summarize(report: ReportSummary, selected_assets: int) -> Reconciliation:
    missing = report.missing + report.error
    if report.exported_uuids is not None:
        exported = len(report.exported_uuids)
    else:
        exported = min(selected_assets, report.exported + report.skipped)
    return reconcile(selected=selected_assets, exported=exported, missing=missing)


def unexpected_columns(summary: ReportSummary) -> frozenset[str]:
    return frozenset(
        column
        for column in summary.columns - EXPECTED_REPORT_COLUMNS
        if not column.startswith(EXPECTED_REPORT_PREFIXES)
    )


def missing_expected_columns(summary: ReportSummary) -> frozenset[str]:
    return frozenset(REQUIRED_REPORT_COLUMNS - summary.columns)


def _load_json_rows(path: Path) -> list[dict[str, Any]]:
    try:
        raw = _load_json_value(path)
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportError(f"could not parse JSON report {path}: {exc}") from exc
    rows = list(_json_rows(raw))
    if not all(isinstance(row, dict) for row in rows):
        raise ReportError(f"JSON report {path} did not contain row objects")
    return rows


def _load_json_value(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _json_rows(raw: Any) -> Iterable[dict[str, Any]]:
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                yield item
        return
    if isinstance(raw, dict):
        for key in ("files", "photos", "results", "report"):
            value = raw.get(key)
            if isinstance(value, list):
                yield from _json_rows(value)
                return
        # Some osxphotos JSON reports are a UUID/path keyed mapping.
        if all(isinstance(value, dict) for value in raw.values()):
            for value in raw.values():
                yield value
            return
        raise ReportError("JSON report did not contain an osxphotos row list")


def _load_csv_rows(path: Path) -> list[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))
    except OSError as exc:
        raise ReportError(f"could not parse CSV report {path}: {exc}") from exc


def _truthy(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value != 0
    text = str(value).strip()
    if text == "":
        return False
    try:
        return float(text) != 0
    except ValueError:
        pass
    return text.lower() not in {"0", "false", "no", "none", "null"}


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _sanitize_row(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    return value


def _sanitize_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _sanitize_field(str(key), value) for key, value in row.items()}


def _sanitize_field(key: str, value: Any) -> Any:
    lowered = key.lower()
    if value in (None, ""):
        return value
    if lowered in {"gps", "latitude", "longitude"} or "gps" in lowered:
        return "<redacted>"
    if "latitude" in lowered or "longitude" in lowered:
        return "<redacted>"
    if lowered in {"uuid", "photo_uuid", "asset_uuid"}:
        return f"uuid-{_short_hash(str(value))}"
    if "path" in lowered or lowered.endswith("filename"):
        return _sanitize_path(str(value))
    return _sanitize_value(value)


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _sanitize_path(value: str) -> str:
    suffix = Path(value).suffix
    return f"/sanitized/file-{_short_hash(value)}{suffix}"
