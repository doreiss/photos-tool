"""Command-line interface for photos-tool."""

from __future__ import annotations

import argparse
import shlex
import sys
from collections.abc import Sequence

from . import __version__
from .plan import ExportOptions, build_export_command
from .tooling import missing_required, probe_all


def _cmd_check(_args: argparse.Namespace) -> int:
    statuses = probe_all()
    width = max(len(status.tool.name) for status in statuses)
    for status in statuses:
        mark = "ok" if status.found else ("MISSING" if status.tool.required else "absent")
        kind = "required" if status.tool.required else "optional"
        detail = status.version or status.path or status.tool.purpose
        print(f"  [{mark:>7}] {status.tool.name:<{width}}  ({kind})  {detail}")

    missing = missing_required(statuses)
    if missing:
        names = ", ".join(status.tool.name for status in missing)
        print(f"\nMissing required tool(s): {names}", file=sys.stderr)
        print("Install with: brew install exiftool && pip install osxphotos", file=sys.stderr)
        return 1
    print("\nAll required tools present.")
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    opts = ExportOptions(
        destination=args.destination,
        scope="album" if args.album else "selected",
        album=args.album,
        convert_to_jpeg=args.jpeg,
        jpeg_quality=args.jpeg_quality,
        directory_template=args.directory,
        filename_template=args.filename,
    )
    try:
        cmd = build_export_command(opts)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(shlex.join(cmd))
    return 0


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
    scope = p_plan.add_mutually_exclusive_group()
    scope.add_argument(
        "--selected",
        action="store_true",
        help="export the photos currently selected in Photos (default)",
    )
    scope.add_argument("--album", help="export a named album instead of the live selection")
    p_plan.add_argument("--jpeg", action="store_true", help="also write HEIC->JPEG copies")
    p_plan.add_argument("--jpeg-quality", type=float, default=0.9)
    p_plan.add_argument("--directory", default="{created.year}/{created.mm}")
    p_plan.add_argument("--filename", default="{original_name}")
    p_plan.set_defaults(func=_cmd_plan)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
