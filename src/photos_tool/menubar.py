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

from ._frozen import bundled_exiftool_dir, is_pyinstaller_bundle
from .config import DEFAULT_STATE_DIR, default_config_path
from .gui_actions import (
    IDLE_GLYPH,
    SETUP_PROMPT_TEXT,
    SETUP_PROMPT_TITLE,
    WORKING_GLYPH,
    build_connect_argv,
    build_send_argv,
    confirm_delete_message,
    confirm_reveal_message,
    connect_failure_message,
    connect_success_message,
    map_exit_code,
    parse_cleanup_query,
    parse_connect_result,
    send_action_for_automation_status,
    status_glyph,
)


def _cli_prefix() -> list[str]:
    """The argv prefix used to run the photos-tool CLI, as a list.

    Frozen PyInstaller .app: re-invoke the app's OWN signed binary with a ``--pyi-cli``
    sentinel (main() dispatches on it), so the osxphotos export and the PhotoKit delete run
    inside the app's code signature — macOS attributes Photos to the app (the prompt reads
    "photos-tool", the grant is reused).

    Dev/CI: the sibling ``photos-tool`` console script in the venv, returned as a one-element
    list so callers always splat ``[*prefix, subcmd, ...]``.
    """
    # Frozen PyInstaller .app: re-invoke OURSELVES with a sentinel so the CLI child is the
    # app's own signed binary — macOS attributes the osxphotos/PhotoKit work to the app's
    # bundle id ("photos-tool"), not a generic "Python". main() dispatches on the sentinel.
    if is_pyinstaller_bundle():
        return [sys.executable, "--pyi-cli"]
    # Dev/CI: prefer the photos-tool console script next to sys.argv[0] (same venv) so the
    # GUI drives its own versioned CLI, not a stale pyenv shim. Fall back to PATH. Return an
    # absolute path when we have one.
    sibling = Path(sys.argv[0]).resolve().parent / "photos-tool"
    if sibling.exists():
        return [str(sibling)]
    return [shutil.which("photos-tool") or "photos-tool"]


def _env() -> dict[str, str]:
    # A GUI app launched from Finder inherits a minimal PATH. These entries serve the DEV/CI
    # console-script path: find the venv's photos-tool sibling and a Homebrew osxphotos/exiftool.
    # The frozen .app doesn't depend on them — it reaches the CLI via --pyi-cli, self-reinvokes
    # osxphotos, and prepends its bundled exiftool (so it works on a clean Mac with no Homebrew);
    # a Homebrew exiftool, if present, would still be found here.
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


# Flips to True once AEDeterminePermissionToAutomateTarget returns 0 (granted) this session,
# so routine sends skip the foreground-activation dance + the watchdog wait below. The grant
# persists in TCC and the osxphotos children inherit it; a later revocation just degrades to a
# graceful "nothing selected" on the next send (osxphotos can't read a denied selection).
_AUTOMATION_GRANTED = False
# True while a consent prompt is up: the 0.5s run-loop spin below processes events, so a second
# "Send Selected" click could re-enter this function and double-flip the activation policy. Guard
# against that re-entry rather than stacking a second prompt.
_CONSENT_IN_FLIGHT = False


def request_photos_automation() -> int | None:
    """Proactively obtain the one-time Automation->Photos ("control Photos") consent.

    Reading the live Photos *selection* needs Apple Events consent. The osxphotos child is a
    non-interactive subprocess that can never present that prompt, so the menu-bar parent must
    obtain the grant once; it is keyed to this bundle id, so the self-reinvoked osxphotos
    children inherit it. Cached per-process so only the FIRST send pays the activation cost.

    Returns the ``OSStatus`` from ``AEDeterminePermissionToAutomateTarget`` -- ``0`` granted,
    ``-1743`` declined, ``-1744`` could-not-prompt, ``-600`` Photos not running -- or ``None``
    if the request could not be made at all (e.g. it hung past the watchdog).

    Three OS requirements the earlier ``NSAppleScript`` attempt silently missed:

      * the bundle Info.plist must declare ``NSAppleEventsUsageDescription`` or tccd refuses
        the request outright -- no dialog, no Automation-pane entry (set in the .spec);
      * an LSUIElement agent is never the *active* app and macOS suppresses the consent dialog
        for inactive apps, so we briefly become a regular foreground app AND spin the run loop
        so the asynchronous activation actually lands before we ask;
      * ``AEDeterminePermissionToAutomateTarget`` (Apple's purpose-built prompt trigger) returns
        an actionable status, where a fire-and-forget AppleScript send just no-ops.
    """
    global _AUTOMATION_GRANTED, _CONSENT_IN_FLIGHT
    if _AUTOMATION_GRANTED:
        return 0  # already granted this session: fast path, no activation dance, no wait
    if _CONSENT_IN_FLIGHT:
        return None  # a prompt is already up (re-entrant click during the run-loop spin)
    _CONSENT_IN_FLIGHT = True
    try:
        import ctypes
        import threading
        import time
        from ctypes import c_int32, c_uint8, c_uint32, c_void_p

        from AppKit import (
            NSApplication,
            NSApplicationActivationPolicyAccessory,
            NSApplicationActivationPolicyRegular,
            NSWorkspace,
        )
        from Foundation import NSAppleEventDescriptor, NSDate, NSRunLoop

        photos_bid = "com.apple.Photos"
        type_wildcard = 0x2A2A2A2A  # four-char code '****' -- "may I send ANY event?"

        # Not bound by PyObjC; reach the C API through the CoreServices umbrella framework.
        core = ctypes.CDLL("/System/Library/Frameworks/CoreServices.framework/CoreServices")
        determine = core.AEDeterminePermissionToAutomateTarget
        # OSStatus(const AEAddressDesc*, AEEventClass, AEEventID, Boolean)
        determine.argtypes = [c_void_p, c_uint32, c_uint32, c_uint8]
        determine.restype = c_int32

        workspace = NSWorkspace.sharedWorkspace()
        # Photos must be running or the call returns -600 (and is more prone to hang).
        running = workspace.runningApplications()
        if not any(a.bundleIdentifier() == photos_bid for a in running):
            workspace.launchApplication_("Photos")

        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        app.activateIgnoringOtherApps_(True)
        # Activation is asynchronous; spin the run loop so we are genuinely frontmost before
        # asking -- exactly what the old synchronous NSAppleScript path got wrong.
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.5))
        try:
            descriptor = NSAppleEventDescriptor.descriptorWithBundleIdentifier_(photos_bid)
            ae_desc = descriptor.aeDesc()  # a PyObjCPointer wrapping the C AEDesc*
            # objc.pyobjc_id() does NOT work here (the AEDesc is not an ObjC object);
            # .pointerAsInteger is the AEDesc address (a bare int, zero ownership). The buffer
            # is owned by `descriptor`, pinned for the worker's whole life inside _ask().
            target_ptr = c_void_p(ae_desc.pointerAsInteger)

            result: dict[str, int] = {}

            def _ask(_keep_descriptor: Any = descriptor) -> None:
                # askUserIfNeeded=1 presents "photos-tool wants to control Photos" and blocks
                # until answered; off the main thread + watchdog-joined because this call is
                # known to occasionally hang at semaphore_wait_trap (Apple FB9126429).
                #
                # The `_keep_descriptor` default arg pins `descriptor` to this function for the
                # worker thread's whole life: target_ptr aliases the AEDesc buffer `descriptor`
                # owns, and on the orphaned-hang path the enclosing frame returns and would
                # otherwise GC `descriptor`, freeing the buffer while determine() still reads it.
                result["status"] = determine(target_ptr, type_wildcard, type_wildcard, 1)

            worker = threading.Thread(target=_ask, name="photos-automation", daemon=True)
            worker.start()
            # Pump the run loop in short slices instead of blocking the main thread on join(),
            # so the menu bar stays responsive while the consent dialog is up (and through the
            # rare semaphore-wait hang). The worker sets the result the instant the user answers;
            # the 120s ceiling only bounds a true hang. Re-entry during the pump is blocked by
            # _CONSENT_IN_FLIGHT (set above) and by send_selected's own guard.
            run_loop = NSRunLoop.currentRunLoop()
            deadline = time.monotonic() + 120
            while worker.is_alive() and time.monotonic() < deadline:
                run_loop.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.1))
            status = result.get("status")  # None if it hung past the deadline
            if status == 0:
                _AUTOMATION_GRANTED = True
            return status
        finally:
            app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    except Exception as exc:  # never crash the caller
        print(f"photos-tool: could not request Photos automation consent: {exc}", file=sys.stderr)
        return None
    finally:
        _CONSENT_IN_FLIGHT = False


def _maybe_dispatch_reinvocation(argv: list[str]) -> int | None:
    """Run a self-reinvocation sentinel and return its exit code, or ``None`` for normal startup.

    The frozen PyInstaller app shells out to ITSELF for the CLI and for osxphotos (so both run
    inside the app's own signed binary, giving a clean "photos-tool" TCC identity); this routes
    the sentinels to the right entry point. Pure + unit-tested so a sentinel typo in the argv
    builders (``_cli_prefix`` / ``_osxphotos_argv``) can't drift silently. ``argv`` is sys.argv[1:].
    """
    if argv[:1] == ["--pyi-cli"]:
        from .cli import main as cli_main

        return cli_main(argv[1:])
    if argv[:1] == ["--pyi-osxphotos"]:
        import runpy

        sys.argv = ["osxphotos", *argv[1:]]
        runpy.run_module("osxphotos.__main__", run_name="__main__")
        return 0
    if argv[:1] == ["--pyi-prime-imports"]:
        # Side-effect-free build smoke (scripts/build-app.sh): prove the consent machinery's
        # dependencies survived PyInstaller collection -- import AppKit/Foundation/ctypes and
        # RESOLVE the CoreServices symbol request_photos_automation() needs -- WITHOUT launching
        # Photos, flipping activation policy, or asking for consent. A collect_all/hiddenimports
        # regression breaks here; the real --pyi-prime-photos path would instead pop a consent
        # dialog and hang the build up to 120s, so it must NOT be used as a build smoke.
        import ctypes

        import AppKit  # noqa: F401  (presence is the test)
        import Foundation  # noqa: F401

        core = ctypes.CDLL("/System/Library/Frameworks/CoreServices.framework/CoreServices")
        _ = core.AEDeterminePermissionToAutomateTarget  # resolve the symbol; never call it here
        print("pyi-prime-imports OK")
        return 0
    if argv[:1] == ["--pyi-prime-photos"]:
        # Manual, on-a-real-Mac entry to obtain (or re-confirm) the "control Photos" grant from
        # the app's own signed identity (pops the consent dialog + can block up to 120s -- NOT a
        # build smoke; see --pyi-prime-imports). Prints the OSStatus; always exits 0.
        status = request_photos_automation()
        print(f"AEDeterminePermissionToAutomateTarget -> {status}")
        return 0
    return None


def _prepend_bundled_exiftool_to_path() -> None:
    """Put the bundled exiftool first on PATH for THIS process and every child it spawns.

    Covers both consumers with one change: osxphotos resolves exiftool via ``shutil.which`` and
    convert.py runs a bare ``exiftool`` — both inherit ``os.environ``. Called at the very top of
    every frozen entry (main + the --pyi-cli / --pyi-osxphotos children all funnel through here),
    so a clean Mac with no Homebrew still embeds metadata. No-op in dev/CI.
    """
    directory = bundled_exiftool_dir()
    if directory:
        os.environ["PATH"] = directory + os.pathsep + os.environ.get("PATH", "")


def main() -> None:  # pragma: no cover - requires a GUI run loop and rumps
    _prepend_bundled_exiftool_to_path()
    dispatched = _maybe_dispatch_reinvocation(sys.argv[1:])
    if dispatched is not None:
        raise SystemExit(dispatched)

    import rumps

    lock = _acquire_single_instance_lock()
    if lock is None:
        rumps.alert("photos-tool is already running", "Look for the 📷 icon in the menu bar.")
        sys.exit(0)

    cli_prefix = _cli_prefix()
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
                "Set up connection…",
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

            # First-run onboarding: with no config yet, reflect it in the status line and
            # nudge the user to connect (the prompt fires from the first _drain tick, on the
            # main thread, so it never blocks __init__/run-loop startup).
            self._configured = default_config_path().exists()
            self._welcomed = False
            if not self._configured:
                self.status.title = "Not connected — click Set up connection…"

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

        def _ask_text(self, prompt: str, title: str, default: str = "") -> str | None:
            """Native text prompt via osascript — reliably typeable for a menu-bar agent.

            rumps.Window/NSAlert text fields never get keyboard focus when the app is a
            menu-bar (accessory) agent, so collect input through macOS's OWN dialog instead.
            Returns the entered text, or None if the user cancelled or left it empty.
            """

            def q(value: str) -> str:
                return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

            script = (
                f"text returned of (display dialog {q(prompt)} "
                f"default answer {q(default)} with title {q(title)})"
            )
            try:
                proc = subprocess.run(
                    ["/usr/bin/osascript", "-e", script],
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except OSError:
                return None
            if proc.returncode != 0:  # user cancelled
                return None
            return proc.stdout.strip() or None

        def _open_automation_settings(self) -> None:
            """Explain the missing Automation->Photos grant and open the right Settings pane.

            Reached only when consent was declined or could not be presented. Also steers the
            user to "Send Album…", which is Full-Disk-Access-only and needs no Apple Events.
            """
            from AppKit import NSWorkspace
            from Foundation import NSURL

            rumps.alert(
                "Allow photos-tool to read your selection",
                "photos-tool needs permission to control Photos so it can see which photos "
                "you've selected.\n\nEnable photos-tool under Automation in the window that "
                "opens, then click Send Selected Photos again.\n\nOr use “Send Album…”, which "
                "backs up an album and needs no extra permission.",
            )
            # The `open` CLI is ignored by System Settings on Ventura+; use NSWorkspace.
            url = NSURL.URLWithString_(
                "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation"
            )
            NSWorkspace.sharedWorkspace().openURL_(url)

        @rumps.clicked("Send Selected Photos")
        def send_selected(self, _: Any) -> None:
            # Not connected yet? Onboard first, rather than popping the control-Photos consent
            # for an action that can't succeed. Use the LIVE config path (the cached _configured
            # goes stale if the user ran `photos-tool init` from the CLI).
            if not default_config_path().exists():
                self._run_setup()
                return
            if _CONSENT_IN_FLIGHT:
                return  # a consent prompt is already up (re-click while the run loop pumps)
            # One-time "control Photos" consent (Apple Events) so osxphotos can read the live
            # selection. 0 == granted; None == could-not-ask (best-effort send anyway, the
            # send's own "nothing selected" path covers a still-missing grant); a real denial
            # (-1743) or unpresentable prompt (-1744/-600) routes the user to Settings.
            status = request_photos_automation()
            if send_action_for_automation_status(status) == "send":
                self._start("send", album=None)
            else:
                self._open_automation_settings()

        @rumps.clicked("Send Album…")
        def send_album(self, _: Any) -> None:
            # Collect input on the main thread BEFORE enqueueing the job.
            album = self._ask_text("Album name to send:", "Send Album")
            if album:
                self._start("send", album=album)

        @rumps.clicked("Clean up last backup…")
        def cleanup_clicked(self, _: Any) -> None:
            if not default_config_path().exists():  # onboard before any backup/cleanup
                self._run_setup()
                return
            # Linear flow, step 1: ask the CLI how many originals are confirmed on
            # the share and where to reveal them. Runs on the worker; the Timer
            # continues the flow once the answer arrives.
            self._start("cleanup_query")

        @rumps.clicked("Run Diagnostics")
        def diagnostics(self, _: Any) -> None:
            # Request the Automation->Photos grant first (from this UI process, which CAN present
            # the prompt). Otherwise the doctor's "Photos selection readable" probe runs as a
            # subprocess that can't prompt and just hangs the 60s timeout. Status is advisory —
            # the doctor reports the result either way; we only need the prompt to have appeared.
            request_photos_automation()
            self._start("doctor")

        @rumps.clicked("Set up connection…")
        def setup_clicked(self, _: Any) -> None:
            self._run_setup()

        def _run_setup(self) -> None:
            # Re-setup guard: don't let a stray click clobber a working connection silently.
            if default_config_path().exists() and (
                rumps.alert(
                    "Already connected",
                    "Reconnect or change the backup share? Your existing settings "
                    "will be replaced.",
                    ok="Reconnect",
                    cancel="Cancel",
                )
                != 1
            ):
                return
            smb_url = self._ask_text(SETUP_PROMPT_TEXT, SETUP_PROMPT_TITLE, default="smb://")
            if smb_url and smb_url != "smb://":
                self._start("connect", smb_url=smb_url)

        def _drain(self, _timer: Any) -> None:
            """Sole place that touches rumps UI from queue results (main thread)."""
            if not self._configured and not self._welcomed:
                self._welcomed = True
                if (
                    rumps.alert(
                        "Welcome to photos-tool",
                        "Let's connect to your backup. Click Set up to enter your "
                        "Windows shared folder address.",
                        ok="Set up",
                        cancel="Later",
                    )
                    == 1
                ):
                    self._run_setup()
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
            elif kind == "connect":
                self._show_connect_result(result)
            elif kind == "error":
                rumps.alert(result.get("title") or "Error", result.get("message") or "")

        def _show_connect_result(self, result: dict[str, Any]) -> None:
            outcome = parse_connect_result(result.get("stdout", ""))
            if outcome.ok:
                self._configured = True
                self.status.title = "Connected — ready to send"
                note = connect_success_message(outcome.destination)
            else:
                note = connect_failure_message(outcome.error)
            rumps.alert(note.title, note.message)

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
                # Reveal-and-select EVERY still-present copy (across whatever date
                # folders the batch spans), so the user confirms the whole batch
                # arrived — not just one zoomed-in file.
                subprocess.run(["open", "-R", *query.reveal], check=False)
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
                    elif kind == "connect":
                        self._results.put(self._do_connect(job.get("smb_url", "")))
                except Exception as exc:  # never let the worker die
                    self._results.put({"kind": "error", "title": "Error", "message": str(exc)})

        def _do_connect(self, smb_url: str) -> dict[str, Any]:
            # Runs `photos-tool connect`, which triggers macOS's OWN mount/auth dialog
            # (password -> Keychain, never seen here). The worker blocks on that dialog;
            # the menu bar stays responsive.
            try:
                proc = subprocess.run(
                    build_connect_argv(cli_prefix, smb_url),
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                    stdin=subprocess.DEVNULL,
                )
            except OSError as exc:
                return {"kind": "error", "title": "Setup failed", "message": str(exc)}
            return {"kind": "connect", "stdout": proc.stdout or ""}

        def _do_send(self, album: str | None) -> dict[str, Any]:
            argv = build_send_argv(cli_prefix, album=album)
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
                    [*cli_prefix, "cleanup-last", "--json"],
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
                    [*cli_prefix, "cleanup-last", "--yes"],
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
                    [*cli_prefix, "doctor"],
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
