# No-Terminal app delivery — empirical findings (for the research workflow)

> **RESOLVED.** This research shipped: the delivery is a PyInstaller `--windowed` `.app` built by
> [`scripts/build-app.sh`](../scripts/build-app.sh) from [`packaging/`](../packaging), signed with
> a stable self-signed identity, bundling its own exiftool. Install + onboarding:
> [docs/app-install.md](app-install.md). The findings below are kept as the rationale.

GOAL: a non-technical family member runs the photos-tool **menu-bar app without ever using a Terminal**,
with all of these working:
1. the 📷 **status-bar item appears** reliably,
2. **Send** works (the app shells out to the `photos-tool` CLI → `osxphotos export`; needs Full Disk Access),
3. **Clean up last backup** works — the PhotoKit **delete authorization prompt appears** (ideally named
   "photos-tool"), and the recoverable delete succeeds,
4. it's launchable like a normal app (Finder / Login Items), installable without a Terminal.

ARCHITECTURE (important): the menu-bar app (`photos_tool.menubar`, rumps) is a THIN GUI that **shells out**
to the `photos-tool` CLI (`photos_tool.cli`), which shells out to the **`osxphotos` binary** and (for the
recoverable delete) imports **pyobjc `Photos`** (PhotoKit). photos_tool deliberately does NOT import
osxphotos as a library. Config in `~/.config/photos-tool/config.toml`; state in `~/.local/state/photos-tool`.
Constraints: no secrets in argv/config (SMB password via macOS native mount → Keychain only); minimal /
few moving parts; never weaken the delete-safety gate.

## What we have EMPIRICALLY established (do not redo; build on these)

1. **A direct process launch shows the icon + works.** Launching `.venv/bin/photos-tool-menubar` via
   `nohup … &` from a shell in the user's login session shows the 📷 and the menu works (we drove the
   onboarding flow through it). So rumps + the venv app are fine; the issue is the *delivery/launch context*.

2. **An orphaned launch (ppid=launchd, via nohup from a non-GUI automation shell) → the PhotoKit delete
   prompt NEVER appears** ("photos-tool is not authorized to modify the Photos library"). TCC has no
   GUI-session responsible app to attribute the Photos permission to, so it can't present the prompt.
   Launching from the user's Terminal made the prompt appear (attributed to Terminal) — but Terminal is
   not an acceptable answer.

3. **py2app STANDALONE bundle = a 90-package slog.** Because photos_tool shells out to osxphotos (never
   imports it), py2app's modulegraph can't discover osxphotos's dependency tree. osxphotos's import
   closure is ~90 packages incl. heavy C-extensions (cffi, psutil, xattr, bitarray) and ~20 pyobjc
   frameworks (Vision, CoreML, Metal, Contacts, CoreLocation, …) plus lazy imports. We hit, in order:
   non-framework pyenv python → bundle interpreter leaked back to pyenv; setuptools 80 incompat; stale
   `setup_requires`; pyproject `[project].dependencies` → `install_requires` rejected by py2app; and
   finally the bundled `MacOS/python` run bare has NO bundle sys.path (py2app's `__boot__` only runs for
   the app stub, not a `MacOS/python -m photos_tool` child) AND osxphotos deps like `tenacity`/`bitmath`
   were never bundled. The bundle's MAIN process launched (icon showed) but the CLI-child shell-out was
   broken. Verdict: standalone-bundling osxphotos with py2app is fragile and very long; TCC still unproven.

4. **Lightweight wrapper .app FAILS to show the status item.** We built a tiny `photos-tool.app`
   (ad-hoc signed, stable `CFBundleIdentifier=com.dominicreiss.photos-tool`, `LSUIElement=true`) whose
   `Contents/MacOS/photos-tool` is a bash launcher that `exec`s the installed `.venv/bin/photos-tool-menubar`.
   The python process RUNS (confirmed via `pgrep`), produces NO output and NO error, but **no 📷 icon
   appears — even when double-clicked from Finder.** Hypothesis: a .app whose executable execs a DIFFERENT
   binary (a python outside the bundle) loses the LaunchServices↔process registration that a process needs
   to draw an NSStatusBar item. (A proper py2app bundle, whose executable IS the python, did show the icon.)

## The open questions for research
- WHY does the wrapper (script→exec) not show the status item, while a direct launch and a proper bundle
  do? What EXACTLY must a process satisfy to display a menu-bar NSStatusBar item (WindowServer/Aqua
  session connection, LaunchServices registration, being the app's own process vs an exec'd foreign
  binary)? Is there a wrapper fix, or is a wrapper fundamentally unable to host the status item?
- Is **PyInstaller** (`--windowed --onedir`, `--collect-all osxphotos`, collect the pyobjc frameworks) a
  better tool than py2app here — does it produce a proper .app (real Mach-O launcher → icon works, .app =
  TCC identity) and bundle osxphotos's tree more reliably?
- Is a **LaunchAgent** (run the installed `photos-tool-menubar` at login via launchd in the Aqua session)
  the most reliable path — icon shows (it's a direct GUI-session launch), Photos prompt appears (named
  "Python")? What's the exact plist + `launchctl` flow + a `photos-tool autostart` command?
- For EACH option, what will the **Photos delete prompt + Full Disk Access** attribute to ("photos-tool"
  vs "Python"), and will they reliably appear?

## Decision criteria (rank options by)
reliability of the status icon · whether the Photos/TCC prompt appears (and its name) · no-Terminal
install + launch for a non-technical user · maintenance complexity / few-moving-parts · self-contained vs
depends-on-installed-tool. Recommend ONE with high confidence + a concrete, executable plan (commands +
code/build changes + a live on-device test checklist for the icon + TCC).
