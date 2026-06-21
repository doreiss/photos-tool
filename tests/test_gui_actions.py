from __future__ import annotations

from photos_tool import cli
from photos_tool.gui_actions import (
    CleanupQuery,
    build_send_argv,
    confirm_delete_message,
    confirm_reveal_message,
    map_exit_code,
    parse_cleanup_query,
    send_action_for_automation_status,
    status_glyph,
)


def test_build_send_argv_defaults_to_selected():
    # The prefix is a list: dev (one element) and frozen (.app: python -m photos_tool) both
    # splat uniformly. JPEG/MP4 are config-only, so argv never carries copy flags.
    assert build_send_argv(["photos-tool"]) == ["photos-tool", "send"]
    assert build_send_argv(["/p/python", "-m", "photos_tool"]) == [
        "/p/python",
        "-m",
        "photos_tool",
        "send",
    ]


def test_build_send_argv_album_and_config_only():
    argv = build_send_argv(["pt"], album="Summer Trip", config="/c.toml")
    assert argv == ["pt", "send", "--album", "Summer Trip", "--config", "/c.toml"]


def test_build_send_argv_has_no_copy_flags():
    for prefix in (["pt"], ["/p/python", "-m", "photos_tool"]):
        argv = build_send_argv(prefix, album="A")
        assert "--jpeg" not in argv
        assert "--no-jpeg" not in argv
        assert "--mp4" not in argv
        assert "--no-mp4" not in argv


def test_map_exit_code_covers_every_known_code():
    for code in (
        cli.EXIT_OK,
        cli.EXIT_PREFLIGHT,
        cli.EXIT_USAGE,
        cli.EXIT_RECONCILE,
        cli.EXIT_UNVERIFIED,
        cli.EXIT_NOTHING_SELECTED,
        cli.EXIT_CONVERSION,
    ):
        note = map_exit_code(code)
        assert note.title and note.message


def test_map_exit_code_wording_matches_intent():
    assert "sent" in map_exit_code(cli.EXIT_OK).title.lower()
    assert "skipped" in map_exit_code(cli.EXIT_RECONCILE).title.lower()
    assert "nothing" in map_exit_code(cli.EXIT_NOTHING_SELECTED).title.lower()


def test_map_exit_code_unknown_is_generic_failure():
    note = map_exit_code(99)
    assert "fail" in note.title.lower()
    assert "99" in note.message


def test_status_glyph_distinguishes_states():
    assert status_glyph(cli.EXIT_OK) == "📷✓"
    assert status_glyph(cli.EXIT_PREFLIGHT) == "📷⚠️"
    assert status_glyph(None) == "📷✕"
    # ok, non-ok, and launch-error are all visually distinct.
    assert len({status_glyph(0), status_glyph(1), status_glyph(None)}) == 3


def test_idle_working_and_result_glyphs_are_all_distinct():
    from photos_tool.gui_actions import IDLE_GLYPH, WORKING_GLYPH

    # Five distinct title states (idle, working, ok, warn, error). The working glyph
    # being distinct is what makes a "stuck on working" bug visible — which is why a
    # non-send job must restore the idle/last-send glyph when it finishes.
    glyphs = {IDLE_GLYPH, WORKING_GLYPH, status_glyph(0), status_glyph(1), status_glyph(None)}
    assert len(glyphs) == 5


def test_parse_cleanup_query_reads_count_and_reveal_list():
    # The whole batch (multiple files, multiple folders) is revealed, not one file.
    q = parse_cleanup_query(
        '{"count": 3, "destination": "/v", "reveal": ["/v/2024/08/a.heic", "/v/2024/09/b.mov"]}'
    )
    assert q == CleanupQuery(3, ("/v/2024/08/a.heic", "/v/2024/09/b.mov"))


def test_parse_cleanup_query_tolerates_legacy_single_string_reveal():
    # An older CLI emitted reveal as one path string; keep parsing it.
    q = parse_cleanup_query('{"count": 1, "reveal": "/v/2024/01/a.heic"}')
    assert q == CleanupQuery(1, ("/v/2024/01/a.heic",))


def test_parse_cleanup_query_caps_reveal_list():
    import json

    from photos_tool.gui_actions import REVEAL_CAP

    paths = [f"/v/2024/{i:03d}.heic" for i in range(REVEAL_CAP + 10)]
    q = parse_cleanup_query(json.dumps({"count": len(paths), "reveal": paths}))
    # Count is the true total; the reveal list is capped to a sane spot-check size.
    assert q.count == REVEAL_CAP + 10
    assert len(q.reveal) == REVEAL_CAP


def test_parse_cleanup_query_empty_is_zero():
    assert parse_cleanup_query("") == CleanupQuery(0, ())
    assert parse_cleanup_query("   ") == CleanupQuery(0, ())


def test_parse_cleanup_query_garbled_is_zero_not_crash():
    # A transient CLI hiccup must never be read as "things to delete".
    assert parse_cleanup_query("not json at all") == CleanupQuery(0, ())
    assert parse_cleanup_query("[1, 2, 3]") == CleanupQuery(0, ())
    assert parse_cleanup_query('{"count": "oops"}') == CleanupQuery(0, ())


def test_parse_cleanup_query_negative_count_clamped():
    assert parse_cleanup_query('{"count": -5}') == CleanupQuery(0, ())


def test_parse_cleanup_query_missing_reveal_is_empty():
    assert parse_cleanup_query('{"count": 2}') == CleanupQuery(2, ())


def test_parse_cleanup_query_drops_non_string_reveal_entries():
    q = parse_cleanup_query('{"count": 1, "reveal": ["/v/a.heic", 5, null, "/v/b.mov"]}')
    assert q == CleanupQuery(1, ("/v/a.heic", "/v/b.mov"))


def test_cleanup_messages_mention_count_and_recovery():
    reveal = confirm_reveal_message(4)
    assert "4" in reveal.message
    delete = confirm_delete_message(4)
    assert "4" in delete.message
    # The deletion prompt must promise recoverability (30-day Recently Deleted).
    assert "recover" in delete.message.lower()
    assert "30" in delete.message


def test_menubar_imports_without_the_gui_extra():
    # rumps is imported lazily inside main(), so the module must import on a plain
    # install (and on Linux CI) without the optional gui dependency present.
    import photos_tool.menubar as menubar

    assert callable(menubar.main)
    prefix = menubar._cli_prefix()
    assert isinstance(prefix, list) and prefix and all(isinstance(p, str) for p in prefix)


def test_cli_prefix_self_reinvokes_under_pyinstaller(monkeypatch):
    # In a PyInstaller bundle, the CLI child is the app's OWN frozen binary (+ a sentinel), so
    # osxphotos/PhotoKit run inside the app's code signature ("photos-tool" TCC identity).
    import photos_tool.menubar as menubar

    exe = "/Applications/photos-tool.app/Contents/MacOS/photos-tool"
    monkeypatch.setattr(menubar.sys, "frozen", True, raising=False)
    monkeypatch.setattr(menubar.sys, "_MEIPASS", "/tmp/meipass", raising=False)
    monkeypatch.setattr(menubar.sys, "executable", exe)
    assert menubar._cli_prefix() == [exe, "--pyi-cli"]


def test_maybe_dispatch_reinvocation_routes_cli_and_none(monkeypatch):
    # The frozen app shells out to itself; --pyi-cli must hand the remaining argv to cli.main
    # (so a sentinel-string typo vs the argv builders is caught), and no sentinel -> None.
    import photos_tool.menubar as menubar

    seen = {}

    def fake_cli_main(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr("photos_tool.cli.main", fake_cli_main)
    assert menubar._maybe_dispatch_reinvocation(["--pyi-cli", "doctor", "--json"]) == 0
    assert seen["argv"] == ["doctor", "--json"]
    assert menubar._maybe_dispatch_reinvocation(["something-else"]) is None
    assert menubar._maybe_dispatch_reinvocation([]) is None


def test_maybe_dispatch_reinvocation_routes_osxphotos(monkeypatch):
    import runpy

    import photos_tool.menubar as menubar

    calls = {}

    def fake_run_module(mod, run_name):
        calls["mod"] = mod
        calls["argv"] = list(menubar.sys.argv)

    monkeypatch.setattr(menubar.sys, "argv", ["orig"])  # registered so monkeypatch restores it
    monkeypatch.setattr(runpy, "run_module", fake_run_module)
    assert menubar._maybe_dispatch_reinvocation(["--pyi-osxphotos", "query", "--count"]) == 0
    assert calls["mod"] == "osxphotos.__main__"
    assert calls["argv"] == ["osxphotos", "query", "--count"]


def test_send_action_routes_automation_status():
    # The other half of the Automation bug fix: a flipped comparison (routing a granted 0 to
    # Settings, or a -1743 denial into a silently-empty send) would ship unnoticed without this.
    assert send_action_for_automation_status(0) == "send"  # granted
    assert send_action_for_automation_status(None) == "send"  # could-not-ask -> best effort
    assert send_action_for_automation_status(-1743) == "open_settings"  # declined
    assert send_action_for_automation_status(-1744) == "open_settings"  # unpresentable
    assert send_action_for_automation_status(-600) == "open_settings"  # Photos not running


def test_maybe_dispatch_reinvocation_routes_prime_photos(monkeypatch):
    # The frozen app primes/verifies the "control Photos" grant from its OWN signed identity
    # via a sentinel; the dispatch must call request_photos_automation and exit 0 (the OSStatus
    # is printed, never used as the exit code).
    import photos_tool.menubar as menubar

    called = {}

    def fake_request():
        called["yes"] = True
        return 0

    monkeypatch.setattr(menubar, "request_photos_automation", fake_request)
    assert menubar._maybe_dispatch_reinvocation(["--pyi-prime-photos"]) == 0
    assert called.get("yes") is True


def test_build_connect_argv_includes_json_and_force():
    from photos_tool.gui_actions import build_connect_argv

    argv = build_connect_argv(["pt"], "smb://pc/Share", config="/c.toml")
    assert argv == [
        "pt",
        "connect",
        "--smb-url",
        "smb://pc/Share",
        "--config",
        "/c.toml",
        "--json",
        "--force",
    ]
    # The list prefix splats for the frozen (.app) invocation too.
    frozen = build_connect_argv(["/p/python", "-m", "photos_tool"], "smb://x/y")
    assert frozen[:4] == ["/p/python", "-m", "photos_tool", "connect"]


def test_parse_connect_result_ok_failure_and_garble():
    from photos_tool.gui_actions import ConnectResult, parse_connect_result

    ok = parse_connect_result('{"ok": true, "destination": "/Volumes/Share/MacA"}')
    assert ok == ConnectResult(True, "/Volumes/Share/MacA", "")
    bad = parse_connect_result('{"ok": false, "error": "nope"}')
    assert bad == ConnectResult(False, "", "nope")
    # Any garble is a failure, never "ok" (a transient hiccup must not look like success).
    assert parse_connect_result("not json").ok is False
    assert parse_connect_result("[1, 2]").ok is False
    assert parse_connect_result("").ok is False


def test_connect_success_message_surfaces_full_disk_access():
    # FDA is the one grant macOS never prompts for, and a hard prerequisite — onboarding must
    # name it (alongside the destination) so a new user enables it before the first Send.
    from photos_tool.gui_actions import connect_success_message

    note = connect_success_message("/Volumes/Share/MacA")
    assert "/Volumes/Share/MacA" in note.message
    assert "Full Disk Access" in note.message
