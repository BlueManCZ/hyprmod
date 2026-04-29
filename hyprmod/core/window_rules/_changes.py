"""Change-tracking and reorder helpers for the rule list.

Mirrors :mod:`hyprmod.core.autostart` for the equivalent operations on
its own list type. The page (:mod:`hyprmod.pages.window_rules`) calls
into here to compute pending-change counts for the sidebar badge and
the diff in :mod:`hyprmod.pages.pending`, and to translate
drag-and-drop hovers into ``SavedList.move`` arguments.
"""

from collections.abc import Iterator
from typing import Literal

from hyprmod.core.window_rules._model import WindowRule

# ---------------------------------------------------------------------------
# Drag-and-drop helpers (mirrors core.autostart)
# ---------------------------------------------------------------------------


def drop_target_idx(src: int, hover: int, before: bool) -> int:
    """Translate a drag-and-drop hover into a ``SavedList.move`` target.

    See :func:`hyprmod.core.autostart.drop_target_idx` for the
    derivation; same semantics here.
    """
    if before:
        return hover - 1 if src < hover else hover
    return hover if src < hover else hover + 1


# ---------------------------------------------------------------------------
# Change tracking (mirrors core.autostart)
# ---------------------------------------------------------------------------


ChangeKind = Literal["added", "modified", "removed"]


def detect_reorder(saved: list[WindowRule], current: list[WindowRule]) -> bool:
    """True if entries common to both lists appear in different relative order."""
    saved_lines = [e.to_line() for e in saved]
    current_lines = [e.to_line() for e in current]
    common = set(saved_lines) & set(current_lines)
    if len(common) < 2:
        return False
    saved_positions = [line for line in saved_lines if line in common]
    current_positions = [line for line in current_lines if line in common]
    return saved_positions != current_positions


def iter_item_changes(
    saved: list[WindowRule],
    current: list[WindowRule],
    current_baselines: list[WindowRule | None],
) -> Iterator[tuple[ChangeKind, int, WindowRule, WindowRule | None]]:
    """Yield per-item add/modify/remove changes."""
    if len(current) != len(current_baselines):
        raise ValueError(
            "current and current_baselines must be the same length "
            f"(got {len(current)} vs. {len(current_baselines)})"
        )

    surviving_baselines: set[str] = set()
    for idx, (item, baseline) in enumerate(zip(current, current_baselines, strict=True)):
        if baseline is None:
            yield "added", idx, item, None
        else:
            surviving_baselines.add(baseline.to_line())
            if baseline.to_line() != item.to_line():
                yield "modified", idx, item, baseline
    for s in saved:
        if s.to_line() not in surviving_baselines:
            yield "removed", -1, s, None


def count_pending_changes(
    saved: list[WindowRule],
    current: list[WindowRule],
    current_baselines: list[WindowRule | None],
) -> int:
    """Total pending-change entries: per-item changes + reorder roll-up."""
    count = sum(1 for _ in iter_item_changes(saved, current, current_baselines))
    if detect_reorder(saved, current):
        count += 1
    return count
