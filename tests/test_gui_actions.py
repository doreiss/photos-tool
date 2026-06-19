from __future__ import annotations

from photos_tool import cli
from photos_tool.gui_actions import build_send_argv, map_exit_code


def test_build_send_argv_defaults_to_selected_no_copies():
    assert build_send_argv("photos-tool") == ["photos-tool", "send", "--no-jpeg", "--no-mp4"]


def test_build_send_argv_album_toggles_and_config():
    argv = build_send_argv("pt", album="Summer Trip", jpeg=True, mp4=True, config="/c.toml")
    assert argv == [
        "pt",
        "send",
        "--album",
        "Summer Trip",
        "--jpeg",
        "--mp4",
        "--config",
        "/c.toml",
    ]


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


def test_menubar_imports_without_the_gui_extra():
    # rumps is imported lazily inside main(), so the module must import on a plain
    # install (and on Linux CI) without the optional gui dependency present.
    import photos_tool.menubar as menubar

    assert callable(menubar.main)
    assert menubar._executable()
