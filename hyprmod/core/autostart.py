"""Parsing and serialization helpers for ``exec``/``exec-once`` autostart entries.

Hyprland exposes a handful of ``exec*`` keywords that all share the same
``keyword = command`` shape but differ in *when* the command runs:

============  =========================================================
``exec``      runs every time the config is reloaded
``exec-once`` runs once at Hyprland startup
============  =========================================================

(Other variants like ``execr``, ``exec-shutdown`` exist but are out of
scope for the initial autostart UI; the data model accepts arbitrary
keyword strings so they can be added later without a migration.)

These keywords can appear multiple times in a config and must be tracked
as an *ordered list* — order matters: ``exec-once`` entries are executed
sequentially, and users may rely on, e.g., ``swaybg`` finishing before
``waybar`` starts.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from hyprmod.core import config

# Set of keyword names this module handles. The order here also defines
# the order entries are written back to disk (once-then-recurring), which
# matches how most users mentally group startup vs. reload behaviour.
EXEC_KEYWORDS: tuple[str, ...] = (config.KEYWORD_EXEC_ONCE, config.KEYWORD_EXEC)

# Human-friendly labels for each keyword, used in the UI and pending
# changes summary. Kept here (not in pages/autostart.py) so pending.py
# can render entries even if the page hasn't been built yet.
KEYWORD_LABELS: dict[str, str] = {
    config.KEYWORD_EXEC_ONCE: "Once at startup",
    config.KEYWORD_EXEC: "On every reload",
}


@dataclass(slots=True)
class ExecData:
    """A single autostart entry: a keyword and the command to run.

    ``command`` is preserved verbatim from the source line — Hyprland
    passes it to ``/bin/sh -c``, so quoting and shell metacharacters
    are the user's responsibility (and we don't try to interpret them).
    """

    keyword: str
    command: str

    def to_line(self) -> str:
        """Serialize as a single ``keyword = command`` config line."""
        return f"{self.keyword} = {self.command}"


def parse_exec_line(line: str) -> ExecData | None:
    """Parse a single ``exec``/``exec-once`` line into an ``ExecData``.

    Returns ``None`` if the line doesn't match a known exec keyword or
    is missing the ``=`` separator.  Whitespace around both the keyword
    and the command is stripped; the command itself is preserved
    otherwise (including any embedded quotes).
    """
    head, sep, tail = line.partition("=")
    if not sep:
        return None
    keyword = head.strip()
    if keyword not in EXEC_KEYWORDS:
        return None
    command = tail.strip()
    if not command:
        return None
    return ExecData(keyword=keyword, command=command)


def parse_exec_lines(lines: list[str]) -> list[ExecData]:
    """Parse multiple raw exec lines, dropping anything unparsable.

    Order is preserved.  Lines that don't match a known exec keyword
    are silently skipped — the caller has already filtered ``sections``
    by keyword, so a mismatch here is a sign of corruption rather than
    user error and shouldn't block loading the rest of the page.
    """
    result = []
    for raw in lines:
        parsed = parse_exec_line(raw)
        if parsed is not None:
            result.append(parsed)
    return result


def serialize(items: list[ExecData]) -> list[str]:
    """Serialize a list of ``ExecData`` back to config lines.

    Items are emitted in the order they appear in *items* — the page
    is responsible for any reordering (e.g. grouping exec-once before
    exec) before calling this.
    """
    return [item.to_line() for item in items]


def drop_target_idx(src: int, hover: int, before: bool) -> int:
    """Translate a drag-and-drop hover into a ``SavedList.move`` target.

    Drag-and-drop UIs let users drop an item *between* rows by hovering
    over the upper or lower half of a target row. Translating that into
    a post-pop insertion index (which is what :meth:`SavedList.move`
    expects) needs to account for the asymmetry caused by the pop:
    when the source originally sat *before* the hover row, popping
    shifts the hover row up by one slot, so the insert index must
    compensate.

    Worked examples on a 4-element list ``[A, B, C, D]``:

    - drop A above C (``src=0, hover=2, before=True``) → ``1`` →
      ``move(0, 1)`` → ``[B, A, C, D]``
    - drop A below C (``src=0, hover=2, before=False``) → ``2`` →
      ``move(0, 2)`` → ``[B, C, A, D]``
    - drop D above B (``src=3, hover=1, before=True``) → ``1`` →
      ``move(3, 1)`` → ``[A, D, B, C]``
    - drop D below B (``src=3, hover=1, before=False``) → ``2`` →
      ``move(3, 2)`` → ``[A, B, D, C]``

    Self-position drops (``src == hover``, or computed target equal
    to source) round-trip to no-ops and should be filtered by the
    caller before invoking ``move``.
    """
    if before:
        return hover - 1 if src < hover else hover
    return hover if src < hover else hover + 1


def detect_reorder(saved: list[ExecData], current: list[ExecData]) -> bool:
    """True if entries common to both lists appear in different relative order.

    Pure-reorder detection ignores adds and removes — it only looks at
    items present in *both* lists and asks whether their relative
    sequence differs. That's what the user perceives as "I reordered
    things," independent of whether they also added or removed
    something in the same session.

    Returns False if there are fewer than two common items (a single
    common item can't be "reordered" relative to anything).
    """
    saved_lines = [e.to_line() for e in saved]
    current_lines = [e.to_line() for e in current]
    common = set(saved_lines) & set(current_lines)
    if len(common) < 2:
        return False
    saved_positions = [line for line in saved_lines if line in common]
    current_positions = [line for line in current_lines if line in common]
    return saved_positions != current_positions


ChangeKind = Literal["added", "modified", "removed"]


def iter_item_changes(
    saved: list[ExecData],
    current: list[ExecData],
    current_baselines: list[ExecData | None],
) -> Iterator[tuple[ChangeKind, int, ExecData, ExecData | None]]:
    """Yield per-item add/modify/remove changes.

    Each yielded tuple is ``(kind, idx, item, baseline)`` where:

    - ``kind="added"``: ``idx`` is the position in *current*,
      ``item`` is the new entry, ``baseline`` is ``None``.
    - ``kind="modified"``: ``idx`` is the position in *current*,
      ``item`` is the current value, ``baseline`` is the saved
      value it diverged from.
    - ``kind="removed"``: ``idx`` is ``-1`` (the saved item is no
      longer in *current*), ``item`` is the saved value (the entry
      that disappeared), ``baseline`` is ``None``.

    Both the sidebar badge counter and the pending-list collector
    iterate this same generator so their results stay in lockstep.
    The reorder roll-up is *not* yielded — call :func:`detect_reorder`
    separately and surface it as a single list-level change.

    Order: yields all current-list entries (added/modified) in order,
    then removed entries in saved order.
    """
    if len(current) != len(current_baselines):
        raise ValueError(
            "current and current_baselines must be the same length "
            f"(got {len(current)} vs. {len(current_baselines)})"
        )

    # Track baselines of items still in *current* — that's how we
    # distinguish an edit (baseline survives, line differs) from a
    # delete (baseline is gone). Tracking ``item.to_line()`` instead
    # would falsely count an edit as both modified AND removed,
    # because the saved entry's old line is no longer in current.
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
    saved: list[ExecData],
    current: list[ExecData],
    current_baselines: list[ExecData | None],
) -> int:
    """Total pending-change entries: per-item changes + reorder roll-up.

    The sidebar badge calls this; the pending-list collector iterates
    :func:`iter_item_changes` directly to also build UI rows. Both
    derive from the same per-item iterator so the sidebar count and
    the pending-list length always agree.
    """
    count = sum(1 for _ in iter_item_changes(saved, current, current_baselines))
    if detect_reorder(saved, current):
        count += 1
    return count
