"""Subprocess boundary for osxphotos.

All osxphotos calls stay here (and in the pure command builder). The package does
not import osxphotos as a Python API, which keeps startup light and makes the
orchestration testable with fake executables on PATH.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from .plan import ExportOptions, build_export_command
from .report import parse_report


class OsxphotosError(RuntimeError):
    """Raised when osxphotos cannot complete a command."""


@dataclass(frozen=True)
class ExportResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    report_path: Path

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def count_selected(timeout: float = 60) -> int:
    return count_assets(scope="selected", timeout=timeout)


def count_assets(scope: str = "selected", album: str | None = None, timeout: float = 60) -> int:
    cmd = ["osxphotos", "query"]
    if scope == "selected":
        cmd.append("--selected")
    elif scope == "album" and album:
        cmd += ["--album", album]
    else:
        raise ValueError("scope must be 'selected' or an album name must be provided")
    cmd.append("--count")

    result = _run(cmd, timeout=timeout)
    if result.returncode != 0:
        # osxphotos exits non-zero with a help message (no count) when the live
        # selection is empty; treat that as zero rather than a hard error.
        combined = f"{result.stdout}\n{result.stderr}".lower()
        if scope == "selected" and "no photos selected" in combined:
            return 0
        _raise_osxphotos(cmd, result)
    text = (result.stdout or result.stderr).strip().splitlines()
    for line in reversed(text):
        line = line.strip()
        if line:
            try:
                return int(line)
            except ValueError as exc:
                raise OsxphotosError(f"osxphotos returned a non-integer count: {line!r}") from exc
    raise OsxphotosError("osxphotos returned no count")


def run_export(
    opts: ExportOptions,
    report_path: Path,
    *,
    dry_run: bool = False,
    extra: list[str] | None = None,
    timeout: float | None = None,
) -> ExportResult:
    cmd = build_export_command(opts)
    cmd += ["--report", str(report_path)]
    if dry_run:
        cmd.append("--dry-run")
    if extra:
        cmd.extend(extra)
    if "--cleanup" in cmd:
        raise ValueError("photos-tool must never pass osxphotos --cleanup")

    # osxphotos refuses a destination that does not yet exist (even on --dry-run);
    # create it first so the compat/ subtree and per-Mac subpaths just work.
    try:
        Path(opts.destination).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OsxphotosError(f"could not create destination {opts.destination}: {exc}") from exc

    result = _run(cmd, timeout=timeout)
    return ExportResult(
        command=tuple(cmd),
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        report_path=report_path,
    )


def probe_optimize_storage_risk(opts: ExportOptions) -> float:
    with tempfile.TemporaryDirectory(prefix="photos-tool-dry-run-") as tmp:
        report_path = Path(tmp) / "report.json"
        result = run_export(opts, report_path, dry_run=True)
        if not result.ok:
            raise OsxphotosError(_format_failure(result.command, result))
        summary = parse_report(report_path)
    return summary.issue_count / summary.total_files if summary.total_files else 0.0


def is_authorization_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(
        needle in text
        for needle in (
            "not authorized",
            "operation not permitted",
            "full disk access",
            "permission denied",
        )
    )


def _run(cmd: list[str], timeout: float | None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise OsxphotosError(f"{cmd[0]} was not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise OsxphotosError(f"{cmd[0]} timed out after {timeout} seconds") from exc


def _raise_osxphotos(cmd: list[str], result: subprocess.CompletedProcess[str]) -> NoReturn:
    raise OsxphotosError(_format_failure(tuple(cmd), result))


def _format_failure(command: tuple[str, ...], result: Any) -> str:
    detail = (result.stderr or result.stdout or "").strip()
    suffix = f": {detail}" if detail else ""
    return f"{' '.join(command)} failed with exit {result.returncode}{suffix}"
