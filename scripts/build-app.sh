#!/usr/bin/env bash
# Build, sign (stable self-signed cert or ad-hoc), and verify the no-Terminal photos-tool
# menu-bar .app via PyInstaller. Run from anywhere:  ./scripts/build-app.sh [--install]
#   --install   also copy the built app to /Applications
#
# The frozen app is one signed binary that self-reinvokes (--pyi-cli / --pyi-osxphotos), so
# osxphotos and the PhotoKit delete run inside the app's own code signature (clean "photos-tool"
# TCC identity). It signs with the stable "photos-tool Self-Signed" identity when present (run
# packaging/create-codesign-cert.sh once) so the macOS grants persist across rebuilds; otherwise
# it ad-hoc signs (grants re-prompt each rebuild). EITHER WAY the build is un-notarized, so
# `spctl -a -vv` reports REJECTED and the first launch needs a one-time right-click > Open.
# (Standalone py2app was rejected — it can't bundle osxphotos's ~90-package tree; see
# docs/app-delivery-findings.md.)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Build venv with pyinstaller + the editable package + osxphotos/pyobjc. A framework Python
# (Homebrew python@3.11) makes the most portable bundle; overridable for CI.
PY="${PHOTOS_TOOL_BUILD_PYTHON:-$REPO/.venv-app/bin/python}"
SPEC="$REPO/packaging/photos-tool.spec"
APP="$REPO/dist/photos-tool.app"
BUNDLE_ID="com.dominicreiss.photos-tool"
INSTALL=0
[ "${1:-}" = "--install" ] && INSTALL=1

cd "$REPO"

if [ ! -x "$PY" ]; then
  echo "ERROR: build venv not found at $PY. Create it once:" >&2
  echo "  /opt/homebrew/opt/python@3.11/bin/python3.11 -m venv .venv-app" >&2
  echo "  .venv-app/bin/python -m pip install -e '.[gui]' -r requirements-build.txt" >&2
  exit 1
fi
if ! "$PY" -c "import sysconfig,sys; sys.exit(0 if sysconfig.get_config_var('PYTHONFRAMEWORK') else 1)"; then
  echo "WARNING: $PY is not a framework build; the bundle should still work, but Homebrew" >&2
  echo "         python@3.11 is recommended for the most portable .app." >&2
fi

echo "==> Ensuring the build toolchain is present"
"$PY" -c "import PyInstaller" 2>/dev/null || "$PY" -m pip install -r requirements-build.txt
"$PY" -c "import photos_tool" 2>/dev/null || "$PY" -m pip install -e '.[gui]'

echo "==> Ensuring the bundled exiftool is present"
# Ship our own exiftool (script + Perl lib tree, run via the system /usr/bin/perl) so the .app
# embeds Photos metadata on a clean Mac with no Homebrew. Fetched (not committed) from a pinned
# GitHub tag + verified by sha256, so the repo stays lean and the build is reproducible.
EXIFTOOL_VER=13.30
EXIFTOOL_SHA=52c034031714f05b556776a4e458124947d561b752c8b24a6740ac0f718af9bd
EXIFTOOL_DIR="$REPO/packaging/exiftool"
if [ ! -x "$EXIFTOOL_DIR/exiftool" ]; then
  echo "  fetching exiftool $EXIFTOOL_VER"
  TARBALL="$(mktemp -t exiftool-tgz)"
  curl -fsSL -o "$TARBALL" \
    "https://github.com/exiftool/exiftool/archive/refs/tags/${EXIFTOOL_VER}.tar.gz"
  echo "${EXIFTOOL_SHA}  ${TARBALL}" | shasum -a 256 -c - \
    || { echo "ERROR: exiftool tarball checksum mismatch" >&2; exit 1; }
  TMPX="$(mktemp -d)"
  tar xzf "$TARBALL" -C "$TMPX"
  rm -rf "$EXIFTOOL_DIR"
  mkdir -p "$EXIFTOOL_DIR"
  cp "$TMPX/exiftool-${EXIFTOOL_VER}/exiftool" "$EXIFTOOL_DIR/exiftool"
  cp -R "$TMPX/exiftool-${EXIFTOOL_VER}/lib" "$EXIFTOOL_DIR/lib"
  chmod +x "$EXIFTOOL_DIR/exiftool"
  rm -rf "$TMPX" "$TARBALL"
fi
"$EXIFTOOL_DIR/exiftool" -ver >/dev/null || { echo "ERROR: bundled exiftool does not run" >&2; exit 1; }

echo "==> Cleaning previous build"
rm -rf "$REPO/build" "$APP"

echo "==> Building with PyInstaller"
"$PY" -m PyInstaller --noconfirm --log-level WARN "$SPEC"

# PyInstaller bundles the exiftool script as DATA (no +x); restore it so shutil.which finds it
# inside the app. Do it BEFORE signing so the signature seals the executable bit. Path differs
# by PyInstaller version (Contents/Frameworks vs MacOS), so locate it.
BUNDLED_EXIFTOOL="$(find "$APP/Contents" -type f -path '*/exiftool/exiftool' | head -1)"
[ -n "$BUNDLED_EXIFTOOL" ] || { echo "ERROR: bundled exiftool missing from the built .app" >&2; exit 1; }
chmod +x "$BUNDLED_EXIFTOOL"

echo "==> Code signing"
# Prefer the stable self-signed identity if it exists (DR = identifier + cert leaf, so TCC
# grants survive rebuilds). Otherwise ad-hoc (cdhash DR changes every build -> re-grant each
# reinstall). NO --options runtime: Hardened Runtime's Library Validation rejects PyInstaller's
# no-Team-ID dylibs ("mapped file has no Team ID"); the build is intentionally un-notarized.
CODESIGN_CN="photos-tool Self-Signed"
SIGNED_STABLE=0
# NB: no -v. A self-signed identity is "not trusted" (no CA chain), so `find-identity -v`
# hides it — but codesign signs with it fine (it never evaluates trust).
if security find-identity -p codesigning 2>/dev/null | grep -qF "$CODESIGN_CN"; then
  echo "  signing with stable identity '$CODESIGN_CN' (grants persist across rebuilds)"
  codesign --force --deep --sign "$CODESIGN_CN" --identifier "$BUNDLE_ID" "$APP"
  SIGNED_STABLE=1
else
  echo "  ad-hoc signing — TCC grants will NOT survive a rebuild. For persistence, run"
  echo "  'packaging/create-codesign-cert.sh' once, then rebuild."
  codesign --force --deep --sign - "$APP"
fi

echo "==> Verifying signature + bundle shape"
codesign --verify --deep --strict --verbose=2 "$APP"
# Capture each codesign output to a variable (runs to completion) then test the string. Piping
# `codesign -dv ... | grep -q` lets grep close the pipe early; codesign then takes SIGPIPE and,
# under `set -o pipefail`, the whole `if` flips to the wrong branch (a bogus identifier warning).
sig_info="$(codesign -dv --verbose=2 "$APP" 2>&1)"
case "$sig_info" in
  *"Identifier=$BUNDLE_ID"*) echo "  signed as $BUNDLE_ID" ;;
  *) echo "  WARNING: signed identifier is not $BUNDLE_ID" >&2 ;;
esac
if [ "$SIGNED_STABLE" = "1" ]; then
  dr_info="$(codesign -d -r- "$APP" 2>&1)"
  case "$dr_info" in
    *"identifier \"$BUNDLE_ID\" and certificate leaf"*)
      echo "  designated requirement is identifier+leaf (TCC grants persist across rebuilds)" ;;
    *)
      echo "ERROR: DR is not identifier+leaf — grants would not persist; check the cert" >&2
      exit 1 ;;
  esac
fi
EXE="$APP/Contents/MacOS/photos-tool"
if ! file "$EXE" | grep -q "Mach-O"; then
  echo "ERROR: launcher is not a Mach-O binary (the icon would not draw)" >&2
  exit 1
fi
lipo -archs "$EXE" 2>/dev/null | grep -q arm64 || echo "  WARNING: launcher is not arm64"
# Exhaustive Info.plist gate: a missing NSAppleEventsUsageDescription makes tccd SILENTLY
# refuse the Apple Events request (no prompt, no Automation-pane entry) — the bug that cost
# days. Verify every load-bearing key on the EMITTED artifact, not just two of them.
PLIST="$APP/Contents/Info.plist"
plutil -lint "$PLIST" >/dev/null  # structurally valid plist
for key in NSAppleEventsUsageDescription NSPhotoLibraryUsageDescription \
           NSPhotoLibraryAddUsageDescription LSUIElement CFBundleIdentifier; do
  if ! /usr/libexec/PlistBuddy -c "Print :$key" "$PLIST" >/dev/null 2>&1; then
    echo "ERROR: Info.plist missing $key (TCC would silently refuse the grant)" >&2
    exit 1
  fi
done

echo "==> Smoke-testing self-reinvocation (proves osxphotos resolves inside the bundle)"
"$EXE" --pyi-cli --version
# Capture each producer to a variable (runs it to completion, no early pipe reader) rather than
# piping to head/grep, which close the read end and can SIGPIPE the producer under `pipefail`.
osx_version="$("$EXE" --pyi-osxphotos --version)"
printf '  osxphotos: %s\n' "${osx_version%%$'\n'*}"
# Side-effect-free: proves AppKit/Foundation/ctypes + the CoreServices consent symbol all
# survived PyInstaller collection (what request_photos_automation needs at runtime), with NO
# consent dialog. Do NOT smoke --pyi-prime-photos here — it pops a prompt and hangs the build.
prime_out="$("$EXE" --pyi-prime-imports)"
case "$prime_out" in
  *"pyi-prime-imports OK"*) ;;
  *) echo "ERROR: --pyi-prime-imports smoke failed: $prime_out" >&2; exit 1 ;;
esac
# Prove the bundled exiftool runs (script + Perl lib found, executable) so the .app can embed
# metadata on a clean Mac with no Homebrew.
et_version="$("$BUNDLED_EXIFTOOL" -ver)"
printf '  bundled exiftool: %s\n' "${et_version%%$'\n'*}"

if [ "$INSTALL" = "1" ]; then
  echo "==> Installing to /Applications"
  rm -rf /Applications/photos-tool.app
  cp -R "$APP" /Applications/photos-tool.app
  echo "  installed /Applications/photos-tool.app"
fi

echo
echo "NOTE: 'spctl -a -vv $APP' reports REJECTED (ad-hoc, no notarization) — that is EXPECTED."
echo "Built:  $APP"
echo "Next:   drag to /Applications, right-click > Open once, add to Login Items, and grant"
echo "        Full Disk Access, then Automation->Photos (first Send) and Photos (first cleanup)."
