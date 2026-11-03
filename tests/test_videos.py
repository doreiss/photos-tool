from __future__ import annotations

from collections import Counter
from pathlib import Path

from photos_tool import convert, videos


def _row(uuid: str, filename: str, **flags: bool) -> dict:
    base = {"uuid": uuid, "filename": filename, "exported": True, "new": True, "skipped": False}
    base.update({"missing": False, "error": False, **flags})
    return base


def test_publish_matches_rows_reported_through_a_resolved_symlink(tmp_path: Path, fake_tools):
    # Regression (found on a real Mac): the scratch dir is reached via a symlink (/var ->
    # /private/var) but osxphotos reports the RESOLVED path. The row must still map to its MP4.
    fake_tools({})
    real = tmp_path / "private" / "staging"
    (real / "2024" / "09").mkdir(parents=True)
    (real / "2024" / "09" / "clip.mov").write_bytes(b"video")
    link = tmp_path / "staging-link"
    link.symlink_to(real)
    share = tmp_path / "share"
    counts: Counter[str] = Counter()

    row = videos._publish(_row("U1", str(real / "2024" / "09" / "clip.mov")), link, share, counts)

    assert counts == Counter(converted=1)
    assert row["filename"] == str(share / "2024" / "09" / "clip.mp4")
    assert convert.read_stamp(Path(row["filename"]))[0] == "U1"


def test_publish_passes_missing_and_error_rows_through_untouched(tmp_path: Path, fake_tools):
    tools = fake_tools({})
    counts: Counter[str] = Counter()
    missing = _row("U2", "", exported=False, missing=True)
    errored = _row("U3", "/x/y.mov", exported=False, error=True)

    assert videos._publish(missing, tmp_path, tmp_path / "share", counts) == missing
    assert videos._publish(errored, tmp_path, tmp_path / "share", counts) == errored
    assert not counts
    assert not [e for e in tools.log() if e["tool"] == "avconvert"]


def test_target_for_walks_from_the_base_name_by_identity(tmp_path: Path, fake_tools):
    fake_tools({})
    (tmp_path / "clip.mp4").write_text("theirs\nidentity=OTHER\n", encoding="utf-8")
    (tmp_path / "clip (1).mp4").write_text("no stamp at all\n", encoding="utf-8")
    (tmp_path / "clip (2).mp4").write_text("mine\nidentity=ME\nsignature=S1\n", encoding="utf-8")
    first = tmp_path / "clip.mp4"

    # Ours and unchanged: reuse. Ours but the video changed since: remake it in place.
    assert videos._target_for(first, "ME", "S1") == (tmp_path / "clip (2).mp4", True)
    assert videos._target_for(first, "ME", "S2") == (tmp_path / "clip (2).mp4", False)
    # Someone new: the first free name. Without a UUID nothing is ever reused.
    assert videos._target_for(first, "NEW", "S") == (tmp_path / "clip (3).mp4", False)
    assert videos._target_for(first, "", "S1") == (tmp_path / "clip (3).mp4", False)
    # osxphotos' in-run "(n)" suffix varies between runs: always search from the base name, so
    # a re-send finds the earlier MP4 instead of converting a duplicate.
    assert videos._target_for(tmp_path / "clip (1).mp4", "ME", "S1") == (
        tmp_path / "clip (2).mp4",
        True,
    )


def test_a_video_edited_after_sending_is_remade_not_reused(tmp_path: Path, fake_tools):
    # Safety: if a trimmed/edited video reused its old MP4, cleanup would delete the asset against
    # a stale copy and lose the edit. A changed source must be converted again, in place.
    tools = fake_tools({})
    staging = tmp_path / "staging"
    (staging / "2024").mkdir(parents=True)
    source = staging / "2024" / "clip.mov"
    share = tmp_path / "share"

    source.write_bytes(b"original cut")
    first = videos._publish(_row("U1", str(source)), staging, share, Counter())
    source.write_bytes(b"trimmed in Photos - different content")
    counts: Counter[str] = Counter()
    second = videos._publish(_row("U1", str(source)), staging, share, counts)

    assert first["filename"] == second["filename"] == str(share / "2024" / "clip.mp4")
    assert counts == Counter(converted=1)  # remade, not reused
    assert len([e for e in tools.log() if e["tool"] == "avconvert"]) == 2
    assert sorted(p.name for p in (share / "2024").iterdir()) == ["clip.mp4"]  # replaced in place


def test_a_filesystem_error_fails_that_video_only(tmp_path: Path, fake_tools):
    fake_tools({})
    staging = tmp_path / "staging"
    staging.mkdir()
    source = staging / "clip.mov"
    source.write_bytes(b"video")
    blocked = tmp_path / "share"
    blocked.write_text("a file where the share folder should be", encoding="utf-8")
    counts: Counter[str] = Counter()

    row = videos._publish(_row("U1", str(source)), staging, blocked, counts)

    assert counts == Counter(failed=1)
    assert row["error"] is True and row["filename"] == ""


def test_target_for_finds_its_own_mp4_across_a_numbering_gap(tmp_path: Path, fake_tools):
    # clip (1).mp4 was deleted (or never made): C's MP4 at clip (2) must still be found, not
    # duplicated into the free clip (1) slot.
    fake_tools({})
    (tmp_path / "clip.mp4").write_text("A\nidentity=A\nsignature=SA\n", encoding="utf-8")
    (tmp_path / "clip (2).mp4").write_text("C\nidentity=C\nsignature=SC\n", encoding="utf-8")

    assert videos._target_for(tmp_path / "clip.mp4", "C", "SC") == (tmp_path / "clip (2).mp4", True)


def test_target_for_keeps_a_real_name_that_looks_like_a_collision_suffix(tmp_path, fake_tools):
    # A video genuinely named "Clip (2).mov" keeps that name on the share when it's free.
    fake_tools({})
    assert videos._target_for(tmp_path / "Clip (2).mp4", "U", "S") == (
        tmp_path / "Clip (2).mp4",
        False,
    )
