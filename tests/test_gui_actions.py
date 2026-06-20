from __future__ import annotations

from photos_tool import cli
from photos_tool.gui_actions import (
    CleanupQuery,
    build_send_argv,
    confirm_delete_message,
    confirm_reveal_message,
    map_exit_code,
    parse_cleanup_query,
    status_glyph,
)


def test_build_send_argv_defaults_to_selected():
    # JPEG/MP4 are config-only now: argv never carries copy flags.
    assert build_send_argv("photos-tool") == ["photos-tool", "send"]


def test_build_send_argv_album_and_config_only():
    argv = build_send_argv("pt", album="Summer Trip", config="/c.toml")
    assert argv == ["pt", "send", "--album", "Summer Trip", "--config", "/c.toml"]


def test_build_send_argv_has_no_copy_flags():
    argv = build_send_argv("pt", album="A")
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


def test_parse_cleanup_query_reads_count_and_reveal():
    q = parse_cleanup_query('{"count": 3, "destination": "/v", "reveal": "/v/2024/a.heic"}')
    assert q == CleanupQuery(3, "/v/2024/a.heic")


def test_parse_cleanup_query_empty_is_zero():
    assert parse_cleanup_query("") == CleanupQuery(0, "")
    assert parse_cleanup_query("   ") == CleanupQuery(0, "")


def test_parse_cleanup_query_garbled_is_zero_not_crash():
    # A transient CLI hiccup must never be read as "things to delete".
    assert parse_cleanup_query("not json at all") == CleanupQuery(0, "")
    assert parse_cleanup_query("[1, 2, 3]") == CleanupQuery(0, "")
    assert parse_cleanup_query('{"count": "oops"}') == CleanupQuery(0, "")


def test_parse_cleanup_query_negative_count_clamped():
    assert parse_cleanup_query('{"count": -5}') == CleanupQuery(0, "")


def test_parse_cleanup_query_missing_reveal_is_empty():
    assert parse_cleanup_query('{"count": 2}') == CleanupQuery(2, "")


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
    assert menubar._executable()
