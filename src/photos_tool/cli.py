"""Command-line interface for photos-tool."""

from __future__ import annotations

import argparse
import fcntl
import json
import re
import shlex
import shutil
import socket
import sys
import tempfile
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from . import __version__, state
from .config import (
    Config,
    ConfigError,
    default_config_path,
    load_config,
    resolved_exportdb_path,
    validate_smb_url,
)
from .convert import ConversionError, ConvertSummary, convert_videos
from .osxphotos_runner import (
    ExportResult,
    OsxphotosError,
    count_assets,
    is_authorization_error,
    probe_optimize_storage_risk,
    run_export,
)
from .plan import ExportOptions, build_export_command
from .remove import RemoveError, remove_originals
from .report import (
    ReportError,
    ReportSummary,
    missing_expected_columns,
    parse_report,
    sanitize_report,
    summarize,
    unexpected_columns,
)
from .smb import SmbError, ensure_mounted, is_writable
from .tooling import ToolStatus, missing_required, probe_all

EXIT_OK = 0
EXIT_PREFLIGHT = 1
EXIT_USAGE = 2
EXIT_RECONCILE = 3
EXIT_NOTHING_SELECTED = 4
EXIT_CONVERSION = 5


def _cmd_check(_args: argparse.Namespace) -> int:
    statuses = probe_all()
    _print_tool_statuses(statuses)

    missing = missing_required(statuses)
    if missing:
        _print_missing_tools(missing)
        return EXIT_PREFLIGHT
    print("\nAll required tools present.")
    return EXIT_OK


def _cmd_plan(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    destination = Path(args.destination).expanduser()
    opts = ExportOptions(
        destination=str(destination),
        scope="album" if args.album else "selected",
        album=args.album,
        exportdb=str(
            resolved_exportdb_path(destination, Path(config.state.exportdb_dir).expanduser())
        ),
        download_missing=config.export.download_missing,
        use_photokit=config.export.use_photokit,
        retry=config.export.retry,
        convert_to_jpeg=args.jpeg,
        jpeg_quality=args.jpeg_quality,
        directory_template=args.directory,
        filename_template=args.filename,
    )
    try:
        cmd = build_export_command(opts)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print(shlex.join(cmd))
    return EXIT_OK


def _cmd_send(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.last_report:
        return _print_last_report(config)

    started = time.monotonic()
    try:
        destination = config.destination_path()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    jpeg = config.copies.jpeg
    mp4 = config.copies.mp4
    use_photokit = config.export.use_photokit
    if use_photokit:
        print(
            "Warning: --use-photokit is an alpha osxphotos path and is expected to work "
            "only when launched from Terminal.app.",
            file=sys.stderr,
        )
    scope = "album" if args.album else "selected"
    album = args.album

    if config.destination.smb_url and not config.destination.subpath:
        print(
            "Warning: no per-Mac subpath is set (destination.subpath is empty). If other Macs "
            "back up to this share, photos that share a name like IMG_0001 can overwrite each "
            "other. Run 'photos-tool init' to set a per-Mac subfolder.",
            file=sys.stderr,
        )

    statuses = probe_all()
    missing = missing_required(statuses)
    if mp4:
        missing.extend(
            status
            for status in statuses
            if status.tool.name in {"ffmpeg", "ffprobe"} and not status.found
        )
    if missing:
        _print_missing_tools(missing)
        return EXIT_PREFLIGHT

    mount_error = _ensure_destination_ready(config, destination)
    if mount_error:
        print(f"preflight error: {mount_error}", file=sys.stderr)
        return EXIT_PREFLIGHT

    try:
        selected = count_assets(scope=scope, album=album)
    except (OsxphotosError, ValueError) as exc:
        if is_authorization_error(exc):
            print(
                "preflight error: Photos is not readable. Grant Full Disk Access to the app "
                "launching photos-tool, then quit and relaunch it.",
                file=sys.stderr,
            )
        else:
            print(f"preflight error: {exc}", file=sys.stderr)
        return EXIT_PREFLIGHT

    if selected == 0:
        if scope == "album":
            print(
                f"No photos matched album {album!r}. Album names are case-sensitive — "
                "check the spelling, or that the album actually contains photos."
            )
        else:
            print("Nothing selected. Select photos in Photos first, then run photos-tool send.")
        return EXIT_NOTHING_SELECTED

    log_dir = Path(config.state.log_dir).expanduser()
    exportdb_dir = Path(config.state.exportdb_dir).expanduser()
    exportdb_dir.mkdir(parents=True, exist_ok=True)
    opts = _export_options(config, destination, exportdb_dir, scope, album, use_photokit)

    if args.dry_run:
        return _send_dry_run(opts, selected)

    # Guard against a double-pressed hotkey launching two overlapping exports on THIS
    # Mac (the lock file lives in the local state dir, so it serializes same-Mac runs
    # only; per-Mac subpaths keep different Macs out of each other's trees). The handle
    # is held for the rest of this run; the OS releases the flock on exit/close.
    lock = _acquire_destination_lock(exportdb_dir, destination)
    if lock is None:
        print(
            "Another photos-tool send is already running for this destination; "
            "wait for it to finish and retry.",
            file=sys.stderr,
        )
        return EXIT_PREFLIGHT

    log_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="photos-tool-report-") as tmp:
        tmpdir = Path(tmp)
        original_report = tmpdir / "original.json"
        result = run_export(opts, original_report)
        if args.verbose:
            _print_captured(result)
        if not result.ok:
            print(f"export failed with exit {result.returncode}", file=sys.stderr)
            _write_run_log(
                log_dir,
                started=started,
                exit_code=EXIT_PREFLIGHT,
                scope=scope,
                selected=selected,
                exported=0,
                missing=0,
                error=0,
            )
            return EXIT_PREFLIGHT

        try:
            report = parse_report(original_report)
            reconciliation = summarize(report, selected)
        except ReportError as exc:
            print(f"report error: {exc}", file=sys.stderr)
            _write_run_log(
                log_dir,
                started=started,
                exit_code=EXIT_PREFLIGHT,
                scope=scope,
                selected=selected,
                exported=0,
                missing=0,
                error=0,
            )
            return EXIT_PREFLIGHT

        # Fail CLOSED if a REQUIRED column is absent (e.g. osxphotos renamed/removed it):
        # a missing 'missing'/'error'/'exported' column would otherwise read as False and
        # silently count an unexported asset as backed up — never record a token from that.
        shape_gap = missing_expected_columns(report)
        if shape_gap:
            print(
                "report error: the osxphotos report is missing required column(s) "
                f"({', '.join(sorted(shape_gap))}); refusing to reconcile or record a backup "
                "(its format may have changed — originals are NOT safe to delete).",
                file=sys.stderr,
            )
            _write_run_log(
                log_dir,
                started=started,
                exit_code=EXIT_PREFLIGHT,
                scope=scope,
                selected=selected,
                exported=0,
                missing=0,
                error=0,
            )
            return EXIT_PREFLIGHT

        _print_summary(destination, selected, report, reconciliation.message)
        _warn_report_shape(report)

        jpeg_report: ReportSummary | None = None
        if jpeg:
            try:
                jpeg_report = _run_jpeg_pass(
                    config=config,
                    destination=destination,
                    exportdb_dir=exportdb_dir,
                    scope=scope,
                    album=album,
                    use_photokit=use_photokit,
                    report_dir=tmpdir,
                    verbose=args.verbose,
                )
            except (OsxphotosError, ReportError) as exc:
                print(f"JPEG compatibility export error: {exc}", file=sys.stderr)
                _write_run_log(
                    log_dir,
                    started=started,
                    exit_code=EXIT_CONVERSION,
                    scope=scope,
                    selected=selected,
                    exported=reconciliation.exported,
                    missing=report.missing,
                    error=report.error,
                )
                return EXIT_CONVERSION
            # The compat pass exports photos only (--only-photos), so its count is
            # legitimately lower than the selection; do not treat that as loss. The
            # originals pass above is the safety-critical reconciliation. Still surface
            # any real missing/errored compat rows as a non-fatal warning.
            if jpeg_report.issue_count:
                print(
                    f"Warning: {jpeg_report.issue_count} compatibility-copy row(s) were "
                    "missing or errored; your originals export is unaffected.",
                    file=sys.stderr,
                )

        convert_summary = ConvertSummary()
        if mp4:
            try:
                convert_summary = convert_videos(
                    destination,
                    destination / "compat",
                    crf=config.copies.mp4_crf,
                )
                print(
                    "MP4 copies into compat/: "
                    f"{convert_summary.transcoded} transcoded, "
                    f"{convert_summary.skipped} skipped (Live Photo motion, "
                    "already current, or non-HEVC)."
                )
            except ConversionError as exc:
                print(f"conversion error: {exc}", file=sys.stderr)
                _write_run_log(
                    log_dir,
                    started=started,
                    exit_code=EXIT_CONVERSION,
                    scope=scope,
                    selected=selected,
                    exported=reconciliation.exported,
                    missing=report.missing,
                    error=report.error,
                    converted=jpeg_report.converted if jpeg_report else 0,
                    mp4=convert_summary.transcoded,
                )
                return EXIT_CONVERSION

        exit_code = EXIT_OK if reconciliation.ok else EXIT_RECONCILE
        _write_run_log(
            log_dir,
            started=started,
            exit_code=exit_code,
            scope=scope,
            selected=selected,
            exported=reconciliation.exported,
            missing=report.missing,
            error=report.error,
            converted=jpeg_report.converted if jpeg_report else 0,
            mp4=convert_summary.transcoded,
            exiftool_error=report.exiftool_error,
            exiftool_warning=report.exiftool_warning,
        )

        # Record this batch (content-fingerprinting each landed copy) so cleanup-last can
        # later remove exactly these originals — never automatically, always a separate step.
        if exit_code == EXIT_OK and report.exported_paths:
            skipped = state.save_backup_token(
                log_dir, destination, config.destination.smb_url, report.exported_paths
            )
            if skipped:
                print(
                    f"Note: {len(skipped)} photo(s) could not be fully verified on the share and "
                    "will NOT be offered for cleanup; re-run send once the share is healthy.",
                    file=sys.stderr,
                )

        return exit_code


def _cmd_doctor(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
        destination = config.destination_path(args.destination)
    except ConfigError as exc:
        print(f"[fail] config: {exc}")
        return EXIT_PREFLIGHT

    ok = True
    statuses = probe_all()
    for status in statuses:
        label = "pass" if status.found or not status.tool.required else "fail"
        if label == "fail":
            ok = False
        detail = status.version or status.path or status.tool.purpose
        print(f"[{label}] tool {status.tool.name}: {detail}")

    mount_error = _ensure_destination_ready(config, destination)
    if mount_error:
        ok = False
        print(f"[fail] destination: {mount_error}")
    else:
        print(f"[pass] destination writable: {destination}")

    selected = 0
    try:
        selected = count_assets()
        print(f"[pass] Photos selection readable: {selected} selected")
    except OsxphotosError as exc:
        ok = False
        if is_authorization_error(exc):
            print("[fail] Photos readable: grant Full Disk Access to the launching app")
        else:
            print(f"[fail] Photos readable: {exc}")

    # The dry-run heuristic only means something when real photos are selected;
    # an empty selection would always read 0% and falsely reassure.
    if selected:
        try:
            exportdb_dir = Path(config.state.exportdb_dir).expanduser()
            exportdb_dir.mkdir(parents=True, exist_ok=True)
            opts = _export_options(
                config,
                destination,
                exportdb_dir,
                "selected",
                None,
                config.export.use_photokit,
            )
            risk = probe_optimize_storage_risk(opts)
            label = "warn" if risk >= 0.25 else "pass"
            print(f"[{label}] Optimize Storage dry-run risk: {risk:.0%} missing/error rows")
        except (OsxphotosError, ReportError, ValueError) as exc:
            ok = False
            print(f"[fail] Optimize Storage dry run: {exc}")
    else:
        print(
            "[info] Optimize Storage risk: select photos in Photos and rerun doctor to gauge "
            "how many originals are cloud-only."
        )

    print("[info] Windows: use authenticated SMB, and install HEIF/HEVC codecs or enable copies.")
    print("[info] macOS 26: Shared Albums may not be readable by osxphotos yet.")
    return EXIT_OK if ok else EXIT_PREFLIGHT


def _cmd_connect(args: argparse.Namespace) -> int:
    """Connect to the SMB share (triggering macOS's native auth) and write the config.

    The no-Terminal onboarding entry point the menu-bar app's "Set up connection…" calls.
    The password is entered into macOS's OWN mount dialog and saved to Keychain — never
    seen, passed in argv, or stored by photos-tool. Config is written only AFTER the share
    is proven reachable and writable, so a bad address never leaves a broken config behind.
    """
    config_path = Path(args.config).expanduser() if args.config else default_config_path()
    smb_url = (args.smb_url or "").strip()
    try:
        validate_smb_url(smb_url)
    except ConfigError as exc:
        return _connect_fail(args, EXIT_USAGE, f"invalid share address: {exc}")

    mount_point = (args.mount_point or _default_mount_point(smb_url)).strip()
    if not mount_point:
        return _connect_fail(
            args,
            EXIT_USAGE,
            "could not work out where the share mounts; pass --mount-point "
            "(for example /Volumes/FamilyPhotos)",
        )
    subpath = args.subpath if args.subpath is not None else _default_subpath()

    if config_path.exists() and not args.force:
        return _connect_fail(
            args, EXIT_USAGE, f"{config_path} already exists; pass --force to reconnect"
        )

    # Trigger the native mount + Keychain auth and prove the share is reachable + writable
    # BEFORE writing any config.
    try:
        ensure_mounted(smb_url, Path(mount_point).expanduser())
    except SmbError as exc:
        return _connect_fail(args, EXIT_PREFLIGHT, str(exc))

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        _render_config(
            smb_url=smb_url,
            mount_point=mount_point,
            subpath=subpath,
            jpeg=args.jpeg,
            mp4=args.mp4,
        ),
        encoding="utf-8",
    )
    destination = str(Path(mount_point).expanduser() / subpath) if subpath else mount_point
    if args.json:
        print(
            json.dumps(
                {
                    "ok": True,
                    "mount_point": mount_point,
                    "subpath": subpath,
                    "destination": destination,
                }
            )
        )
    else:
        print(f"Connected. Backups from this Mac will go to {destination}.")
        print(
            "One-time macOS grants the app needs: Full Disk Access (read the library), "
            "Automation -> Photos (read your selection), and Photos (the recoverable cleanup "
            "delete). The menu-bar app prompts for the last two on first use."
        )
    return EXIT_OK


def _connect_fail(args: argparse.Namespace, code: int, message: str) -> int:
    if getattr(args, "json", False):
        print(json.dumps({"ok": False, "error": message}))
    else:
        print(f"could not connect: {message}", file=sys.stderr)
    return code


def _cmd_init(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser() if args.config else default_config_path()
    if args.non_interactive:
        if not args.smb_url or not args.mount_point:
            print("error: --non-interactive requires --smb-url and --mount-point", file=sys.stderr)
            return EXIT_USAGE
        smb_url = args.smb_url
        mount_point = args.mount_point
        subpath = args.subpath if args.subpath is not None else _default_subpath()
    else:
        smb_url = input("SMB URL (for example smb://192.168.1.50/FamilyPhotos): ").strip()
        default_mount = _default_mount_point(smb_url)
        mount_prompt = (
            f"Mount point [{default_mount}]: "
            if default_mount
            else "Mount point (for example /Volumes/FamilyPhotos): "
        )
        mount_point = input(mount_prompt).strip() or default_mount
        default_subpath = _default_subpath()
        entered = input(
            f"Subfolder for THIS Mac (keeps each Mac's photos separate) [{default_subpath}]: "
        ).strip()
        subpath = entered or default_subpath

    if not mount_point:
        print(
            "error: a mount point is required (for example /Volumes/FamilyPhotos) — it is where "
            "the share appears once mounted.",
            file=sys.stderr,
        )
        return EXIT_USAGE

    if smb_url:
        try:
            validate_smb_url(smb_url)
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_USAGE

    text = _render_config(
        smb_url=smb_url,
        mount_point=mount_point,
        subpath=subpath,
        jpeg=args.jpeg,
        mp4=args.mp4,
    )
    if config_path.exists() and not args.force:
        print(f"error: {config_path} already exists; pass --force to overwrite", file=sys.stderr)
        return EXIT_USAGE
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(text, encoding="utf-8")
    print(f"Wrote {config_path}")
    print("Next: connect to the SMB share once in Finder and save the password in Keychain")
    print("(or use the menu-bar app's 'Set up connection…', which does this for you).")
    print("Grant the app Full Disk Access; it prompts for Automation->Photos and Photos on use.")
    return EXIT_OK


def _cmd_install_shortcut(args: argparse.Namespace) -> int:
    script_path = (
        Path(args.script).expanduser()
        if args.script
        else default_config_path().parent / "send-selected.sh"
    )
    config_path = Path(args.config).expanduser() if args.config else None
    if script_path.exists() and not args.force:
        print(f"error: {script_path} already exists; pass --force to overwrite", file=sys.stderr)
        return EXIT_USAGE

    executable = _photos_tool_executable()
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(_render_shortcut_script(executable, config_path), encoding="utf-8")
    script_path.chmod(0o755)

    print(f"Wrote {script_path}")
    print("Create a macOS Shortcut with one 'Run Shell Script' action:")
    print(str(script_path))
    print("Then assign that Shortcut a keyboard shortcut and grant it Full Disk Access.")
    return EXIT_OK


def _cmd_sanitize_report(args: argparse.Namespace) -> int:
    source = Path(args.source).expanduser()
    target = Path(args.target).expanduser()
    if not source.exists():
        print(f"error: {source} does not exist", file=sys.stderr)
        return EXIT_USAGE
    if target.exists() and not args.force:
        print(f"error: {target} already exists; pass --force to overwrite", file=sys.stderr)
        return EXIT_USAGE
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        sanitize_report(source, target)
    except (OSError, ReportError, json.JSONDecodeError) as exc:
        print(f"error: could not sanitize report: {exc}", file=sys.stderr)
        return EXIT_USAGE
    print(f"Wrote sanitized report to {target}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="photos-tool",
        description="Push selected Apple Photos to a Windows PC on the LAN, metadata intact.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_check = sub.add_parser("check", help="verify the required external tools are installed")
    p_check.set_defaults(func=_cmd_check)

    p_plan = sub.add_parser(
        "plan",
        help="print the exact osxphotos export command for the given options (runs nothing)",
    )
    p_plan.add_argument("destination", help="export destination, e.g. a mounted SMB share path")
    p_plan.add_argument("--config", help="config TOML path")
    p_plan.add_argument("--album", help="export a named album instead of the live selection")
    p_plan.add_argument("--jpeg", action="store_true", help="also write HEIC->JPEG copies")
    p_plan.add_argument("--jpeg-quality", type=float, default=0.9)
    p_plan.add_argument("--directory", default="{created.year}/{created.mm}")
    p_plan.add_argument("--filename", default="{original_name}")
    p_plan.set_defaults(func=_cmd_plan)

    p_send = sub.add_parser("send", help="export selected Photos items to the destination")
    # No positional destination: send is config-only so the recorded backup token is always
    # keyed to the destination cleanup-last queries (an ad-hoc override would strand the token).
    _add_runtime_options(p_send)
    p_send.add_argument("--dry-run", action="store_true", help="simulate export and parse report")
    p_send.add_argument("--verbose", action="store_true", help="print captured osxphotos output")
    p_send.add_argument("--last-report", action="store_true", help="print the last run summary")
    p_send.set_defaults(func=_cmd_send)

    p_cleanup = sub.add_parser(
        "cleanup-last",
        help="move the last backup's originals to Recently Deleted (re-verified on the share)",
    )
    p_cleanup.add_argument("--config", help="config TOML path")
    p_cleanup.add_argument(
        "--dry-run", action="store_true", help="report what would be removed; delete nothing"
    )
    p_cleanup.add_argument("--yes", action="store_true", help="skip the interactive confirmation")
    p_cleanup.add_argument(
        "--json", action="store_true", help="print {count, destination} for the GUI and exit"
    )
    p_cleanup.set_defaults(func=_cmd_cleanup_last)

    p_doctor = sub.add_parser("doctor", help="diagnose tools, permissions, share, and iCloud risk")
    p_doctor.add_argument("destination", nargs="?", help="destination override")
    p_doctor.add_argument("--config", help="config TOML path")
    p_doctor.set_defaults(func=_cmd_doctor)

    p_connect = sub.add_parser(
        "connect",
        help="connect to the SMB share (native macOS auth) and write the config — no manual setup",
    )
    p_connect.add_argument("--smb-url", required=True, help="smb://host/Share")
    p_connect.add_argument(
        "--mount-point", help="override the mount point (default: /Volumes/<Share>)"
    )
    p_connect.add_argument(
        "--subpath", default=None, help="per-Mac subfolder (default: this Mac's name)"
    )
    p_connect.add_argument("--config", help="config TOML path")
    p_connect.add_argument(
        "--jpeg", action="store_true", help="default to JPEG compatibility copies"
    )
    p_connect.add_argument("--mp4", action="store_true", help="default to MP4 compatibility copies")
    p_connect.add_argument("--force", action="store_true", help="overwrite an existing config")
    p_connect.add_argument(
        "--json", action="store_true", help="machine-readable result for the GUI"
    )
    p_connect.set_defaults(func=_cmd_connect)

    p_init = sub.add_parser("init", help="write a starter config file without storing secrets")
    p_init.add_argument("--config", help="config TOML path")
    p_init.add_argument("--non-interactive", action="store_true")
    p_init.add_argument("--smb-url")
    p_init.add_argument("--mount-point")
    p_init.add_argument(
        "--subpath",
        default=None,
        help="per-Mac subfolder on the share (default: this Mac's name; pass '' to disable)",
    )
    p_init.add_argument("--jpeg", action="store_true", help="default to JPEG compatibility copies")
    p_init.add_argument("--mp4", action="store_true", help="default to MP4 compatibility copies")
    p_init.add_argument("--force", action="store_true", help="overwrite an existing config")
    p_init.set_defaults(func=_cmd_init)

    p_shortcut = sub.add_parser(
        "install-shortcut",
        help="write a no-secrets launcher script for a macOS Shortcut",
    )
    p_shortcut.add_argument("--config", help="config TOML path to pass to send")
    p_shortcut.add_argument("--script", help="launcher script path")
    p_shortcut.add_argument("--force", action="store_true", help="overwrite an existing script")
    p_shortcut.set_defaults(func=_cmd_install_shortcut)

    p_sanitize = sub.add_parser(
        "sanitize-report",
        help="write a privacy-preserving copy of an osxphotos JSON or CSV report",
    )
    p_sanitize.add_argument("source", help="source .json or .csv report")
    p_sanitize.add_argument("target", help="sanitized output path")
    p_sanitize.add_argument("--force", action="store_true", help="overwrite target if it exists")
    p_sanitize.set_defaults(func=_cmd_sanitize_report)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


def _add_runtime_options(parser: argparse.ArgumentParser) -> None:
    # Preferences (JPEG/MP4 copies, PhotoKit, removal) live in config.toml only, set
    # once at init — so behavior is identical across the CLI, the GUI, and relaunches.
    parser.add_argument("--config", help="config TOML path")
    parser.add_argument("--album", help="export a named album instead of the live selection")


def _print_tool_statuses(statuses: Sequence[ToolStatus]) -> None:
    width = max(len(status.tool.name) for status in statuses)
    for status in statuses:
        mark = "ok" if status.found else ("MISSING" if status.tool.required else "absent")
        kind = "required" if status.tool.required else "optional"
        detail = status.version or status.path or status.tool.purpose
        print(f"  [{mark:>7}] {status.tool.name:<{width}}  ({kind})  {detail}")


def _print_missing_tools(missing: Sequence[ToolStatus]) -> None:
    names = ", ".join(status.tool.name for status in missing)
    print(f"Missing required tool(s): {names}", file=sys.stderr)
    print(
        "Install with: pip install osxphotos && brew install exiftool"
        " (and brew install ffmpeg for MP4 copies)",
        file=sys.stderr,
    )


def _on_boot_volume(path: Path) -> bool:
    """True if ``path`` (or its nearest existing parent) sits on the same device as ``/``.

    Used to refuse a destination that resolves to this Mac's own boot disk — e.g. a stale
    ``/Volumes/<share>`` directory left after an unclean unmount. Backing up there and then
    deleting originals would leave the only copy on the same disk as the Photos library.
    """
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return probe.stat().st_dev == Path("/").stat().st_dev
    except OSError:
        return False


def _ensure_destination_ready(config: Config, destination: Path) -> str | None:
    mount_point = Path(config.destination.mount_point).expanduser()
    try:
        if config.destination.smb_url and mount_point:
            ensure_mounted(config.destination.smb_url, mount_point)
            if destination.exists() and not is_writable(destination):
                return f"{destination} exists but is not writable"
            if not destination.exists() and not is_writable(mount_point):
                return f"{mount_point} is not writable"
            return None
        # smb_url is empty (manual-mount config): there is NO mount verification here, so a
        # stale /Volumes dir on the boot disk would look "ready". Refuse the boot volume.
        if _on_boot_volume(destination):
            return (
                f"{destination} is on this Mac's boot disk, not a mounted share — refusing to "
                "use the Mac's own disk as the backup destination. Set destination.smb_url to "
                "your share and mount it (a backup on the same disk as the library is no backup)."
            )
        if destination.exists() and is_writable(destination):
            return None
        if (
            not destination.exists()
            and destination.parent.exists()
            and is_writable(destination.parent)
        ):
            return None
        return f"{destination} is not writable"
    except SmbError as exc:
        return str(exc)


def _acquire_destination_lock(exportdb_dir: Path, destination: Path):
    """Take a non-blocking per-destination lock, or return ``None`` if busy.

    The lock file lives in the local state dir and is keyed on the destination
    hash, so it serializes two sends to the same destination *on this Mac* (the
    double-pressed-hotkey case). It does not coordinate across Macs — per-Mac
    subpaths keep different Macs in different trees. The caller holds the handle
    for the run; the flock is released automatically on close or process exit.
    """
    exportdb_dir.mkdir(parents=True, exist_ok=True)
    stem = resolved_exportdb_path(destination, exportdb_dir).stem
    handle = (exportdb_dir / f"{stem}.lock").open("w")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def _cmd_cleanup_last(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config)
        destination = config.destination_path()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EXIT_USAGE

    log_dir = Path(config.state.log_dir).expanduser()
    token = state.load_backup_token(log_dir, destination)
    if token is None or not token.assets:
        if token is None and state.stale_token_exists(log_dir, destination):
            print(
                "A backup from an older version of photos-tool cannot be auto-cleaned; "
                "re-run send to record a fresh one.",
                file=sys.stderr,
            )
        else:
            print(
                "No backup recorded for this destination yet. Run a backup first.",
                file=sys.stderr,
            )
        return EXIT_USAGE

    # Re-validate the share is the configured one and is mounted before trusting any
    # on-disk path — the destructive step must never act on a stale or wrong volume.
    mount_error = _ensure_destination_ready(config, destination)
    if mount_error:
        print(f"preflight error: {mount_error}", file=sys.stderr)
        return EXIT_PREFLIGHT
    if token.smb_url and config.destination.smb_url and token.smb_url != config.destination.smb_url:
        print(
            "The configured share has changed since this backup; not removing anything.",
            file=sys.stderr,
        )
        return EXIT_PREFLIGHT

    removable, kept = state.removable_assets(token)

    if args.json:
        print(
            json.dumps(
                {
                    "count": len(removable),
                    "destination": token.destination_root,
                    "reveal": state.reveal_paths(token, set(removable)),
                }
            )
        )
        return EXIT_OK

    if kept:
        print(
            f"Keeping {len(kept)} original(s): their backup copy is missing or changed on the "
            "share.",
            file=sys.stderr,
        )
    if not removable:
        print("No backed-up originals are confirmed on the share; nothing to remove.")
        return EXIT_OK

    if args.dry_run:
        try:
            result = remove_originals(removable, dry_run=True, max_delete=config.remove.max_delete)
        except RemoveError as exc:
            print(f"Dry run could not verify originals in Photos: {exc}", file=sys.stderr)
            return EXIT_PREFLIGHT
        print(
            f"Would move {result.requested} backed-up original(s) to Recently Deleted "
            "(recoverable ~30 days). Nothing was deleted."
        )
        return EXIT_OK

    if not args.yes:
        if not sys.stdin.isatty():
            print("Refusing to remove without confirmation; pass --yes.", file=sys.stderr)
            return EXIT_USAGE
        answer = (
            input(
                f"Move {len(removable)} backed-up originals to Recently Deleted "
                "(recoverable ~30 days)? [y/N] "
            )
            .strip()
            .lower()
        )
        if answer not in {"y", "yes"}:
            print("Left originals in Photos.")
            return EXIT_OK

    try:
        result = remove_originals(removable, dry_run=False, max_delete=config.remove.max_delete)
    except RemoveError as exc:
        print(f"Could not remove originals: {exc}", file=sys.stderr)
        return EXIT_PREFLIGHT
    # Consume the token so the same batch can never be offered for deletion again.
    state.clear_backup_token(log_dir, destination)
    print(f"Moved {result.deleted} original(s) to Recently Deleted (recoverable ~30 days).")
    return EXIT_OK


def _export_options(
    config: Config,
    destination: Path,
    exportdb_dir: Path,
    scope: str,
    album: str | None,
    use_photokit: bool,
    *,
    compat: bool = False,
) -> ExportOptions:
    # The compat pass writes a Windows-friendly mirror: stills converted to JPEG,
    # no Live Photo motion movies and no standalone movies (those become MP4s).
    return ExportOptions(
        destination=str(destination),
        scope=scope,
        album=album,
        exportdb=str(resolved_exportdb_path(destination, exportdb_dir)),
        exiftool=True,
        download_missing=config.export.download_missing,
        use_photokit=use_photokit,
        touch_file=True,
        retry=config.export.retry,
        convert_to_jpeg=compat,
        jpeg_quality=config.copies.jpeg_quality,
        only_photos=compat,
        skip_live=compat,
        directory_template=config.export.directory_template,
        filename_template=config.export.filename_template,
    )


def _send_dry_run(opts: ExportOptions, selected: int) -> int:
    with tempfile.TemporaryDirectory(prefix="photos-tool-dry-run-") as tmp:
        report_path = Path(tmp) / "report.json"
        result = run_export(opts, report_path, dry_run=True)
        if not result.ok:
            print(f"dry run failed with exit {result.returncode}", file=sys.stderr)
            _print_captured(result)
            return EXIT_PREFLIGHT
        try:
            report = parse_report(report_path)
            reconciliation = summarize(report, selected)
        except ReportError as exc:
            print(f"report error: {exc}", file=sys.stderr)
            return EXIT_PREFLIGHT
        _print_summary(
            Path(opts.destination),
            selected,
            report,
            reconciliation.message,
            dry_run=True,
        )
        if report.total_files and report.issue_count / report.total_files >= 0.25:
            print(
                "Warning: many rows were missing or errored in dry-run; this often means "
                "iCloud Optimize Mac Storage is leaving originals cloud-only.",
                file=sys.stderr,
            )
        _warn_report_shape(report)
    return EXIT_OK


def _run_jpeg_pass(
    *,
    config: Config,
    destination: Path,
    exportdb_dir: Path,
    scope: str,
    album: str | None,
    use_photokit: bool,
    report_dir: Path,
    verbose: bool,
) -> ReportSummary:
    compat_destination = destination / "compat"
    opts = _export_options(
        config,
        compat_destination,
        exportdb_dir,
        scope,
        album,
        use_photokit,
        compat=True,
    )
    report_path = report_dir / "jpeg.json"
    result = run_export(opts, report_path)
    if verbose:
        _print_captured(result)
    if not result.ok:
        raise OsxphotosError(f"JPEG compatibility export failed with exit {result.returncode}")
    report = parse_report(report_path)
    print(f"JPEG compatibility copies: {report.converted} converted into {compat_destination}")
    _warn_report_shape(report)
    return report


def _print_summary(
    destination: Path,
    selected: int,
    report: ReportSummary,
    message: str,
    *,
    dry_run: bool = False,
) -> None:
    prefix = "Dry run: " if dry_run else ""
    print(f"{prefix}{message}")
    print(f"Destination: {destination}")
    print(
        "Report: "
        f"{report.total_files} file row(s), {report.exported} exported, {report.new} new, "
        f"{report.updated} updated, {report.skipped} skipped, "
        f"{report.missing} missing, {report.error} error."
    )
    print(f"Selected assets: {selected}")
    if report.exiftool_error or report.exiftool_warning:
        print(
            f"Note: metadata embedding reported {report.exiftool_error} error(s) and "
            f"{report.exiftool_warning} warning(s). The photo/video bytes copied fine, but "
            "some EXIF/GPS/date tags on those files may be incomplete — check the report.",
            file=sys.stderr,
        )
    if dry_run:
        print("No files were written.")


def _warn_report_shape(report: ReportSummary) -> None:
    # Missing REQUIRED columns are already a hard EXIT_PREFLIGHT in _cmd_send (we never
    # reach here in that case); this only surfaces unexpected NEW columns, informationally.
    extra = unexpected_columns(report)
    if extra:
        print(
            "Warning: osxphotos report had unexpected column(s): " + ", ".join(sorted(extra)),
            file=sys.stderr,
        )


def _status_message(exit_code: int) -> str:
    return {
        EXIT_OK: "All selected photos sent.",
        EXIT_PREFLIGHT: "Send failed before exporting; run photos-tool doctor.",
        EXIT_RECONCILE: "Some photos were skipped (often iCloud cloud-only originals).",
        EXIT_NOTHING_SELECTED: "Nothing was selected.",
        EXIT_CONVERSION: "Photos sent, but a compatibility (JPEG/MP4) copy failed.",
    }.get(exit_code, f"Send ended with code {exit_code}.")


def _last_run_path(log_dir: Path) -> Path:
    return log_dir / "last-run.json"


def _write_run_log(
    log_dir: Path,
    *,
    started: float,
    exit_code: int,
    scope: str,
    selected: int,
    exported: int,
    missing: int,
    error: int,
    converted: int = 0,
    mp4: int = 0,
    exiftool_error: int = 0,
    exiftool_warning: int = 0,
) -> None:
    row = {
        "status": _status_message(exit_code),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "scope": scope,
        "selected": selected,
        "exported": exported,
        "missing": missing,
        "error": error,
        "exiftool_error": exiftool_error,
        "exiftool_warning": exiftool_warning,
        "converted": converted,
        "mp4": mp4,
        "duration_seconds": round(time.monotonic() - started, 3),
        "exit_code": exit_code,
    }
    # One atomically-replaced record, not a growing log — the GUI only reads the last run.
    state.atomic_write_json(_last_run_path(log_dir), row)


def _print_last_report(config: Config) -> int:
    path = _last_run_path(Path(config.state.log_dir).expanduser())
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        print(f"No run recorded at {path}", file=sys.stderr)
        return EXIT_PREFLIGHT
    try:
        row = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"Last run record in {path} is corrupt: {exc}", file=sys.stderr)
        return EXIT_PREFLIGHT
    print(json.dumps(row, indent=2, sort_keys=True))
    return EXIT_OK


def _print_captured(result: ExportResult) -> None:
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)


def _default_subpath() -> str:
    """A filesystem-safe per-Mac subfolder so family Macs do not collide on names."""
    name = socket.gethostname().split(".")[0]
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-")
    return slug or "mac"


def _default_mount_point(smb_url: str) -> str:
    """Guess where macOS mounts ``smb://host/Share`` — ``/Volumes/Share``."""
    if not smb_url:
        return ""
    share = urlparse(smb_url).path.strip("/").split("/")[0]
    return f"/Volumes/{share}" if share else ""


def _render_config(smb_url: str, mount_point: str, subpath: str, *, jpeg: bool, mp4: bool) -> str:
    return f"""[destination]
smb_url = {json.dumps(smb_url)}
mount_point = {json.dumps(mount_point)}
subpath = {json.dumps(subpath)}

[export]
directory_template = "{{created.year}}/{{created.mm}}"
filename_template = "{{original_name}}"
download_missing = true
use_photokit = false
retry = 3

[copies]
jpeg = {str(jpeg).lower()}
jpeg_quality = 0.9
mp4 = {str(mp4).lower()}
mp4_crf = 20

[state]
exportdb_dir = "~/.local/state/photos-tool/exportdb"
log_dir = "~/.local/state/photos-tool/logs"
"""


def _render_shortcut_script(executable: str, config_path: Path | None) -> str:
    args = [executable, "send"]
    if config_path is not None:
        args += ["--config", str(config_path)]
    # The final echoed line is what a Shortcut surfaces as a notification, so it
    # maps the send exit code to a human-readable status. No password ever here.
    return (
        "#!/usr/bin/env bash\n"
        "set -uo pipefail\n"
        'export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"\n'
        f"{shlex.join(args)}\n"
        "code=$?\n"
        'case "$code" in\n'
        '  0) echo "✅ Photos sent." ;;\n'
        "  3) echo \"⚠️ Some photos were skipped — turn on iCloud 'Download Originals' "
        'and retry." ;;\n'
        '  4) echo "👉 Nothing selected — pick photos in Photos first." ;;\n'
        '  5) echo "⚠️ Photos sent, but a compatibility (JPEG/MP4) copy failed." ;;\n'
        '  *) echo "❌ Send failed (code $code) — run: photos-tool doctor" ;;\n'
        "esac\n"
        'exit "$code"\n'
    )


def _photos_tool_executable() -> str:
    path = shutil.which("photos-tool")
    if path:
        return path
    argv0 = Path(sys.argv[0])
    if argv0.is_absolute():
        return str(argv0)
    return "photos-tool"


if __name__ == "__main__":
    raise SystemExit(main())
