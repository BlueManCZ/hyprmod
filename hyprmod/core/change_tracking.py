"""Generic change-tracking primitives for ordered, line-serialisable lists.

The autostart, env-var, layer-rule, and window-rule pages all maintain a
``SavedList`` of items with a ``to_line() -> str`` method, and they all
need the same four operations:

- translate a drag-and-drop hover into a ``SavedList.move`` target,
- detect whether common items have been reordered,
- yield per-item add/modify/remove changes,
- count total pending changes for the sidebar badge.

Before this module each page reimplemented all four. The behaviour is
identical across types so it lives here once, parameterised on a
``LineSerialisable`` protocol.
"""

from collections.abc import Iterator
from typing import Literal, Protocol, TypeVar


class LineSerialisable(Protocol):
    """An item that knows how to render itself as a config line."""

    def to_line(self) -> str: ...


T = TypeVar("T", bound=LineSerialisable)

ChangeKind = Literal["added", "modified", "removed"]


def drop_target_idx(src: int, hover: int, before: bool) -> int:
    """Translate a drag-and-drop hover into a ``SavedList.move`` target.

    Drop-between-rows UIs hover the upper or lower half of a row; the
    pop+insert that ``SavedList.move`` performs shifts indices when the
    source originally sat before the hover row, so the target index has
    to compensate. Self-position drops round-trip to no-ops and should
    be filtered by the caller.
    """
    if before:
        return hover - 1 if src < hover else hover
    return hover if src < hover else hover + 1


def detect_reorder(saved: list[T], current: list[T]) -> bool:
    """True if entries common to both lists appear in different relative order.

    Pure-reorder detection ignores adds and removes — only the relative
    sequence of items present in *both* lists matters. Returns False if
    fewer than two items are common (a single item can't be reordered).
    """
    saved_lines = [e.to_line() for e in saved]
    current_lines = [e.to_line() for e in current]
    common = set(saved_lines) & set(current_lines)
    if len(common) < 2:
        return False
    saved_positions = [line for line in saved_lines if line in common]
    current_positions = [line for line in current_lines if line in common]
    return saved_positions != current_positions


def iter_item_changes(
    saved: list[T],
    current: list[T],
    current_baselines: list[T | None],
) -> Iterator[tuple[ChangeKind, int, T, T | None]]:
    """Yield ``(kind, idx, item, baseline)`` for every add/modify/remove.

    - ``"added"``: ``idx`` is the position in *current*, ``baseline`` is None.
    - ``"modified"``: ``idx`` is the position in *current*, ``baseline`` is
      the saved value it diverged from.
    - ``"removed"``: ``idx`` is ``-1``, ``item`` is the saved value that
      disappeared, ``baseline`` is None.

    Reorder is *not* yielded — call :func:`detect_reorder` for that and
    surface it as a single list-level change. The sidebar badge counter
    and the pending-list collector share this generator so they stay in
    lockstep.
    """
    if len(current) != len(current_baselines):
        raise ValueError(
            "current and current_baselines must be the same length "
            f"(got {len(current)} vs. {len(current_baselines)})"
        )

    # Track baselines of items still in *current* so we can distinguish an
    # edit (baseline survives, line differs) from a delete (baseline is
    # gone). Tracking ``item.to_line()`` would falsely count an edit as
    # both modified AND removed, because the saved entry's old line is no
    # longer in current.
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
    saved: list[T],
    current: list[T],
    current_baselines: list[T | None],
) -> int:
    """Total pending-change entries: per-item changes plus a reorder roll-up."""
    count = sum(1 for _ in iter_item_changes(saved, current, current_baselines))
    if detect_reorder(saved, current):
        count += 1
    return count
