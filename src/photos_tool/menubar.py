"""macOS menu-bar launcher (rumps) — a thin shell over the photos-tool CLI.

Owns no business logic: every action shells out to the ``photos-tool`` console
script (so it gets the real exit code, inherits Full Disk Access, and reuses the
per-Mac send lock). All pure decisions live in :mod:`photos_tool.gui_actions`
(unit-tested in CI); this module is the untested view and is excluded from
pyright.

Threading contract (AppKit is not thread-safe):
  * rumps ``@clicked`` callbacks and the ``rumps.Timer`` callback run on the
    MAIN thread. They are the ONLY code that may touch rumps/AppKit
    (``self.title``, ``MenuItem.title``, ``rumps.alert``/``notification``).
  * A single daemon worker thread runs every ``subprocess`` and pushes a result
    dict onto ``self._results``. It touches NOTHING rumps/AppKit.
  * A ``self._busy`` flag makes a second click a no-op while a job is in flight,
    so two osxphotos exports can never overlap (never silently lose photos).

Install with the ``gui`` extra (``pip install 'photos-tool[gui]'``) and run
``photos-tool-menubar`` (dev/CI). For a family member, ship a signed .app bundle
with ``LSUIElement=True`` so the bundle is the stable TCC identity osxphotos
children inherit.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from .config import DEFAULT_STATE_DIR
from .gui_actions import (
    IDLE_GLYPH,
    WORKING_GLYPH,
    build_send_argv,
    confirm_delete_message,
    confirm_reveal_message,
    map_exit_code,
    parse_cleanup_query,
    status_glyph,
)


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


def _state_dir() -> Path:
    return Path(DEFAULT_STATE_DIR).expanduser()


def _acquire_single_instance_lock():
    """Take the process-level single-instance lock, or return ``None`` if held.

    Reuses the cli.py flock idiom: a non-blocking ``LOCK_EX`` on a lockfile in
    the state dir. rumps does not prevent a second copy (login item + manual
    launch); without this guard two status items would contend. The caller holds
    the handle for the process lifetime; the OS releases the flock on exit.
    """
    state_dir = _state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    handle = (state_dir / "menubar.lock").open("w")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def main() -> None:  # pragma: no cover - requires a GUI run loop and rumps
    import rumps

    lock = _acquire_single_instance_lock()
    if lock is None:
        rumps.alert("photos-tool is already running", "Look for the 📷 icon in the menu bar.")
        sys.exit(0)

    exe = _executable()
    env = _env()

    class PhotosToolApp(rumps.App):
        def __init__(self) -> None:
            super().__init__("📷", quit_button="Quit")
            # Hold the single-instance lock for the process lifetime (don't GC it).
            self._lock = lock
            # An always-visible, disabled result line: Notification Center alerts
            # only work from a signed .app bundle, so this is the reliable feedback
            # unbundled.
            self.status = rumps.MenuItem("Last backup: none yet")
            self.status.set_callback(None)  # disabled (greyed) info line
            # Config-only: JPEG/MP4/remove are set once at init in the TOML config;
            # the menu exposes no toggles, so a non-technical user can't change
            # behaviour between runs (deterministic).
            self.menu = [
                self.status,
                None,
                "Send Selected Photos",
                "Send Album…",
                None,
                "Clean up last backup…",
                "Run Diagnostics",
            ]

            # Threading: a single daemon worker drains a job queue; results come
            # back on a results queue that ONLY the Timer (main thread) reads.
            self._busy = False
            # The title to return to once a job finishes: the last send result glyph
            # (or plain idle before any send). Non-send jobs restore this rather than
            # leaving the working glyph stuck on.
            self._idle_glyph = IDLE_GLYPH
            self._jobs: queue.Queue[dict[str, Any]] = queue.Queue()
            self._results: queue.Queue[dict[str, Any]] = queue.Queue()
            worker = threading.Thread(target=self._worker, name="photos-tool-worker", daemon=True)
            worker.start()
            self._timer = rumps.Timer(self._drain, 0.3)
            self._timer.start()

        # ---- main thread only: enqueue + UI -------------------------------

        def _start(self, kind: str, **payload: Any) -> bool:
            """Enqueue a job. A second click while busy is a deliberate no-op."""
            if self._busy:
                rumps.alert("Please wait", "A job is already running.")
                return False
            self._busy = True
            self.title = WORKING_GLYPH
            job: dict[str, Any] = {"kind": kind}
            job.update(payload)
            self._jobs.put(job)
            return True

        @rumps.clicked("Send Selected Photos")
        def send_selected(self, _: Any) -> None:
            self._start("send", album=None)

        @rumps.clicked("Send Album…")
        def send_album(self, _: Any) -> None:
            # Modal — collect input on the main thread BEFORE enqueueing the job.
            resp = rumps.Window("Album name to send:", "Send Album", dimensions=(240, 24)).run()
            if resp.clicked and resp.text.strip():
                self._start("send", album=resp.text.strip())

        @rumps.clicked("Clean up last backup…")
        def cleanup_clicked(self, _: Any) -> None:
            # Linear flow, step 1: ask the CLI how many originals are confirmed on
            # the share and where to reveal them. Runs on the worker; the Timer
            # continues the flow once the answer arrives.
            self._start("cleanup_query")

        @rumps.clicked("Run Diagnostics")
        def diagnostics(self, _: Any) -> None:
            self._start("doctor")

        def _drain(self, _timer: Any) -> None:
            """Sole place that touches rumps UI from queue results (main thread)."""
            while True:
                try:
                    result = self._results.get_nowait()
                except queue.Empty:
                    return
                self._busy = False
                self._handle(result)
                # send sets its own result glyph; for every other kind, restore the
                # title once the chain is truly done. _handle may enqueue a follow-up
                # (cleanup_query -> cleanup_apply), which re-sets _busy + the working
                # glyph — so only restore when nothing new was started.
                if result["kind"] != "send" and not self._busy:
                    self.title = self._idle_glyph

        def _handle(self, result: dict[str, Any]) -> None:
            kind = result["kind"]
            if kind == "send":
                self._show_send_result(result)
            elif kind == "cleanup_query":
                self._continue_cleanup(result)
            elif kind == "cleanup_apply":
                rumps.alert("Cleanup", result.get("message") or "Done.")
                self.status.title = "Last backup: cleaned up"
            elif kind == "doctor":
                rumps.alert(
                    "photos-tool diagnostics", result.get("output") or "doctor produced no output"
                )
            elif kind == "error":
                rumps.alert(result.get("title") or "Error", result.get("message") or "")

        def _show_send_result(self, result: dict[str, Any]) -> None:
            code = result.get("code")
            note = map_exit_code(code) if code is not None else None
            title = note.title if note else "Could not run photos-tool"
            message = note.message if note else result.get("message", "")
            self._idle_glyph = status_glyph(code)  # remember it across later non-send jobs
            self.title = self._idle_glyph
            self.status.title = f"Last backup: {title}"
            with contextlib.suppress(Exception):  # notifications need a signed bundle
                rumps.notification(title, "", message)

        def _continue_cleanup(self, result: dict[str, Any]) -> None:
            query = parse_cleanup_query(result.get("stdout", ""))
            if query.count == 0:
                rumps.alert(
                    "Nothing to clean up",
                    "No backed-up originals are confirmed on the share.",
                )
                return
            # Step 2: confirm + reveal the REAL file in Finder so the user can see
            # it landed before anything is deleted.
            reveal = confirm_reveal_message(query.count)
            resp = rumps.alert(reveal.title, reveal.message, ok="Show me", cancel="Cancel")
            if resp != 1:  # Cancel
                return
            if query.reveal:
                subprocess.run(["open", "-R", query.reveal], check=False)
            # Step 3: only now offer the recoverable deletion.
            confirm = confirm_delete_message(query.count)
            resp = rumps.alert(
                confirm.title, confirm.message, ok="Move to Recently Deleted", cancel="Keep"
            )
            if resp != 1:  # Keep
                return
            self._start("cleanup_apply")

        # ---- worker thread only: subprocess, no rumps ---------------------

        def _worker(self) -> None:
            while True:
                job = self._jobs.get()
                kind = job["kind"]
                try:
                    if kind == "send":
                        self._results.put(self._do_send(job.get("album")))
                    elif kind == "cleanup_query":
                        self._results.put(self._do_cleanup_query())
                    elif kind == "cleanup_apply":
                        self._results.put(self._do_cleanup_apply())
                    elif kind == "doctor":
                        self._results.put(self._do_doctor())
                except Exception as exc:  # never let the worker die
                    self._results.put({"kind": "error", "title": "Error", "message": str(exc)})

        def _do_send(self, album: str | None) -> dict[str, Any]:
            argv = build_send_argv(exe, album=album)
            try:
                # No terminal in a GUI launch: fully detach the child's stdio so a
                # background osxphotos progress bar can't get SIGTTIN-suspended.
                code = subprocess.run(
                    argv,
                    env=env,
                    check=False,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).returncode
            except OSError as exc:
                return {"kind": "send", "code": None, "message": str(exc)}
            return {"kind": "send", "code": code}

        def _do_cleanup_query(self) -> dict[str, Any]:
            try:
                proc = subprocess.run(
                    [exe, "cleanup-last", "--json"],
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                    stdin=subprocess.DEVNULL,
                )
            except OSError as exc:
                return {"kind": "error", "title": "Cleanup failed", "message": str(exc)}
            return {"kind": "cleanup_query", "stdout": proc.stdout or ""}

        def _do_cleanup_apply(self) -> dict[str, Any]:
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
                return {"kind": "error", "title": "Cleanup failed", "message": str(exc)}
            lines = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
            return {"kind": "cleanup_apply", "message": lines[-1] if lines else "Done."}

        def _do_doctor(self) -> dict[str, Any]:
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
                return {"kind": "error", "title": "Diagnostics failed", "message": str(exc)}
            return {"kind": "doctor", "output": ((proc.stdout or "") + (proc.stderr or "")).strip()}

    PhotosToolApp().run()


if __name__ == "__main__":
    main()
