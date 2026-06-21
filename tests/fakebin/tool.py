#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    tool = os.environ.get("PHOTOS_TOOL_FAKE_NAME", Path(sys.argv[0]).name)
    scenario = _scenario()
    _log(tool, sys.argv[1:])
    if "--version" in sys.argv[1:] or "-ver" in sys.argv[1:]:
        print(f"{tool} fake 1.0")
        return 0
    if tool == "osxphotos":
        return _osxphotos(scenario)
    if tool == "ffprobe":
        return _ffprobe(scenario)
    if tool == "ffmpeg":
        return _ffmpeg(scenario)
    if tool == "exiftool":
        return 0
    if tool == "osascript":
        return _osascript(scenario)
    if tool == "mount":
        return _mount(scenario)
    print(f"unknown fake tool: {tool}", file=sys.stderr)
    return 2


def _osxphotos(scenario: dict) -> int:
    args = sys.argv[1:]
    if args[:1] == ["query"]:
        if scenario.get("auth_error"):
            print("Operation not permitted", file=sys.stderr)
            return 1
        count = int(scenario.get("selected", 0))
        if count == 0 and "--selected" in args:
            # Mirror real osxphotos: empty selection exits non-zero with a help message.
            print("--selected option used but no photos selected in Photos.", file=sys.stderr)
            return 1
        print(count)
        return 0
    if args[:1] == ["export"]:
        if "--cleanup" in args:
            print("--cleanup is banned", file=sys.stderr)
            return 99
        report_path = Path(args[args.index("--report") + 1])
        dry_run = "--dry-run" in args
        destination = Path(args[1])
        report_path.parent.mkdir(parents=True, exist_ok=True)
        if scenario.get("invalid_report") and not dry_run and destination.name != "compat":
            report_path.write_text("{not-json", encoding="utf-8")
        else:
            report = _pick_report(scenario, destination, dry_run)
            if not dry_run and destination.name != "compat":
                # Land each exported/already-present originals row as a real file on the share
                # and point the row at it, so the report agrees with what is on disk — the delete
                # gate re-fingerprints those bytes, and send now demotes if they are absent.
                _materialize(report, destination)
            report_path.write_text(json.dumps(report), encoding="utf-8")
        if not dry_run:
            only_photos = "--only-photos" in args
            for rel in _pick_files(scenario, destination):
                # Mirror --only-photos: the compat pass never writes movies.
                if only_photos and Path(rel).suffix.lower() in {".mov", ".m4v"}:
                    continue
                path = destination / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("fake", encoding="utf-8")
        if destination.name == "compat":
            return int(scenario.get("jpeg_returncode", scenario.get("export_returncode", 0)))
        return int(scenario.get("export_returncode", 0))
    return 0


def _pick_report(scenario: dict, destination: Path, dry_run: bool) -> list[dict]:
    if dry_run:
        return list(scenario.get("dry_report", scenario.get("report", [])))
    if destination.name == "compat":
        return list(scenario.get("jpeg_report", scenario.get("report", [])))
    if "report" in scenario:
        return list(scenario["report"])
    # No explicit report: DERIVE the rows from the files actually written, so the report
    # can never silently disagree with what is on disk (the delete gate's core invariant).
    # uuid = the file stem, so a multi-file asset (a.heic + a.mov) groups under one uuid.
    return [
        {
            "uuid": Path(rel).stem,
            "filename": str(destination / rel),
            "exported": True,
            "new": True,
            "skipped": False,
            "missing": False,
            "error": False,
        }
        for rel in _pick_files(scenario, destination)
    ]


def _materialize(report: list[dict], destination: Path) -> None:
    """Write a real non-empty file for each exported/already-present originals row and rewrite
    its filename to that path, so the fake report agrees with disk. Rows that are missing/error
    or have an empty filename are left untouched (no copy on the share) — exactly the cases the
    delete gate must drop and `send` must now treat as not-fully-backed-up.
    """
    for r in report:
        filename = r.get("filename")
        if not filename or r.get("missing") or r.get("error"):
            continue
        if not (r.get("exported") or r.get("skipped")):
            continue
        path = Path(filename)
        try:
            under_destination = path.is_relative_to(destination)
        except ValueError:
            under_destination = False
        if under_destination:
            # Already a real share path: whether it lands is governed by scenario["files"]
            # (so a test can deliberately keep a constituent OFF the share). Leave it to the
            # _pick_files loop; do not force it to exist here.
            continue
        path = destination / path.name  # a synthetic /fake/... row -> relocate onto the share
        r["filename"] = str(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fake", encoding="utf-8")


def _pick_files(scenario: dict, destination: Path) -> list[str]:
    key = "jpeg_files" if destination.name == "compat" else "files"
    return list(scenario.get(key, []))


def _ffprobe(scenario: dict) -> int:
    path = Path(sys.argv[-1])
    codecs = scenario.get("codecs", {})
    print(codecs.get(path.name, "hevc"))
    return 0


def _ffmpeg(scenario: dict) -> int:
    if scenario.get("ffmpeg_fail") and "-i" in sys.argv[1:]:
        print("ffmpeg failed by scenario", file=sys.stderr)
        return 1
    output = Path(sys.argv[-1])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("mp4", encoding="utf-8")
    return 0


def _osascript(scenario: dict) -> int:
    if scenario.get("mount_fail"):
        print("mount failed", file=sys.stderr)
        return 1
    _state_path().write_text(json.dumps({"mounted": True}), encoding="utf-8")
    return 0


def _mount(scenario: dict) -> int:
    mounted = bool(scenario.get("initial_mounted", False))
    state = _state_path()
    if state.exists():
        mounted = json.loads(state.read_text(encoding="utf-8")).get("mounted", mounted)
    if mounted:
        print(f"//photos@pc/FamilyPhotos on {scenario['mount_point']} (smbfs, nodev)")
    return 0


def _scenario() -> dict:
    path = os.environ.get("PHOTOS_TOOL_FAKE_SCENARIO")
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _state_path() -> Path:
    return Path(os.environ["PHOTOS_TOOL_FAKE_STATE"])


def _log(tool: str, argv: list[str]) -> None:
    path = os.environ.get("PHOTOS_TOOL_FAKE_LOG")
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"tool": tool, "argv": argv}) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
