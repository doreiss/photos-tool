"""Pure logic for the menu-bar app: argv building, exit-code mapping, cleanup flow.

Kept import-light (no rumps) so the whole decision layer is unit-tested in CI;
``menubar.py`` is the only untested view code. Reuses the CLI's exit-code
constants so the GUI and CLI can never drift on what an exit code means.
"""

from __future__ import annotations

import json
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
    cli_prefix: list[str],
    *,
    album: str | None = None,
    config: str | None = None,
) -> list[str]:
    """Build the ``photos-tool send`` argv for a menu action.

    ``cli_prefix`` is the CLI invocation as a list: ``["/abs/photos-tool"]`` in dev, or
    ``["/abs/bundle-python", "-m", "photos_tool"]`` when running frozen inside the signed
    .app (so the osxphotos/PhotoKit children inherit the bundle's TCC identity).

    JPEG/MP4/remove are config-only (set once at init): the GUI never passes
    ``--jpeg``/``--mp4`` so the CLI's config — the single source of truth —
    decides. The only per-action input is which album (or selection) to send.
    """
    argv = [*cli_prefix, "send"]
    if album:
        argv += ["--album", album]
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


# Menu-bar title glyphs (all view strings live here, never inline in menubar.py).
IDLE_GLYPH = "📷"  # nothing running; also the "no send yet" resting state
WORKING_GLYPH = "📷…"  # a job is in flight


def status_glyph(code: int | None) -> str:
    """Menu-bar title glyph for the last send result (``None`` == launch error)."""
    if code is None:
        return "📷✕"
    if code == EXIT_OK:
        return "📷✓"
    return "📷⚠️"


# Cap how many files the cleanup reveal selects in Finder: a human spot-check of a
# few files across the batch's folders is enough, and it avoids a window storm for a
# huge batch. The prompt always states the true total separately.
REVEAL_CAP = 30


@dataclass(frozen=True)
class CleanupQuery:
    """Result of ``cleanup-last --json``: how many originals are removable and the
    still-present backup copies (up to :data:`REVEAL_CAP`) to reveal-and-select in
    Finder so the user can confirm the whole batch — not one file — really landed."""

    count: int
    reveal: tuple[str, ...]


def parse_cleanup_query(stdout: str) -> CleanupQuery:
    """Parse ``cleanup-last --json`` stdout into a :class:`CleanupQuery`.

    Tolerates empty/garbled output (returns a zero-count query) so a transient
    CLI hiccup can never be mistaken for "there are things to delete".
    """
    try:
        data = json.loads(stdout.strip() or "{}")
    except json.JSONDecodeError:
        return CleanupQuery(0, ())
    if not isinstance(data, dict):
        return CleanupQuery(0, ())
    try:
        count = int(data.get("count", 0))
    except (TypeError, ValueError):
        count = 0
    raw = data.get("reveal", [])
    if isinstance(raw, str):  # back-compat: an older CLI emitted a single path
        raw = [raw] if raw else []
    if not isinstance(raw, list):
        raw = []
    reveal = tuple(p for p in raw if isinstance(p, str) and p)[:REVEAL_CAP]
    return CleanupQuery(max(count, 0), reveal)


def confirm_reveal_message(count: int) -> Notification:
    """First cleanup prompt: confirm the originals are on the share before any
    deletion. ``Show me`` reveals the real file in Finder; ``Cancel`` aborts."""
    return Notification(
        "Confirm your photos arrived",
        f"{count} photo(s) are confirmed on the share. Show them in Finder so you can "
        "check they really arrived?",
    )


def confirm_delete_message(count: int) -> Notification:
    """Second cleanup prompt, shown only after the user has seen the files: the
    actual, recoverable deletion. ``Keep`` aborts; only the move runs the CLI."""
    return Notification(
        "Did they all arrive?",
        f"Move {count} backed-up original(s) to Recently Deleted? "
        "They stay recoverable for 30 days.",
    )
