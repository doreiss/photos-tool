"""Pure logic for the menu-bar app: build the send argv and map exit codes.

Kept import-light (no rumps) so the whole decision layer is unit-tested in CI;
``menubar.py`` is the only untested view code. Reuses the CLI's exit-code
constants so the GUI and CLI can never drift on what an exit code means.
"""

from __future__ import annotations

from dataclasses import dataclass

from .cli import (
    EXIT_CONVERSION,
    EXIT_NOTHING_SELECTED,
    EXIT_OK,
    EXIT_PREFLIGHT,
    EXIT_RECONCILE,
    EXIT_USAGE,
)


@dataclass(frozen=True)
class Notification:
    title: str
    message: str


def build_send_argv(
    executable: str,
    *,
    album: str | None = None,
    jpeg: bool = False,
    mp4: bool = False,
    config: str | None = None,
) -> list[str]:
    """Build the ``photos-tool send`` argv for a menu action."""
    argv = [executable, "send"]
    if album:
        argv += ["--album", album]
    argv.append("--jpeg" if jpeg else "--no-jpeg")
    argv.append("--mp4" if mp4 else "--no-mp4")
    if config:
        argv += ["--config", config]
    return argv


_MESSAGES = {
    EXIT_OK: Notification("Photos sent", "Backup complete."),
    EXIT_RECONCILE: Notification(
        "Some photos were skipped", "Turn on iCloud 'Download Originals' and try again."
    ),
    EXIT_NOTHING_SELECTED: Notification("Nothing selected", "Pick photos in Photos first."),
    EXIT_CONVERSION: Notification(
        "Sent, but a copy failed", "Your originals were sent; a JPEG/MP4 copy failed."
    ),
    EXIT_PREFLIGHT: Notification(
        "Can't send yet", "Check tools, the share, and Full Disk Access — Run Diagnostics."
    ),
    EXIT_USAGE: Notification("Setup needed", "Configure the share first."),
}


def map_exit_code(code: int) -> Notification:
    """Map a send exit code to a notification a non-technical user understands."""
    return _MESSAGES.get(
        code, Notification("Send failed", f"Something went wrong (code {code}). Run Diagnostics.")
    )
