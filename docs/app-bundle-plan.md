# photos-tool .app bundle — build plan & artifacts (from workflow wf_d5ca443f-847)

Status: DESIGN COMPLETE, not yet implemented. Implement in the same pass as the safety fixes.

## Core architecture decision (the crux: TCC identity)

The bundled menu-bar app shells out to its **own embedded framework interpreter**
(`[<bundle>/Contents/Frameworks/Python.framework/Versions/3.11/bin/python3.11, "-m", "photos_tool", <subcmd>, ...]`),
NOT to an external venv `photos-tool`. This keeps the whole responsible-process chain
(app stub → bundled python → `remove.py` PhotoKit / osxphotos) inside ONE signed bundle id, so the
Photos consent prompt reads **"photos-tool"** (not "Python") and the grant is reusable. Every safety
semantic is preserved: the delete still runs in the locked CLI child (separate process, detached stdio,
real exit code, flock, verify-before-delete gate, recoverable PhotoKit delete, per-destination token).

**Most important code-correctness point:** under py2app `sys.executable` IS the app stub — running it
re-launches the GUI. `_cli_prefix()` MUST discover the real framework interpreter from a probed candidate
list and HARD-FAIL if none found (never silently re-run the stub).

CLI invocation is generalized to a `list[str]` prefix (`cli_prefix`): dev = `["/abs/photos-tool"]`;
frozen = `["/abs/bundle-python", "-m", "photos_tool"]`. All call sites splat `[*cli_prefix, subcmd, ...]`.

Signing: **ad-hoc** (`codesign --force --deep --sign -`) with a STABLE `CFBundleIdentifier`
`com.dominicreiss.photos-tool`. Developer-ID + notarization is the optional later upgrade (stable
Designated Requirement → grants survive rebuilds, double-click opens without Gatekeeper override).

## NEW FILE: app_main.py (repo root, NOT in the package)
```python
"""py2app app entry point. Keep trivial; all logic lives in the package."""

from photos_tool.menubar import main

if __name__ == "__main__":
    main()
```

## NEW FILE: requirements-build.txt
```
# Build-only: produces dist/photos-tool.app via `python setup.py py2app`.
# Never added to runtime dependencies.
py2app>=0.28.8
```

## NEW FILE: setup.py
APP = ["app_main.py"]; DATA_FILES = []. PLIST keys:
- CFBundleName / CFBundleDisplayName = "photos-tool"
- CFBundleIdentifier = "com.dominicreiss.photos-tool"  (STABLE — never change post-grant)
- CFBundleShortVersionString / CFBundleVersion = "0.0.1"
- LSUIElement = True  (menu-bar-only, no Dock icon)
- LSMinimumSystemVersion = "13.0"; NSHighResolutionCapable = True
- NSPhotoLibraryUsageDescription = "photos-tool reads the photos you select so it can copy them to your backup share with their dates and metadata intact."
- NSPhotoLibraryAddUsageDescription = "photos-tool moves originals you have already backed up into Recently Deleted (recoverable for 30 days) so they can be cleared from this Mac."
- NSHumanReadableCopyright = "Copyright (c) 2026 Dominic Reiss. MIT License."

OPTIONS (py2app): argv_emulation=False (REQUIRED — Carbon loop breaks rumps), semi_standalone=False,
site_packages=False, arch="arm64", optimize=0,
packages=[photos_tool, rumps, osxphotos, certifi, rich, click, mako, markdown2, yaml, bpylist2,
photoscript, osxmetadata, cgmetadata, textx, whenever],
includes=[Photos, CoreFoundation, Foundation, AppKit, Quartz, AVFoundation, objc, pkg_resources],
excludes=[tkinter, test, lib2to3, pydoc_data], plist=PLIST.
(packages/includes are a STARTING point — expand `includes` on the first runtime ModuleNotFoundError;
build-verify required.)

## EDIT: src/photos_tool/menubar.py — replace `_executable()` with `_cli_prefix()`
```python
def _cli_prefix() -> list[str]:
    """argv prefix to run the photos-tool CLI, as a list.

    Frozen (py2app .app): the bundle's OWN embedded framework interpreter running
    ``-m photos_tool``, so the PhotoKit/osxphotos work is done by a binary inside
    the app's code signature and TCC attributes Photos to the app (the prompt reads
    "photos-tool", the grant is reused). NOTE: under py2app ``sys.executable`` is the
    app *stub*, not a usable interpreter -- running it would re-launch this menu-bar
    app -- so we must locate the real framework interpreter and never fall back to
    the stub.

    Dev/CI: the sibling ``photos-tool`` console script in the venv (today's behaviour),
    returned as a one-element list so callers always splat ``[*prefix, subcmd, ...]``.
    """
    is_frozen = getattr(sys, "frozen", False) or ".app/Contents/" in sys.executable
    if is_frozen:
        stub = Path(sys.executable).resolve()
        macos_dir = stub.parent  # .../Contents/MacOS
        contents = macos_dir.parent  # .../Contents
        ver = f"{sys.version_info.major}.{sys.version_info.minor}"  # e.g. 3.11
        fw_bin = contents / "Frameworks" / "Python.framework" / "Versions" / ver / "bin"
        candidates = [
            fw_bin / f"python{ver}",
            fw_bin / "python3",
            macos_dir / "python3",
            macos_dir / "python",
        ]
        for cand in candidates:
            if cand.exists() and not cand.samefile(stub):
                return [str(cand), "-m", "photos_tool"]
        raise RuntimeError(
            "bundled python interpreter not found next to the app stub; checked: "
            f"{[str(c) for c in candidates]}"
        )
    sibling = Path(sys.argv[0]).resolve().parent / "photos-tool"
    if sibling.exists():
        return [str(sibling)]
    return [shutil.which("photos-tool") or "photos-tool"]
```
- main(): bind `cli_prefix = _cli_prefix()` (replacing `exe = _executable()`).
- _do_send: `argv = build_send_argv(cli_prefix, album=album)`
- _do_cleanup_query: `[*cli_prefix, "cleanup-last", "--json"]`
- _do_cleanup_apply: `[*cli_prefix, "cleanup-last", "--yes"]`
- _do_doctor: `[*cli_prefix, "doctor"]`
- _env() unchanged.

## EDIT: src/photos_tool/gui_actions.py — `build_send_argv(cli_prefix: list[str], ...)`
First param `executable: str` → `cli_prefix: list[str]`; `argv = [*cli_prefix, "send"]`; rest unchanged.

## NEW FILE: scripts/build-app.sh
Build with venv python → `python setup.py py2app` → `codesign --force --deep --sign - dist/photos-tool.app`
→ verify (`codesign --verify --deep --strict`, `codesign -dv --verbose=4`) → print LSUIElement/CFBundleIdentifier
→ list the embedded interpreter path (to confirm it matches `_cli_prefix` candidates).
NOTE: `spctl -a -vv` will REJECT an ad-hoc app — EXPECTED, not a failure.

## Test updates (tests/test_gui_actions.py)
- build_send_argv now takes a list prefix: `build_send_argv(["photos-tool"]) == ["photos-tool", "send"]`;
  add frozen case `build_send_argv(["/p/python","-m","photos_tool"]) == ["/p/python","-m","photos_tool","send"]`.
- album/config + no-copy-flags tests: pass `["pt"]` instead of `"pt"`.
- menubar smoke test: replace `_executable()` with `_cli_prefix()`, assert non-empty `list[str]`.
- Grep suite for `_executable` and string-arg `build_send_argv("` and convert.

## Build/install/grant (on-device, no Terminal for the family member)
1. `.venv/bin/python -m pip install -r requirements-build.txt` then `./scripts/build-app.sh` → dist/photos-tool.app.
   Inspect `.../Contents/Frameworks/Python.framework/Versions/*/bin` and prune/reorder `_cli_prefix` candidates to match.
2. Drag photos-tool.app to /Applications; first launch right-click → Open → Open (Gatekeeper override).
3. Add to System Settings ▸ General ▸ Login Items (LSUIElement keeps it Dock-less).
4. Grant Full Disk Access to /Applications/photos-tool.app (macOS does NOT prompt for FDA); quit+relaunch.

## RISKIEST ASSUMPTION (verify live)
TCC responsible-process attribution for the in-bundle shell-out: that the child framework interpreter's
PhotoKit `requestAuthorizationForAccessLevel_handler_` is attributed UP to the .app, so the prompt reads
"photos-tool", the bundle id owns the grant (TCC.db auth_value=2), and it persists across relaunch.
Build-verify the interpreter path; ad-hoc has no stable DR so a rebuild may re-prompt.

## FALLBACK (if prompt shows "Python" or grant doesn't persist)
(a) re-verify `codesign -dv` shows the stable id + that you launched the .app; (b) `tccutil reset Photos
com.dominicreiss.photos-tool` and re-grant; (c) confirm the probed interpreter is the in-bundle one.
If it still leaks to "Python": PRE-WARM authorization once from the menu-bar MAIN process at startup —
in menubar.main(), before constructing the app, lazily `import Photos` and call
`PHPhotoLibrary.requestAuthorizationForAccessLevel_handler_(PHAccessLevelReadWrite, handler)` so consent
fires from the .app's own main process. Keep the destructive delete in the locked CLI child (ZERO safety
change — only moves WHERE consent is requested). Last resort: Developer-ID cert + hardened runtime +
notarization for a durable cross-rebuild grant.

## On-device acceptance checklist (the cardinal checks)
- Launch from Finder → exactly one 📷, NO Dock icon, not in Cmd-Tab.
- Grant FDA to the .app, relaunch; Run Diagnostics → all green.
- Send Selected → copies land on the share. (FAIL X-glyph ⇒ _cli_prefix found the stub not the real interpreter.)
- Clean up last backup → "Show me" reveals all present copies across date folders.
- **CARDINAL TCC CHECK:** the delete consent dialog title reads "photos-tool", NOT "Python"/"Terminal".
- **CARDINAL SAFETY CHECK:** after delete, originals in Recently Deleted (recoverable) AND share copies intact.
- Quit/relaunch + reboot: icon returns via Login Items; no re-prompt (grant reused).
