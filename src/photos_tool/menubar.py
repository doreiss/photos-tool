"""macOS menu-bar launcher (rumps) — a thin shell over the photos-tool CLI.

Owns no business logic: every action shells out to the ``photos-tool`` console
script (so it gets the real exit code, inherits Full Disk Access, and reuses the
per-Mac send lock) and turns the exit code into a notification. Install with the
``gui`` extra (``pip install 'photos-tool[gui]'``) and run ``photos-tool-menubar``.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .gui_actions import build_send_argv, map_exit_code


def _executable() -> str:
    # Prefer the photos-tool console script installed right next to this menubar
    # script (same venv / app bundle) so the GUI always drives its own versioned
    # CLI, not some other photos-tool on PATH (e.g. a stale pyenv shim). Fall back
    # to PATH. subprocess resolves a relative name via the parent PATH (not our
    # augmented env), so always return an absolute path when we have one.
    sibling = Path(sys.argv[0]).resolve().parent / "photos-tool"
    if sibling.exists():
        return str(sibling)
    return shutil.which("photos-tool") or "photos-tool"


def _env() -> dict[str, str]:
    # A GUI app launched from Finder inherits a minimal PATH; make sure the tools
    # (and the venv's photos-tool) are findable.
    env = dict(os.environ)
    extra = [
        str(Path(sys.argv[0]).resolve().parent),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        str(Path.home() / ".local" / "bin"),
    ]
    env["PATH"] = os.pathsep.join([*extra, env.get("PATH", "")])
    return env


def main() -> None:  # pragma: no cover - requires a GUI run loop and rumps
    import rumps

    exe = _executable()
    env = _env()

    class PhotosToolApp(rumps.App):
        def __init__(self) -> None:
            super().__init__("📷", quit_button="Quit")
            # An always-visible result line: Notification Center alerts only work
            # from a signed .app bundle, so this is the reliable feedback unbundled.
            self.status = rumps.MenuItem("Last backup: none yet")
            self.jpeg = rumps.MenuItem("JPEG copies for Windows", callback=self._toggle)
            self.mp4 = rumps.MenuItem("MP4 copies for Windows", callback=self._toggle)
            self.offer = rumps.MenuItem("Offer cleanup after each backup", callback=self._toggle)
            self.menu = [
                self.status,
                None,
                "Send Selected Photos",
                "Send Album…",
                None,
                self.jpeg,
                self.mp4,
                None,
                "Clean up last backup…",
                self.offer,
                None,
                "Run Diagnostics",
            ]

        def _toggle(self, item: rumps.MenuItem) -> None:
            item.state = not item.state

        def _run(self, album: str | None = None) -> None:
            argv = build_send_argv(
                exe, album=album, jpeg=bool(self.jpeg.state), mp4=bool(self.mp4.state)
            )
            try:
                # A GUI app has no terminal; fully detach the child's stdio so a
                # background launch can't be suspended (SIGTTIN/SIGTTOU) when
                # osxphotos writes its progress bar. The exit code drives the result.
                code = subprocess.run(
                    argv,
                    env=env,
                    check=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).returncode
            except OSError as exc:
                self._report(None, "Could not run photos-tool", str(exc))
                return
            note = map_exit_code(code)
            self._report(code, note.title, note.message)
            # After a clean backup, optionally offer to free up space (opt-in).
            if code == 0 and bool(self.offer.state):
                self._offer_cleanup(after_send=True)

        def _report(self, code: int | None, title: str, message: str) -> None:
            if code is None:
                glyph = "📷✕"
            elif code == 0:
                glyph = "📷✓"
            else:
                glyph = "📷⚠️"
            self.title = glyph
            self.status.title = f"Last backup: {title}"
            # Best effort — Notification Center only works from a signed .app bundle.
            with contextlib.suppress(Exception):
                rumps.notification(title, "", message)

        def _cleanup_query(self) -> tuple[int, str]:
            try:
                proc = subprocess.run(
                    [exe, "cleanup-last", "--json"],
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                    stdin=subprocess.DEVNULL,
                )
                data = json.loads(proc.stdout.strip() or "{}")
            except (OSError, json.JSONDecodeError):
                return 0, ""
            return int(data.get("count", 0)), str(data.get("destination", ""))

        def _offer_cleanup(self, after_send: bool) -> None:
            count, destination = self._cleanup_query()
            if count == 0:
                if not after_send:  # only speak up when the user explicitly asked
                    rumps.alert(
                        "Nothing to clean up",
                        "No backed-up originals are confirmed on the share.",
                    )
                return
            resp = rumps.alert(
                "Free up space on this Mac?",
                f"{count} photo(s) from your last backup are confirmed on the share. Move them to "
                "Recently Deleted? They stay recoverable for 30 days.\n\nReveal them on the share "
                "first if you'd like to check they arrived.",
                ok="Move to Recently Deleted",
                cancel="Not now",
                other="Reveal on share…",
            )
            if resp == -1:  # Reveal — open the folder so the user can verify, then re-offer later
                if destination:
                    subprocess.run(["open", destination], check=False)
                return
            if resp != 1:  # Not now
                return
            try:
                proc = subprocess.run(
                    [exe, "cleanup-last", "--yes"],
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                    stdin=subprocess.DEVNULL,
                )
            except OSError as exc:
                rumps.alert("Cleanup failed", str(exc))
                return
            lines = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
            rumps.alert("Cleanup", lines[-1] if lines else "Done.")
            self.status.title = "Last backup: cleaned up"

        @rumps.clicked("Clean up last backup…")
        def cleanup_clicked(self, _: rumps.MenuItem) -> None:
            self._offer_cleanup(after_send=False)

        @rumps.clicked("Send Selected Photos")
        def send_selected(self, _: rumps.MenuItem) -> None:
            self._run()

        @rumps.clicked("Send Album…")
        def send_album(self, _: rumps.MenuItem) -> None:
            resp = rumps.Window("Album name to send:", "Send Album", dimensions=(240, 24)).run()
            if resp.clicked and resp.text.strip():
                self._run(album=resp.text.strip())

        @rumps.clicked("Run Diagnostics")
        def diagnostics(self, _: rumps.MenuItem) -> None:
            # doctor talks only via stdout/stderr; a Finder-launched app has no
            # terminal, so capture the output and show it in a dialog.
            try:
                proc = subprocess.run(
                    [exe, "doctor"],
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                    stdin=subprocess.DEVNULL,
                )
            except OSError as exc:
                rumps.alert("Diagnostics failed", str(exc))
                return
            output = ((proc.stdout or "") + (proc.stderr or "")).strip()
            rumps.alert("photos-tool diagnostics", output or "doctor produced no output")

    PhotosToolApp().run()


if __name__ == "__main__":
    main()
