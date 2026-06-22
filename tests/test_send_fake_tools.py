from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

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


def write_config(
    tmp_path: Path,
    mount_point: Path,
    *,
    jpeg: bool = False,
    mp4: bool = False,
    use_photokit: bool = False,
) -> Path:
    # Preferences are config-only now, so tests configure JPEG/MP4/PhotoKit here.
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
use_photokit = {str(use_photokit).lower()}
retry = 3

[copies]
jpeg = {str(jpeg).lower()}
jpeg_quality = 0.9
mp4 = {str(mp4).lower()}
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


def test_send_success_persists_last_run_and_exportdb(tmp_path: Path, fake_tools, capsys):
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
    # No timestamped report copies are written anymore (they were write-only and leaked
    # GPS/paths); the single last-run.json is the only persisted run state.
    assert not list((tmp_path / "state" / "logs").glob("*-report.json"))
    assert (tmp_path / "state" / "logs" / "last-run.json").exists()


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
    assert '"status":' in captured.out


def test_send_last_report_handles_corrupt_record(tmp_path: Path, capsys):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    log_dir = tmp_path / "state" / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / "last-run.json").write_text("{not-json", encoding="utf-8")

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
    # A non-clean reconcile must arm NO cleanup, even though asset-1 genuinely exported: a later
    # cleanup-last could otherwise delete originals from a batch the user was told to re-send.
    from photos_tool import state

    assert state.load_backup_token(tmp_path / "state" / "logs", mount) is None


def test_send_reports_unverified_when_reconciled_copies_are_absent_on_share(
    tmp_path: Path, fake_tools, capsys
):
    # The flagship "misleading success": a stale export DB + a wiped/restored share makes
    # --update write nothing yet report the asset as "skipped" (== already present), so reconcile
    # would say OK. send must re-fingerprint the share, find the copy gone, and report NOT-fully-
    # backed-up (EXIT_UNVERIFIED) with no token -- never a false "Backup complete."
    from photos_tool import state

    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    skipped_absent = {
        **row("a", exported=False, new=False, skipped=True),
        "filename": str(mount / "2024/01/a.heic"),
    }
    fake_tools(scenario(mount, selected=1, report=[skipped_absent], files=[]))  # nothing landed

    rc = cli.main(["send", "--config", str(config)])
    assert rc == cli.EXIT_UNVERIFIED
    assert "NOT fully backed up" in capsys.readouterr().err
    # The token records only share-verified copies (none here), so cleanup offers nothing.
    token = state.load_backup_token(tmp_path / "state" / "logs", mount)
    assert token is not None and token.assets == ()


def test_send_exits_three_when_report_has_error_rows(tmp_path: Path, fake_tools):
    from photos_tool import state

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
    assert state.load_backup_token(tmp_path / "state" / "logs", mount) is None


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
    assert (tmp_path / "state" / "logs" / "last-run.json").exists()


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
    config = write_config(tmp_path, mount, use_photokit=True)
    tools = fake_tools(scenario(mount, selected=1, report=[row("asset-1")]))

    rc = cli.main(["send", "--config", str(config)])
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
    config = write_config(tmp_path, mount, jpeg=True)
    tools = fake_tools(
        scenario(
            mount,
            selected=1,
            report=[row("asset-1")],
            jpeg_report=[row("asset-1", converted=True)],
            jpeg_files=["2024/01/IMG_0001.jpg"],
        )
    )

    rc = cli.main(["send", "--config", str(config)])
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
    config = write_config(tmp_path, mount, jpeg=True)
    fake_tools(
        scenario(
            mount,
            selected=1,
            report=[row("asset-1")],
            jpeg_returncode=1,
        )
    )

    rc = cli.main(["send", "--config", str(config)])
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
    config = write_config(tmp_path, mount, jpeg=True)
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

    rc = cli.main(["send", "--config", str(config)])
    captured = capsys.readouterr()

    assert rc == 0
    assert "JPEG compatibility export error" not in captured.err


def test_send_mp4_transcodes_standalone_hevc_only(tmp_path: Path, fake_tools):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount, mp4=True)
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

    assert cli.main(["send", "--config", str(config)]) == 0
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
    # exiftool tags the same-dir temp; the final .mp4 is atomically published only after every step.
    assert exiftool_calls[0][-1].endswith("VID_0002.partial.mp4")
    assert (mount / "compat" / "2024" / "01" / "VID_0002.mp4").exists()


def test_send_jpeg_mp4_transcodes_from_main_tree_into_compat(tmp_path: Path, fake_tools):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount, jpeg=True, mp4=True)
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

    assert cli.main(["send", "--config", str(config)]) == 0

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
    # ffmpeg writes a same-dir temp; the final .mp4 is published atomically once tagging succeeds.
    assert output.endswith("/compat/2024/01/VID_0002.partial.mp4")
    assert (mount / "compat" / "2024" / "01" / "VID_0002.mp4").exists()
    # The compat pass never created a video to re-transcode.
    assert not (mount / "compat" / "2024" / "01" / "VID_0002.MOV").exists()


def test_send_mp4_conversion_error_exits_five(tmp_path: Path, fake_tools, capsys):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount, mp4=True)
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

    rc = cli.main(["send", "--config", str(config)])
    captured = capsys.readouterr()

    assert rc == 5
    assert "conversion error" in captured.err
    # A conversion failure exits before the token is recorded, so cleanup is never armed for a
    # run the user must re-do (the safe direction: originals are kept until a clean send).
    from photos_tool import state

    assert state.load_backup_token(tmp_path / "state" / "logs", mount) is None


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


def _row_at(uuid: str, dest: Path, rel: str) -> dict[str, Any]:
    # A report row whose destination filename is the actual copy written on the share.
    return {**row(uuid), "filename": str(dest / rel)}


def test_send_records_a_backup_token(tmp_path: Path, fake_tools):
    from photos_tool import state

    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    files = ["2024/01/a.heic", "2024/02/b.heic"]
    fake_tools(
        scenario(
            mount,
            selected=2,
            report=[_row_at("a", mount, files[0]), _row_at("b", mount, files[1])],
            files=files,
        )
    )

    assert cli.main(["send", "--config", str(config)]) == 0

    token = state.load_backup_token(tmp_path / "state" / "logs", mount)
    assert token is not None
    assert sorted(asset.uuid for asset in token.assets) == ["a", "b"]
    # The recorded size matches the bytes the fake wrote ("fake" == 4 bytes).
    assert all(f.size == 4 for asset in token.assets for f in asset.files)


def test_send_with_derived_report_cannot_disagree_with_disk(tmp_path: Path, fake_tools, capsys):
    # F14: with no explicit report, the fake DERIVES the report from the files it writes, so
    # the token records exactly what is on disk (the report can't silently disagree).
    from photos_tool import state

    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    files = ["2024/01/a.heic", "2024/02/b.mov"]
    s = scenario(mount, selected=2, files=files)
    del s["report"]  # force the derive-from-files path
    fake_tools(s)

    assert cli.main(["send", "--config", str(config)]) == 0
    capsys.readouterr()

    token = state.load_backup_token(tmp_path / "state" / "logs", mount)
    assert token is not None
    recorded = sorted(f.path for asset in token.assets for f in asset.files)
    assert recorded == sorted(str(mount / rel) for rel in files)


def test_send_warns_and_drops_a_partial_live_photo(tmp_path: Path, fake_tools, capsys):
    # F5 at the CLI: a Live Photo whose MOV never lands is dropped WHOLE from the token, and
    # the user is told a photo could not be verified (never silently excluded).
    from photos_tool import state

    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    heic, mov = "2024/08/live.heic", "2024/08/live.mov"
    fake_tools(
        scenario(
            mount,
            selected=1,
            report=[_row_at("live", mount, heic), _row_at("live", mount, mov)],
            files=[heic],  # the MOV never lands on the share
        )
    )

    # The MOV is absent on the share, so this is NOT a clean backup: the whole Live Photo is
    # dropped from the token AND the send reports it (no false "Backup complete").
    assert cli.main(["send", "--config", str(config)]) == cli.EXIT_UNVERIFIED
    assert "NOT fully backed up" in capsys.readouterr().err

    token = state.load_backup_token(tmp_path / "state" / "logs", mount)
    assert token is not None and token.assets == ()  # whole Live Photo dropped


@pytest.mark.parametrize("drop", ["filename", "exported", "missing", "error"])
def test_send_fails_closed_when_any_required_column_is_missing(
    tmp_path: Path, fake_tools, capsys, drop: str
):
    # F9: a report missing ANY required column must fail closed (EXIT_PREFLIGHT) and record NO
    # token, rather than read the absent column as False and trust the row.
    from photos_tool import state

    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    full = {
        "uuid": "a",
        "filename": str(mount / "2024/01/a.heic"),
        "exported": True,
        "missing": False,
        "error": False,
    }
    row_without = {k: v for k, v in full.items() if k != drop}
    fake_tools(scenario(mount, selected=1, report=[row_without], files=["2024/01/a.heic"]))

    rc = cli.main(["send", "--config", str(config)])
    assert rc == cli.EXIT_PREFLIGHT
    assert "missing required column" in capsys.readouterr().err
    assert state.load_backup_token(tmp_path / "state" / "logs", mount) is None


def test_send_drops_asset_with_an_empty_filename_constituent(tmp_path: Path, fake_tools, capsys):
    # An exported constituent with an EMPTY filename can't be verified; the WHOLE asset is
    # dropped from the token (never recorded as a complete-looking single-file asset).
    from photos_tool import state

    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    heic = "2024/08/live.heic"
    rows = [
        _row_at("live", mount, heic),
        {**row("live"), "filename": ""},  # positively exported, but no filename
    ]
    fake_tools(scenario(mount, selected=1, report=rows, files=[heic]))

    # The empty-filename constituent can't be verified -> whole asset dropped AND the send is
    # reported as not fully backed up (not a silent "complete").
    assert cli.main(["send", "--config", str(config)]) == cli.EXIT_UNVERIFIED
    assert "NOT fully backed up" in capsys.readouterr().err
    token = state.load_backup_token(tmp_path / "state" / "logs", mount)
    assert token is not None and token.assets == ()


def _empty_smb_config(tmp_path: Path, mount: Path) -> Path:
    config = tmp_path / "config-nosmb.toml"
    config.write_text(
        f"""
[destination]
smb_url = ""
mount_point = {str(mount)!r}
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


def test_on_boot_volume_detects_the_root_device():
    from pathlib import Path as _P

    assert cli._on_boot_volume(_P("/")) is True
    # A nonexistent path resolves to its nearest existing parent (root) -> boot volume.
    assert cli._on_boot_volume(_P("/no-such-dir-xyzzy/sub")) is True


def test_boot_volume_destination_refused_for_send_and_cleanup(
    tmp_path: Path, fake_tools, capsys, monkeypatch
):
    # F8: with an empty smb_url (manual-mount config) there is no mount check, so a stale
    # boot-disk path would look ready. Both send AND cleanup must refuse the boot volume.
    mount = tmp_path / "share"
    mount.mkdir()
    config = _empty_smb_config(tmp_path, mount)
    fake_tools(
        scenario(
            mount,
            selected=1,
            report=[_row_at("a", mount, "2024/01/a.heic")],
            files=["2024/01/a.heic"],
        )
    )

    # Force the boot-volume reading deterministically (portable across macOS/Linux CI).
    monkeypatch.setattr(cli, "_on_boot_volume", lambda p: True)
    assert cli.main(["send", "--config", str(config)]) == cli.EXIT_PREFLIGHT
    assert "boot disk" in capsys.readouterr().err

    # Off the boot volume, the same send proceeds and records a token.
    monkeypatch.setattr(cli, "_on_boot_volume", lambda p: False)
    assert cli.main(["send", "--config", str(config)]) == 0
    capsys.readouterr()

    # Cleanup shares _ensure_destination_ready, so it refuses the boot volume too.
    monkeypatch.setattr(cli, "_on_boot_volume", lambda p: True)
    assert cli.main(["cleanup-last", "--config", str(config), "--yes"]) == cli.EXIT_PREFLIGHT
    assert "boot disk" in capsys.readouterr().err


def test_connect_mounts_then_writes_config(tmp_path: Path, fake_tools, capsys):
    # The no-Terminal onboarding: connect triggers the (faked) native mount, then writes
    # a usable config pointing at the share.
    from photos_tool import config as cfg

    mount = tmp_path / "FamilyPhotos"
    mount.mkdir()
    config = tmp_path / "config.toml"
    fake_tools(scenario(mount, initial_mounted=False))  # mounts only once osascript runs

    rc = cli.main(
        [
            "connect",
            "--smb-url",
            "smb://pc/FamilyPhotos",
            "--mount-point",
            str(mount),
            "--subpath",
            "MacA",
            "--config",
            str(config),
            "--json",
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True and out["subpath"] == "MacA"
    assert config.exists()
    loaded = cfg.load_config(str(config))
    assert loaded.destination.smb_url == "smb://pc/FamilyPhotos"
    assert str(mount) in loaded.destination.mount_point


def test_connect_writes_no_config_when_the_share_cannot_mount(tmp_path: Path, fake_tools, capsys):
    mount = tmp_path / "FamilyPhotos"
    mount.mkdir()
    config = tmp_path / "config.toml"
    fake_tools(scenario(mount, initial_mounted=False, mount_fail=True))

    rc = cli.main(
        [
            "connect",
            "--smb-url",
            "smb://pc/FamilyPhotos",
            "--mount-point",
            str(mount),
            "--config",
            str(config),
            "--json",
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == cli.EXIT_PREFLIGHT
    assert out["ok"] is False and out["error"]
    assert not config.exists()  # a bad/unreachable share never leaves a broken config


def test_connect_rejects_a_bad_url_without_mounting(tmp_path: Path, capsys):
    config = tmp_path / "config.toml"
    rc = cli.main(["connect", "--smb-url", "not-a-real-url", "--config", str(config), "--json"])
    out = json.loads(capsys.readouterr().out)
    assert rc == cli.EXIT_USAGE
    assert out["ok"] is False
    assert not config.exists()


def test_connect_refuses_to_overwrite_config_without_force(tmp_path: Path, fake_tools, capsys):
    mount = tmp_path / "FamilyPhotos"
    mount.mkdir()
    config = write_config(tmp_path, mount)  # a config already exists
    fake_tools(scenario(mount, initial_mounted=False))

    rc = cli.main(
        [
            "connect",
            "--smb-url",
            "smb://pc/FamilyPhotos",
            "--mount-point",
            str(mount),
            "--config",
            str(config),
            "--json",
        ]
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == cli.EXIT_USAGE
    assert out["ok"] is False and "already exists" in out["error"]


def test_osxphotos_argv_self_reinvokes_only_in_the_frozen_app(monkeypatch):
    # In the frozen .app, osxphotos runs via the app's OWN binary (self-reinvocation); in
    # dev/CI it stays the plain `osxphotos` on PATH (so the fake-tool tests keep working).
    from photos_tool import osxphotos_runner as runner

    # Dev (not frozen): unchanged.
    assert runner._osxphotos_argv(["osxphotos", "query"]) == ["osxphotos", "query"]
    # Frozen PyInstaller app: rewrite to the app's own binary + sentinel.
    monkeypatch.setattr(runner.sys, "frozen", True, raising=False)
    monkeypatch.setattr(runner.sys, "_MEIPASS", "/tmp/meipass", raising=False)
    exe = "/Applications/photos-tool.app/Contents/MacOS/photos-tool"
    monkeypatch.setattr(runner.sys, "executable", exe)
    assert runner._osxphotos_argv(["osxphotos", "query", "--count"]) == [
        exe,
        "--pyi-osxphotos",
        "query",
        "--count",
    ]
    # Non-osxphotos commands are never rewritten, even when frozen.
    assert runner._osxphotos_argv(["ffmpeg", "-i", "x"]) == ["ffmpeg", "-i", "x"]


def test_probe_finds_osxphotos_in_the_frozen_app(monkeypatch):
    # In the frozen .app osxphotos has no PATH binary; the probe must still find it via
    # self-reinvocation, or send's tool preflight would wrongly block.
    from photos_tool import osxphotos_runner, tooling

    exe = "/Applications/photos-tool.app/Contents/MacOS/photos-tool"
    monkeypatch.setattr(osxphotos_runner.sys, "frozen", True, raising=False)
    monkeypatch.setattr(osxphotos_runner.sys, "_MEIPASS", "/tmp/meipass", raising=False)
    monkeypatch.setattr(osxphotos_runner.sys, "executable", exe)
    # Don't actually run the version command; just confirm the probe resolves to the app binary.
    monkeypatch.setattr(tooling, "_query_version", lambda argv: "osxphotos, version 0.76.1")
    osx_tool = next(t for t in tooling.REQUIRED_TOOLS if t.name == "osxphotos")
    status = tooling.probe(osx_tool)
    assert status.found and status.path == exe and status.version


def _record_a_backup(tmp_path: Path, mount: Path, fake_tools) -> Path:
    config = write_config(tmp_path, mount)
    fake_tools(
        scenario(
            mount,
            selected=1,
            report=[_row_at("a", mount, "2024/01/a.heic")],
            files=["2024/01/a.heic"],
        )
    )
    assert cli.main(["send", "--config", str(config)]) == 0
    return config


def test_cleanup_last_json_counts_copies_present_on_share(tmp_path: Path, fake_tools, capsys):
    mount = tmp_path / "share"
    mount.mkdir()
    config = _record_a_backup(tmp_path, mount, fake_tools)
    capsys.readouterr()  # discard the send output

    assert cli.main(["cleanup-last", "--config", str(config), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["count"] == 1
    assert isinstance(data["reveal"], list)
    assert len(data["reveal"]) == 1
    assert data["reveal"][0].endswith("2024/01/a.heic")


def test_cleanup_last_json_count_zero_when_copy_gone(tmp_path: Path, fake_tools, capsys):
    mount = tmp_path / "share"
    mount.mkdir()
    config = _record_a_backup(tmp_path, mount, fake_tools)
    # The recorded copy vanishes from the share before cleanup.
    (mount / "2024" / "01" / "a.heic").unlink()
    capsys.readouterr()

    assert cli.main(["cleanup-last", "--config", str(config), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["count"] == 0


def test_cleanup_last_removes_then_clears_token(tmp_path, fake_tools, monkeypatch, capsys):
    from photos_tool import state
    from photos_tool.remove import RemoveResult

    mount = tmp_path / "share"
    mount.mkdir()
    config = _record_a_backup(tmp_path, mount, fake_tools)

    seen: dict[str, object] = {}

    def fake_remove(uuids, *, dry_run=False, max_delete=500):
        ids = sorted(uuids)
        seen["uuids"] = ids
        return RemoveResult(requested=len(ids), deleted=len(ids), dry_run=dry_run)

    monkeypatch.setattr(cli, "remove_originals", fake_remove)

    rc = cli.main(["cleanup-last", "--config", str(config), "--yes"])
    captured = capsys.readouterr()

    assert rc == 0
    assert seen["uuids"] == ["a"]
    assert "Moved 1 original(s) to Recently Deleted" in captured.out
    # The token is consumed, so the same batch can never be offered for deletion again.
    assert state.load_backup_token(tmp_path / "state" / "logs", mount) is None


def test_cleanup_last_refuses_without_yes_when_non_interactive(
    tmp_path, fake_tools, monkeypatch, capsys
):
    # The one destructive command must NOT delete when run without a tty (a Shortcut, cron, or
    # the menu-bar worker) unless --yes is explicit. The gate guards remove_originals entirely.
    import io

    from photos_tool import state

    mount = tmp_path / "share"
    mount.mkdir()
    config = _record_a_backup(tmp_path, mount, fake_tools)  # arms a real, removable token
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO())  # isatty() -> False (non-interactive)

    def fake_remove(*args, **kwargs):
        raise AssertionError("remove_originals must not run without --yes on a non-tty")

    monkeypatch.setattr(cli, "remove_originals", fake_remove)

    rc = cli.main(["cleanup-last", "--config", str(config)])  # no --yes
    assert rc == cli.EXIT_USAGE
    assert "--yes" in capsys.readouterr().err
    # The token is untouched, so a later interactive run can still offer the same batch.
    assert state.load_backup_token(tmp_path / "state" / "logs", mount) is not None


def test_cleanup_last_without_a_recorded_backup_errors(tmp_path: Path, capsys):
    config = write_config(tmp_path, tmp_path / "share")

    rc = cli.main(["cleanup-last", "--config", str(config)])

    assert rc == 2
    assert "No backup recorded" in capsys.readouterr().err


def test_send_rejects_a_report_without_uuids_without_crashing(tmp_path: Path, fake_tools, capsys):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    no_uuid = [
        {"filename": str(mount / "x.heic"), "exported": True, "missing": False, "error": False}
    ]
    fake_tools(scenario(mount, selected=1, report=no_uuid, files=[]))

    rc = cli.main(["send", "--config", str(config)])

    assert rc == 1
    assert "report error" in capsys.readouterr().err


def test_send_dry_run_rejects_a_report_without_uuids(tmp_path: Path, fake_tools, capsys):
    mount = tmp_path / "share"
    mount.mkdir()
    config = write_config(tmp_path, mount)
    no_uuid = [{"filename": "/x", "exported": True, "missing": False, "error": False}]
    fake_tools(scenario(mount, selected=1, dry_report=no_uuid))

    rc = cli.main(["send", "--config", str(config), "--dry-run"])

    assert rc == 1
    assert "report error" in capsys.readouterr().err


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
    record = (tmp_path / "state" / "logs" / "last-run.json").read_text(encoding="utf-8")
    assert '"exiftool_error": 1' in record


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
