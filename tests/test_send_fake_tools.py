from __future__ import annotations

from pathlib import Path
from typing import Any

from photos_tool import cli


def row(
    uuid: str,
    *,
    exported: bool = True,
    new: bool = True,
    skipped: bool = False,
    converted: bool = False,
    missing: bool = False,
    error: bool = False,
    exiftool_error: bool = False,
) -> dict[str, Any]:
    return {
        "uuid": uuid,
        "filename": f"/fake/{uuid}",
        "exported": exported,
        "new": new,
        "updated": False,
        "skipped": skipped,
        "converted_to_jpeg": converted,
        "missing": missing,
        "error": error,
        "exiftool_error": exiftool_error,
    }


def write_config(tmp_path: Path, mount_point: Path) -> Path:
    config = tmp_path / "config.toml"
    config.write_text(
        f"""
[destination]
smb_url = "smb://pc/FamilyPhotos"
mount_point = {str(mount_point)!r}
subpath = ""

[export]
directory_template = "{{created.year}}/{{created.mm}}"
filename_template = "{{original_name}}"
download_missing = true
use_photokit = false
retry = 3

[copies]
jpeg = false
jpeg_quality = 0.9
mp4 = false
mp4_crf = 20

[state]
exportdb_dir = {str(tmp_path / "state" / "exportdb")!r}
log_dir = {str(tmp_path / "state" / "logs")!r}
""",
        encoding="utf-8",
    )
    return config


def scenario(mount_point: Path, **overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "selected": 3,
        "initial_mounted": True,
        "mount_point": str(mount_point),
        "report": [
            row("asset-1"),
            row("asset-2"),
            row("asset-3", exported=False, new=False, skipped=True),
        ],
        "files": ["2024/01/IMG_0001.HEIC"],
    }
    base.update(overrides)
    return base


def osxphotos_exports(log: list[dict[str, Any]]) -> list[list[str]]:
    return [
        entry["argv"]
        for entry in log
        if entry["tool"] == "osxphotos" and entry["argv"][:1] == ["export"]
    ]


def test_send_success_persists_local_report_and_exportdb(tmp_path: Path, fake_tools, capsys):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    tools = fake_tools(scenario(mount))

    rc = cli.main(["send", "--config", str(config)])
    captured = capsys.readouterr()

    assert rc == 0
    assert "All 3 selected" in captured.out
    exports = osxphotos_exports(tools.log())
    assert len(exports) == 1
    argv = exports[0]
    assert "--cleanup" not in argv
    report_path = Path(argv[argv.index("--report") + 1])
    exportdb_path = Path(argv[argv.index("--exportdb") + 1])
    assert not report_path.is_relative_to(mount)
    assert not exportdb_path.is_relative_to(mount)
    assert list((tmp_path / "state" / "logs").glob("*-original-report.json"))
    assert (tmp_path / "state" / "logs" / "runs.jsonl").exists()


def test_send_album_uses_album_scope_not_selected(tmp_path: Path, fake_tools):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    tools = fake_tools(scenario(mount, selected=1, report=[row("asset-1")]))

    assert cli.main(["send", "--config", str(config), "--album", "Summer Trip"]) == 0

    query_calls = [
        entry["argv"]
        for entry in tools.log()
        if entry["tool"] == "osxphotos" and entry["argv"][:1] == ["query"]
    ]
    export = osxphotos_exports(tools.log())[0]
    assert "--album" in query_calls[0]
    assert "Summer Trip" in query_calls[0]
    assert "--album" in export
    assert "Summer Trip" in export
    assert "--selected" not in export


def test_send_missing_required_tool_exits_one(tmp_path: Path, fake_tools, monkeypatch, capsys):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    tools = fake_tools(scenario(mount))
    missing = [
        status if status.tool.name != "osxphotos" else status.__class__(status.tool, None, None)
        for status in cli.probe_all()
    ]
    monkeypatch.setattr(cli, "probe_all", lambda: missing)

    rc = cli.main(["send", "--config", str(config)])
    captured = capsys.readouterr()

    assert rc == 1
    assert "osxphotos" in captured.err
    assert osxphotos_exports(tools.log()) == []


def test_send_last_report_prints_last_run_summary(tmp_path: Path, fake_tools, capsys):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    fake_tools(scenario(mount, selected=1, report=[row("asset-1")]))

    assert cli.main(["send", "--config", str(config)]) == 0
    rc = cli.main(["send", "--config", str(config), "--last-report"])
    captured = capsys.readouterr()

    assert rc == 0
    assert '"selected": 1' in captured.out
    assert '"exit_code": 0' in captured.out


def test_send_last_report_handles_corrupt_last_line(tmp_path: Path, capsys):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    log_dir = tmp_path / "state" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "runs.jsonl").write_text('{"ok": true}\n{not-json\n', encoding="utf-8")

    rc = cli.main(["send", "--config", str(config), "--last-report"])
    captured = capsys.readouterr()

    assert rc == 1
    assert "corrupt" in captured.err


def test_send_exits_three_when_report_has_missing_rows(tmp_path: Path, fake_tools, capsys):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    fake_tools(
        scenario(
            mount,
            selected=2,
            report=[row("asset-1"), row("asset-2", exported=False, new=False, missing=True)],
        )
    )

    rc = cli.main(["send", "--config", str(config)])
    captured = capsys.readouterr()

    assert rc == 3
    assert "Optimize Mac Storage" in captured.out


def test_send_exits_three_when_report_has_error_rows(tmp_path: Path, fake_tools):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    fake_tools(
        scenario(
            mount,
            selected=2,
            report=[row("asset-1"), row("asset-2", exported=False, new=False, error=True)],
        )
    )

    assert cli.main(["send", "--config", str(config)]) == 3


def test_send_exiftool_error_does_not_exit_three(tmp_path: Path, fake_tools, capsys):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    fake_tools(scenario(mount, selected=1, report=[row("asset-1", exiftool_error=True)]))

    rc = cli.main(["send", "--config", str(config)])
    captured = capsys.readouterr()

    assert rc == 0
    assert "All 1 selected" in captured.out


def test_send_report_parse_failure_writes_run_log(tmp_path: Path, fake_tools, capsys):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    fake_tools(scenario(mount, selected=1, invalid_report=True))

    rc = cli.main(["send", "--config", str(config)])
    captured = capsys.readouterr()

    assert rc == 1
    assert "report error" in captured.err
    assert (tmp_path / "state" / "logs" / "runs.jsonl").exists()


def test_send_noop_dedup_report_is_success(tmp_path: Path, fake_tools, capsys):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    fake_tools(
        scenario(
            mount,
            selected=2,
            report=[
                row("asset-1", exported=False, new=False, skipped=True),
                row("asset-2", exported=False, new=False, skipped=True),
            ],
            files=[],
        )
    )

    rc = cli.main(["send", "--config", str(config)])
    captured = capsys.readouterr()

    assert rc == 0
    assert "All 2 selected" in captured.out


def test_send_empty_selection_exits_four_without_export(tmp_path: Path, fake_tools, capsys):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    tools = fake_tools(scenario(mount, selected=0, report=[]))

    rc = cli.main(["send", "--config", str(config)])
    captured = capsys.readouterr()

    assert rc == 4
    assert "Nothing selected" in captured.out
    assert osxphotos_exports(tools.log()) == []


def test_send_dry_run_warns_on_high_missing_fraction(tmp_path: Path, fake_tools, capsys):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    fake_tools(
        scenario(
            mount,
            selected=2,
            dry_report=[
                row("asset-1", exported=False, new=False, missing=True),
                row("asset-2", exported=False, new=False, missing=True),
            ],
        )
    )

    rc = cli.main(["send", "--config", str(config), "--dry-run"])
    captured = capsys.readouterr()

    assert rc == 0
    assert "No files were written" in captured.out
    assert "Optimize Mac Storage" in captured.err
    assert not any(mount.rglob("*"))
    assert (tmp_path / "state" / "exportdb").is_dir()


def test_send_use_photokit_modifies_download_missing_and_warns(tmp_path: Path, fake_tools, capsys):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    tools = fake_tools(scenario(mount, selected=1, report=[row("asset-1")]))

    rc = cli.main(["send", "--config", str(config), "--use-photokit"])
    captured = capsys.readouterr()

    assert rc == 0
    export = osxphotos_exports(tools.log())[0]
    assert "--use-photokit" in export
    assert "--download-missing" in export
    assert "Terminal.app" in captured.err


def test_send_photos_authorization_failure_exits_one(tmp_path: Path, fake_tools, capsys):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    fake_tools(scenario(mount, auth_error=True))

    rc = cli.main(["send", "--config", str(config)])
    captured = capsys.readouterr()

    assert rc == 1
    assert "Full Disk Access" in captured.err


def test_send_auto_mounts_unmounted_share(tmp_path: Path, fake_tools):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    tools = fake_tools(scenario(mount, initial_mounted=False))

    assert cli.main(["send", "--config", str(config)]) == 0
    assert any(entry["tool"] == "osascript" for entry in tools.log())


def test_send_fails_when_mounted_share_is_not_writable(tmp_path: Path, fake_tools):
    mount = tmp_path / "missing"
    config = write_config(tmp_path, mount)
    fake_tools(scenario(mount, initial_mounted=True))

    assert cli.main(["send", "--config", str(config)]) == 1


def test_send_jpeg_runs_parallel_compat_export(tmp_path: Path, fake_tools, capsys):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    tools = fake_tools(
        scenario(
            mount,
            selected=1,
            report=[row("asset-1")],
            jpeg_report=[row("asset-1", converted=True)],
            jpeg_files=["2024/01/IMG_0001.jpg"],
        )
    )

    rc = cli.main(["send", "--config", str(config), "--jpeg"])
    captured = capsys.readouterr()

    assert rc == 0
    exports = osxphotos_exports(tools.log())
    assert len(exports) == 2
    assert Path(exports[1][1]) == mount / "compat"
    assert "--convert-to-jpeg" in exports[1]
    # The compat pass is stills-only so it never drops broken HEVC movies in compat/.
    assert "--only-photos" in exports[1]
    assert "--skip-live" in exports[1]
    assert "--only-photos" not in exports[0]
    assert "JPEG compatibility copies" in captured.out


def test_send_jpeg_failure_exits_five_after_original_export(tmp_path: Path, fake_tools, capsys):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    fake_tools(
        scenario(
            mount,
            selected=1,
            report=[row("asset-1")],
            jpeg_returncode=1,
        )
    )

    rc = cli.main(["send", "--config", str(config), "--jpeg"])
    captured = capsys.readouterr()

    assert rc == 5
    assert "JPEG compatibility export error" in captured.err


def test_send_jpeg_fewer_compat_copies_is_not_fatal(tmp_path: Path, fake_tools, capsys):
    # The compat pass is photos-only, so exporting fewer items than were selected is
    # expected (videos are excluded) and must NOT fail the run. A real missing/errored
    # compat row is surfaced as a non-fatal warning; the originals export still drives
    # the exit code.
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    fake_tools(
        scenario(
            mount,
            selected=2,
            report=[row("photo"), row("video")],
            files=["2024/01/IMG_0001.HEIC", "2024/01/VID_0002.MOV"],
            jpeg_report=[row("photo", converted=True)],
            jpeg_files=["2024/01/IMG_0001.jpeg"],
        )
    )

    rc = cli.main(["send", "--config", str(config), "--jpeg"])
    captured = capsys.readouterr()

    assert rc == 0
    assert "JPEG compatibility export error" not in captured.err


def test_send_mp4_transcodes_standalone_hevc_only(tmp_path: Path, fake_tools):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    tools = fake_tools(
        scenario(
            mount,
            selected=2,
            report=[row("live"), row("video")],
            files=[
                "2024/01/IMG_0001.HEIC",
                "2024/01/IMG_0001.MOV",
                "2024/01/VID_0002.MOV",
            ],
            codecs={"IMG_0001.MOV": "hevc", "VID_0002.MOV": "hevc"},
        )
    )

    assert cli.main(["send", "--config", str(config), "--mp4"]) == 0
    log = tools.log()
    ffmpeg_calls = [
        entry["argv"] for entry in log if entry["tool"] == "ffmpeg" and "-i" in entry["argv"]
    ]
    exiftool_calls = [
        entry["argv"]
        for entry in log
        if entry["tool"] == "exiftool" and "-tagsFromFile" in entry["argv"]
    ]
    assert len(ffmpeg_calls) == 1
    assert "VID_0002.MOV" in " ".join(ffmpeg_calls[0])
    assert "IMG_0001.MOV" not in " ".join(ffmpeg_calls[0])
    assert len(exiftool_calls) == 1
    assert "-overwrite_original" in exiftool_calls[0]
    assert exiftool_calls[0][-1].endswith("VID_0002.mp4")


def test_send_jpeg_mp4_transcodes_from_main_tree_into_compat(tmp_path: Path, fake_tools):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    tools = fake_tools(
        scenario(
            mount,
            selected=2,
            report=[row("photo"), row("video")],
            files=["2024/01/IMG_0001.HEIC", "2024/01/VID_0002.MOV"],
            jpeg_report=[row("photo", converted=True)],
            # --only-photos means the compat pass would not write this movie; the fake
            # honours that, so the only video on disk is in the pristine main tree.
            jpeg_files=["2024/01/IMG_0001.jpeg", "2024/01/VID_0002.MOV"],
            codecs={"VID_0002.MOV": "hevc"},
        )
    )

    assert cli.main(["send", "--config", str(config), "--jpeg", "--mp4"]) == 0

    ffmpeg_calls = [
        entry["argv"]
        for entry in tools.log()
        if entry["tool"] == "ffmpeg" and "-i" in entry["argv"]
    ]
    assert len(ffmpeg_calls) == 1
    argv = ffmpeg_calls[0]
    source = argv[argv.index("-i") + 1]
    output = argv[-1]
    # Source is the pristine original; the H.264 copy lands in the compat/ mirror.
    assert source.endswith("2024/01/VID_0002.MOV")
    assert "/compat/" not in source
    assert output.endswith("/compat/2024/01/VID_0002.mp4")
    # The compat pass never created a video to re-transcode.
    assert not (mount / "compat" / "2024" / "01" / "VID_0002.MOV").exists()


def test_send_mp4_conversion_error_exits_five(tmp_path: Path, fake_tools, capsys):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    fake_tools(
        scenario(
            mount,
            selected=1,
            report=[row("video")],
            files=["2024/01/VID_0002.MOV"],
            codecs={"VID_0002.MOV": "hevc"},
            ffmpeg_fail=True,
        )
    )

    rc = cli.main(["send", "--config", str(config), "--mp4"])
    captured = capsys.readouterr()

    assert rc == 5
    assert "conversion error" in captured.err


def test_send_album_typo_reports_album_specific_message(tmp_path: Path, fake_tools, capsys):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    tools = fake_tools(scenario(mount, selected=0))

    rc = cli.main(["send", "--config", str(config), "--album", "Hawaii 2019"])
    captured = capsys.readouterr()

    assert rc == 4
    assert "No photos matched album 'Hawaii 2019'" in captured.out
    assert "Nothing selected" not in captured.out
    assert osxphotos_exports(tools.log()) == []


def test_send_warns_when_no_per_mac_subpath(tmp_path: Path, fake_tools, capsys):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)  # smb_url set, subpath = ""
    fake_tools(scenario(mount, selected=1, report=[row("asset-1")]))

    cli.main(["send", "--config", str(config)])
    captured = capsys.readouterr()

    assert "no per-Mac subpath is set" in captured.err


def test_send_remove_originals_after_clean_export(tmp_path: Path, fake_tools, monkeypatch, capsys):
    from photos_tool.remove import RemoveResult

    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    fake_tools(scenario(mount, selected=2, report=[row("a"), row("b")]))

    seen: dict[str, object] = {}

    def fake_remove(uuids, *, dry_run=False, max_delete=500):
        ids = sorted(uuids)
        seen["uuids"] = ids
        return RemoveResult(requested=len(ids), deleted=len(ids), dry_run=dry_run)

    monkeypatch.setattr(cli, "remove_originals", fake_remove)

    rc = cli.main(["send", "--config", str(config), "--remove-originals", "--yes"])
    captured = capsys.readouterr()

    assert rc == 0
    assert seen["uuids"] == ["a", "b"]
    assert "Moved 2 original(s) to Recently Deleted" in captured.out
    assert (tmp_path / "state" / "logs" / "removed.jsonl").exists()


def test_send_remove_originals_blocked_when_not_clean(tmp_path: Path, fake_tools, monkeypatch):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    fake_tools(
        scenario(
            mount,
            selected=2,
            report=[row("a"), row("b", exported=False, new=False, missing=True)],
        )
    )

    called: list[object] = []
    monkeypatch.setattr(cli, "remove_originals", lambda *a, **k: called.append(1))

    rc = cli.main(["send", "--config", str(config), "--remove-originals", "--yes"])

    # A skipped/missing export exits 3 and must never trigger a delete.
    assert rc == 3
    assert called == []


def test_send_remove_dry_run_deletes_nothing(tmp_path: Path, fake_tools, monkeypatch, capsys):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    fake_tools(scenario(mount, selected=1, report=[row("a")]))

    from photos_tool.remove import RemoveResult

    calls: list[bool] = []

    def fake_remove(uuids, *, dry_run=False, max_delete=500):
        calls.append(dry_run)
        ids = sorted(uuids)
        return RemoveResult(requested=len(ids), deleted=0 if dry_run else len(ids), dry_run=dry_run)

    monkeypatch.setattr(cli, "remove_originals", fake_remove)

    rc = cli.main(
        ["send", "--config", str(config), "--remove-originals", "--remove-dry-run", "--yes"]
    )
    captured = capsys.readouterr()

    assert rc == 0
    # The dry run verifies resolvability with dry_run=True and deletes nothing.
    assert calls == [True]
    assert "Remove dry run" in captured.out


def test_doctor_runs_fake_preflight_and_dry_run(tmp_path: Path, fake_tools, capsys):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    fake_tools(scenario(mount, selected=1, dry_report=[row("asset-1")]))

    rc = cli.main(["doctor", "--config", str(config)])
    captured = capsys.readouterr()

    assert rc == 0
    assert "[pass] destination writable" in captured.out
    assert "Optimize Storage dry-run risk: 0%" in captured.out
    assert (tmp_path / "state" / "exportdb").is_dir()


def test_doctor_does_not_claim_zero_risk_without_a_selection(tmp_path: Path, fake_tools, capsys):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    fake_tools(scenario(mount, selected=0))

    rc = cli.main(["doctor", "--config", str(config)])
    captured = capsys.readouterr()

    assert rc == 0
    assert "[info] Optimize Storage risk" in captured.out
    assert "Optimize Storage dry-run risk: 0%" not in captured.out


def test_send_surfaces_exiftool_errors_without_failing(tmp_path: Path, fake_tools, capsys):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    fake_tools(
        scenario(
            mount,
            selected=1,
            report=[{**row("asset-1"), "exiftool_error": True}],
        )
    )

    rc = cli.main(["send", "--config", str(config)])
    captured = capsys.readouterr()

    assert rc == 0
    assert "metadata embedding reported" in captured.err
    log_line = (tmp_path / "state" / "logs" / "runs.jsonl").read_text(encoding="utf-8")
    assert '"exiftool_error": 1' in log_line


def test_send_rejects_concurrent_run_for_same_destination(tmp_path: Path, fake_tools, capsys):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    tools = fake_tools(scenario(mount, selected=1, report=[row("asset-1")]))

    exportdb_dir = tmp_path / "state" / "exportdb"
    held = cli._acquire_destination_lock(exportdb_dir, mount)
    assert held is not None
    try:
        rc = cli.main(["send", "--config", str(config)])
    finally:
        held.close()
    captured = capsys.readouterr()

    assert rc == 1
    assert "already running" in captured.err
    assert osxphotos_exports(tools.log()) == []
