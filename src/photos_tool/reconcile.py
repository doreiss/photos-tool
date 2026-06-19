"""Reconcile how many photos were selected against how many were exported.

This guards the single biggest silent-loss trap: with iCloud "Optimize Mac
Storage" enabled, originals may be cloud-only and get skipped, so the export
quietly contains fewer items than were selected. Comparing the counts surfaces
that instead of letting it pass unnoticed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Status(str, Enum):
    OK = "ok"
    EMPTY = "empty"
    SKIPPED = "skipped"
    OVER = "over"


@dataclass(frozen=True)
class Reconciliation:
    selected: int
    exported: int
    missing: int
    status: Status
    ok: bool
    message: str


def reconcile(selected: int, exported: int, missing: int = 0) -> Reconciliation:
    """Compare selected vs exported asset counts and classify the outcome.

    ``selected`` and ``exported`` are asset counts (one Live Photo or one video is
    one asset, even though it may land as several files). ``missing`` is the number
    osxphotos reported as unavailable (e.g. cloud-only originals).
    """
    if min(selected, exported, missing) < 0:
        raise ValueError("counts must be non-negative")

    if selected == 0:
        return Reconciliation(
            selected,
            exported,
            missing,
            Status.EMPTY,
            ok=False,
            message="Nothing was selected — select photos in Photos before sending.",
        )

    if missing > 0 or exported < selected:
        gap = max(selected - exported, missing)
        return Reconciliation(
            selected,
            exported,
            missing,
            Status.SKIPPED,
            ok=False,
            message=(
                f"{gap} of {selected} selected item(s) were not exported. "
                "This usually means iCloud 'Optimize Mac Storage' placeholders — turn on "
                "'Download Originals to this Mac', let it finish, and retry."
            ),
        )

    if exported > selected:
        return Reconciliation(
            selected,
            exported,
            missing,
            Status.OVER,
            ok=True,
            message=(
                f"Exported {exported} files from {selected} selected item(s); the extras are "
                "Live Photo motion or edited renditions."
            ),
        )

    return Reconciliation(
        selected,
        exported,
        missing,
        Status.OK,
        ok=True,
        message=f"All {selected} selected item(s) exported.",
    )
