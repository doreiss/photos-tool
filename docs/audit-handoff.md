# photos-tool — Independent Adversarial Audit Handoff

> This document is a self-contained brief for an **external, independent auditor**.
> Assume no prior conversation and no knowledge of the authors' opinions about this
> code. Everything you need to judge it is below; everything you need to *read* is in
> the repository you have been given access to. Your job is to find what is wrong,
> risky, fragile, surprising, or over-built — **not** to confirm that it is good.

---

## 0. Your role and posture

You are a skeptical, adversarial code auditor with **no stake** in this project. Treat
every reassuring comment, docstring, commit message, and test name as a *claim to be
disproven*, not as evidence. Specifically:

- Do **not** trust a docstring that says "fail-closed", "atomic", "safe", or "verified"
  — prove from the code that it cannot fail open.
- Do **not** assume a green test proves what its name claims — read what it actually
  exercises, and check whether the test doubles (the fake `osxphotos`/`exiftool`/`ffmpeg`/
  `mount`/`osascript` shims, the PhotoKit mocks) faithfully mirror real behavior. A test
  that asserts against a fake that diverges from reality proves nothing.
- Default to skepticism. For any safety claim, ask "what's the input that breaks this?"
- An empty findings list for a lens is acceptable **only** if you genuinely tried to
  break it and could not.
- Distinguish real defects (give a concrete trigger / line of reasoning / reproduction)
  from style nits. Lead with defects.
- You may also note where the design is genuinely sound — but that is secondary.

---

## 1. What this software is (neutral description)

**photos-tool** is a small macOS tool (a Python CLI plus an optional menu-bar app) that
sends **selected** photos and videos from the Apple **Photos** app to a **Windows PC on
the same home LAN**, over a mounted SMB share, preserving metadata and Live Photos. It
is built for **one family's private use**, not the public.

Design shape:
- **Assemble, don't reinvent.** The heavy lifting (reading the Photos library, copying
  originals with metadata) is done by the third-party tool [`osxphotos`](https://github.com/RhetTbull/osxphotos),
  pinned to a specific version. This project is the thin wrapper that orchestrates it and
  adds safety/reconciliation/UX.
- **Manual push.** The user selects photos in Photos and triggers a send on demand (CLI,
  a hotkey Shortcut, or the menu-bar app). There is **no background daemon**.
- **Transport** is a mounted Windows SMB share; `osxphotos` writes directly onto the
  mount. The Windows archive is meant to be **append-only** (never pruned by this tool).
- **Optional Windows-friendly copies.** A `compat/` subtree can hold JPEG (for HEIC
  stills) and H.264 MP4 (for HEVC video) so a Windows PC with no codecs can browse them.
- **Optional, opt-in cleanup.** After a backup, the user can — as a *separate* step —
  move that batch's **originals** off the Mac into Photos' **Recently Deleted** (which is
  recoverable for ~30 days) via PhotoKit, to free space.
- **Multi-Mac.** Several family Macs may back up to one share; each writes to its own
  per-Mac subfolder.

Read the repository to learn the exact structure. Orienting pointers (verify against the
actual tree — do not assume these are complete or current):
- `src/photos_tool/` — `cli.py` (commands), `config.py`, `plan.py` (builds the osxphotos
  argv), `osxphotos_runner.py` (the subprocess boundary), `report.py` + `reconcile.py`
  (parse the export report, reconcile selected-vs-exported by UUID), `convert.py` (JPEG/
  MP4 compat copies), `smb.py` (mount checks), `remove.py` (PhotoKit deletion), `state.py`
  (the backup "token" that authorizes deletion), `tooling.py`, `gui_actions.py` (pure GUI
  logic) and `menubar.py` (the rumps view).
- Surfaces/commands today: `check`, `plan`, `send`, `doctor`, `init`, `install-shortcut`,
  `sanitize-report`, `cleanup-last`; plus the menu-bar app entry point.
- Persistence under `~/.local/state/photos-tool/`: a per-destination `osxphotos`
  export DB, a per-destination backup token, a `last-run.json`, and a lock file; plus
  `~/.config/photos-tool/config.toml`.
- Tests in `tests/` (a "fake binaries on PATH" harness lets the whole pipeline run without
  a Mac/Photos/Windows), CI in `.github/workflows/`, operator docs in `docs/`.

Runtime reality: **macOS only** at runtime (Apple Silicon, recent macOS). The CLI installs
and the pure logic runs cross-platform, but `osxphotos` and PhotoKit are macOS-only.

---

## 2. The owners' stated intentions and values — judge the code *against* these

These are the properties the software is *supposed* to have. Your job includes finding
where the code **fails to live up to its own stated values**.

1. **Never silently lose a photo.** This is the cardinal property. A photo is "lost" if
   it is reported as backed up but wasn't, or if an original is deleted from the Mac while
   its only copy on the share is missing, truncated, or actually a *different* photo.
2. **Deletion is opt-in, recoverable, verified, and separate from backup.** It must run
   only as its own explicit step; only after a clean reconciliation; only on originals
   whose copy is *currently* confirmed on the share (existence **and** recorded size);
   and it must land in Recently Deleted (recoverable), never a hard delete.
3. **Deterministic.** Same config + same inputs → same behavior. Minimal hidden state.
   The fewest moving parts and the fewest options. Preferences live in `config.toml`
   (set once at `init`), not in per-run flags or ephemeral GUI state.
4. **Simple enough for a non-technical family member**, GUI-first. Failures must be
   legible; there must be no silent no-ops.
5. **Privacy.** No secrets in argv, config, or the repo — the SMB password lives only in
   the macOS Keychain. GPS coordinates and person names (which live in the Photos
   library and the osxphotos export DB/report) must **not** be written to the share or
   left lying around on disk. The export DB and any report must stay local.
6. **Append-only Windows archive.** This tool must never prune the destination (e.g. it
   must never pass `osxphotos --cleanup`).
7. **Import-light and shell-safe.** Shell out to external tools via `subprocess` with
   **list args (never `shell=True`)**; do not import the `osxphotos` Python API.
8. **Honest about the unknown.** CI cannot exercise a real Photos library / GUI selection /
   Full Disk Access / a real Windows share; that gap is meant to be covered by a manual
   test, not papered over.

---

## 3. The single most important question

> **Is there *any* path by which this tool can silently lose an irreplaceable photo?**

That includes: an export that drops/skips assets but reports success; a reconciliation
that counts the wrong thing; a `cleanup-last` that deletes an original whose backup is
gone, truncated, replaced, on a different/remounted volume, or actually belongs to a
*different* asset; a multi-Mac collision; a partial/torn write to the record that
authorizes deletion; a PhotoKit call that resolves the wrong asset. Weight every finding
by its proximity to this question.

---

## 4. Adversarial viewpoints we are handing you (audit through each)

Adopt each of these personas in turn and try, in earnest, to break the software through
that lens. For each, we include a few sharp starting questions; go beyond them.

1. **The data-loss adversary.** Find one input/sequence where a photo is lost. Can
   `send` report success while assets were skipped/missing/errored? Can `cleanup-last`
   delete an original whose share copy is absent, zero-byte, truncated, a same-named
   *different* file, on a stale/remounted/empty-but-mounted volume, or after the config
   was repointed? Is the size/identity binding actually sufficient, or can two distinct
   photos satisfy it?

2. **The reconciliation-semantics skeptic.** The tool reconciles "selected" vs "exported"
   by UUID and treats some rows as exported. Is "selected count" the same unit as
   "exported count" (assets vs files; Live Photos; edited renditions; bursts)? When does
   a *skipped* row count as success, and is that ever wrong for the deletion gate? What
   happens with an empty selection, an album typo, a 100%-cloud-only selection?

3. **The determinism / hidden-state critic.** Enumerate every source of nondeterminism:
   reliance on the live GUI selection, on the local export DB for skip decisions, on the
   mount being present at a path, on `socket.gethostname()`, on timestamps, on ordering,
   on environment/PATH. Could the same config produce different results on two runs or two
   machines? Is any preference still hidden in ephemeral state rather than config?

4. **The concurrency / atomicity adversary.** Two Macs to one share; a double-pressed
   hotkey; the menu-bar app launched twice; a process killed mid-write; a send
   interrupted between the export and the token write; a cleanup interrupted mid-delete.
   Are the locks real and correctly scoped? Are whole-file state writes atomic and
   corruption-tolerant on read? Can an interrupted run leave a dangerous record?

5. **The macOS / TCC / permissions adversary.** Full Disk Access vs Photos (PhotoKit)
   authorization vs Automation — three different grants. Does the tool degrade gracefully
   when each is missing, or hang / fail confusingly? What about iCloud "Optimize Mac
   Storage" (cloud-only originals)? Mount timing and auto-mount races? What breaks if the
   pinned `osxphotos` version's report schema, CSV/`uuid` behavior, or flag set shifts?

6. **The non-technical-family-user adversary.** Walk the GUI and the setup as someone who
   does not read docs. Where is a failure silent, a state stuck, a dialog confusing, a
   footgun reachable? What happens on the very first run before any grant is given? Can a
   misconfiguration cause data loss or just confusion?

7. **The security / privacy adversary.** Can a secret reach argv, config, logs, or the
   repo? Is the SMB URL safely handled where it is interpolated (e.g. into AppleScript)?
   Do GPS coordinates / person names / raw library paths / UUIDs ever land on the share,
   in a log, in a committed fixture, or in a world-readable file? Is the mounted share
   itself a trust boundary the tool over-trusts? Is the `sanitize-report` actually
   sufficient before a fixture is committed?

8. **The simplicity / over-engineering critic.** The owners value *few moving parts*.
   Where is there dead code, an unnecessary abstraction, a config knob nobody needs, a
   persisted file nobody reads, two code paths that should be one, or accidental
   complexity that increases the chance of a bug? What could be deleted with no loss?

9. **The test-quality / false-confidence skeptic.** Do the fake binaries diverge from the
   real tools in a way that makes a passing test meaningless? Which real-world paths are
   untested (the actual `osxphotos` export, the real PhotoKit delete, the GUI threading,
   the real SMB mount)? Are there tests that assert the implementation rather than the
   behavior? Where would a real bug ship green?

10. **The GUI-lifecycle / threading adversary.** The menu-bar app runs subprocesses off
    the main thread and updates UI via a timer. Is AppKit ever touched off the main
    thread? Can the menu freeze, get stuck on a working state, double-run a job, or leak
    a process? What happens on quit-during-work, on a launch-from-Finder vs from a shell,
    on a stale PATH?

11. **The maintainability / future adversary.** `osxphotos` is a fast-moving,
    single-maintainer dependency pinned to one version. What breaks on an upgrade, and is
    there a guard? Are the on-disk records schema-versioned and migration-safe? What
    undocumented assumptions (e.g. PhotoKit local-identifier conventions, report column
    names, mount path shapes) are load-bearing and unverified at runtime?

12. **The "works on my machine" / CI-vs-reality adversary.** Exactly which guarantees does
    CI actually establish, and which does it merely *appear* to? List the claims a green
    pipeline does **not** justify, and the smallest real-environment test that would.

---

## 5. Now generate your *own* adversarial viewpoints

We are blind to our own blind spots; the list above is necessarily incomplete and
biased toward what we already worry about. **Invent at least five additional adversarial
angles or personas that we did not list**, state what each is looking for, and audit
through them. Prioritize angles that could (a) cause silent data loss, (b) be hit by a
non-technical family member, or (c) expose a privacy leak. Explicitly flag any assumption
this brief itself makes that you think is unsafe.

---

## 6. Scope and where to dig hardest

Read the entire repository — every module, the tests, the CI workflows, and the docs.
Spend the most effort where a mistake is irreversible or invisible:
- the deletion path (`remove.py`, `state.py`, and the `cleanup-last` flow in `cli.py`):
  the backup token, the size/identity binding, the mount re-validation, the
  consume-after-delete, the PhotoKit resolution;
- the reconciliation (`report.py`, `reconcile.py`): counting semantics and the
  no-uuid / skipped-row handling;
- the export boundary (`plan.py`, `osxphotos_runner.py`): the emitted argv, the
  `--cleanup` guard, and whether the report is trusted correctly;
- the mount logic (`smb.py`) and any place the SMB URL is interpolated;
- the GUI threading (`menubar.py`) and its pure/view split;
- the test doubles (`tests/fakebin/`, the PhotoKit mocks) vs the real tools.

---

## 7. Deliverable

Produce a written audit with:
- **Per finding:** a one-line title; a severity (`blocker` / `high` / `medium` / `low` /
  `nit`); the `file:line`; a concrete trigger, reproduction, or line of reasoning that
  shows it is real (not a hunch); and a recommended fix.
- **A ranked list** ordered by impact on the owners' values (§2), with anything touching
  §3 — silent photo loss — at the top.
- **An overall verdict:** ship / ship-with-fixes / needs-work, in one paragraph, honest
  about both the genuine strengths and the genuine risks.
- **The top residual unknowns:** the things that *cannot* be settled by reading the code
  and would need a real Mac + real Windows share to confirm — and the smallest experiment
  that would settle each.
- **Your invented lenses (§5)** and what they turned up.

Be specific, be adversarial, and assume the authors were too close to the code to see
their own mistakes — your value is in seeing what they could not.
