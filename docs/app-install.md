# Installing the photos-tool menu-bar app (no Terminal for the family member)

This builds, signs, and installs the no-Terminal `.app`. The `.app` bundles its own exiftool
(run via the system perl) and self-reinvokes its own signed binary, so it needs **no Homebrew**
on the target Mac. **Apple-Silicon only** (the bundle is arm64).

Two ways to use it:

- **Same Mac** — you build *and* run backups on this Mac: do sections 1–4 below.
- **A different / fresh Mac runs the backups** (e.g. a family MacBook) — you do NOT need Xcode,
  Homebrew, Python, or the cert on that Mac. Build + sign **once** on your dev Mac (sections 1–3),
  run `./scripts/package-dmg.sh`, and send them `dist/photos-tool.dmg` — see section 5.

## 1. One-time build environment

```bash
cd ~/dev/photos-tool
/opt/homebrew/opt/python@3.11/bin/python3.11 -m venv .venv-app   # a framework Python builds the most portable bundle
.venv-app/bin/python -m pip install -e '.[gui]' -r requirements-build.txt
```

## 2. Create the stable signing identity (once — so grants persist)

```bash
./packaging/create-codesign-cert.sh   # enter your login/keychain password when asked
```

This creates a self-signed `photos-tool Self-Signed` identity. Signing the app with it gives a
designated requirement of `identifier + certificate leaf` (not a per-build cdhash), so the macOS
grants you give the app **survive every future rebuild**. It does *not* make the app notarized.

(Skip this and the app still works, ad-hoc signed — but you'd re-grant after each rebuild.)

## 3. Build + install

```bash
./scripts/build-app.sh --install
```

It fetches + verifies exiftool, builds with PyInstaller, signs with the stable identity (you'll
see `designated requirement is identifier+leaf`), smoke-tests the bundle, and copies it to
`/Applications`. On the first build, click **Always Allow** if macOS asks codesign to use the key.

> **Only if you previously ran an ad-hoc-signed build of this app**, clear the old identity's
> grants once so they don't ghost (a *fresh* Mac skips this):
> ```bash
> tccutil reset SystemPolicyAllFiles com.dominicreiss.photos-tool
> tccutil reset AppleEvents          com.dominicreiss.photos-tool
> tccutil reset Photos               com.dominicreiss.photos-tool
> ```

## 4. First launch + the one-time grants (nominal onboarding)

The app is un-notarized (expected; `spctl` reports REJECTED), so the first open needs Gatekeeper's
manual approval:

1. **Open it once:** right-click `/Applications/photos-tool.app` → **Open** → **Open**.
2. **Login Items:** System Settings → General → Login Items → add photos-tool (so it starts at login).
3. **Full Disk Access** (the one grant macOS never prompts for): System Settings → Privacy &
   Security → Full Disk Access → enable photos-tool → **quit and reopen** the app.
4. **Set up connection:** click the 📷 menu → *Set up connection…* → enter `smb://<pc>/<share>` and
   choose the format (*Viewable*, the default, or *Original files*; see the README's "What lands on
   the share")
   (macOS's own dialog handles the password into your Keychain; the tool never sees it).
5. **Automation → Photos:** the first **Send Selected Photos** pops *"photos-tool" wants to
   control "Photos"* → **Allow** (this lets it read your selection).
6. **Photos:** the first **Clean up last backup** asks for Photos access → **Allow** (for the
   recoverable delete).

Steps 3, 5, 6 are the only permissions, granted once. Because the app is signed with the stable
identity, **they persist** — rebuilding/reinstalling never re-prompts. Run *Run Diagnostics* to
confirm every check is green.

## 5. Receiving a prebuilt .app on a fresh Mac (no build tools)

When the backups run on a *different* Apple-Silicon Mac than the one you built on, that Mac needs
nothing from Homebrew/Python/Xcode — the bundle is self-contained (its own Python, exiftool, and
osxphotos). The cert's private key is **not** needed there either: the signature self-validates
offline, and TCC grants are per-Mac, so that Mac grants once and they persist across updates.

On your Mac, after `./scripts/build-app.sh`:

```bash
./scripts/package-dmg.sh   # -> dist/photos-tool.dmg (app + Applications shortcut + setup guide)
```

Send the `.dmg` by AirDrop or via the share. It contains **Read Me First.txt**
([docs/family-guide.md](family-guide.md)), a plain-language walkthrough of section 4 for the
recipient. They skip the `tccutil reset` in section 3: it only applies to a Mac that previously ran
an *ad-hoc*-signed build.

To ship an update later, rebuild, re-run `package-dmg.sh`, and have them replace the app in
`/Applications`. Their Full Disk Access / Automation / Photos grants carry over because the stable
signing identity is unchanged (macOS may ask for the "Open Anyway" approval again for the new copy).
