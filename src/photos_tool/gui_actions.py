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
    EXIT_UNVERIFIED,
    EXIT_USAGE,
)


@dataclass(frozen=True)
class Notification:
    title: str
    message: str


def send_action_for_automation_status(status: int | None) -> str:
    """Decide what to do after ``request_photos_automation()`` returns its OSStatus.

    ``0`` (granted) -> ``"send"``. ``None`` (could-not-ask: watchdog timeout / exception)
    -> best-effort ``"send"`` anyway; the send's own "nothing selected" path covers a
    still-missing grant. Any real failure status (``-1743`` declined, ``-1744`` could-not-
    prompt, ``-600`` Photos not running) -> ``"open_settings"``, so the user is steered to
    the Automation pane instead of a silently-empty send. Pure so it's unit-tested (the
    rumps callback that calls it is not).
    """
    if status == 0 or status is None:
        return "send"
    return "open_settings"


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
    EXIT_UNVERIFIED: Notification(
        "Not fully backed up",
        "Some photos didn't reach the share. Check it's connected, then send again.",
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


# ---- first-run connection onboarding (no-Terminal setup) --------------------------

SETUP_PROMPT_TITLE = "Connect to your backup"
SETUP_PROMPT_TEXT = (
    "Enter the address of your Windows shared folder — it looks like\n"
    "smb://192.168.1.50/FamilyPhotos (ask whoever set up the PC if unsure).\n\n"
    "Next, macOS will ask for the share's username and password and offer to\n"
    "save them to your Keychain. photos-tool never sees or stores the password."
)


@dataclass(frozen=True)
class ConnectResult:
    ok: bool
    destination: str
    error: str


def build_connect_argv(
    cli_prefix: list[str],
    smb_url: str,
    *,
    config: str | None = None,
) -> list[str]:
    """Build the ``photos-tool connect`` argv for the GUI setup flow.

    ``--force`` is always passed: "Set up connection…" is a deliberate user action, and
    the GUI confirms a reconnect before calling this. ``--json`` gives a parseable result.
    """
    argv = [*cli_prefix, "connect", "--smb-url", smb_url]
    if config:
        argv += ["--config", config]
    argv += ["--json", "--force"]
    return argv


def parse_connect_result(stdout: str) -> ConnectResult:
    """Parse ``connect --json`` stdout. Any garble is treated as a failure (never 'ok')."""
    try:
        data = json.loads(stdout.strip() or "{}")
    except json.JSONDecodeError:
        return ConnectResult(False, "", "photos-tool did not return a result")
    if not isinstance(data, dict):
        return ConnectResult(False, "", "unexpected result from photos-tool")
    return ConnectResult(
        ok=bool(data.get("ok")),
        destination=str(data.get("destination") or ""),
        error=str(data.get("error") or ""),
    )


def connect_success_message(destination: str) -> Notification:
    return Notification(
        "Connected",
        f"Your photos will back up to:\n{destination}\n\n"
        "Select photos in Photos, then click Send Selected Photos.",
    )


def connect_failure_message(error: str) -> Notification:
    detail = error.strip() or "could not connect to the share."
    return Notification(
        "Couldn't connect",
        f"{detail}\n\nCheck the address, that the PC is on and sharing, and the "
        "password, then try Set up connection… again.",
    )
