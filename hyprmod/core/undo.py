"""Undo/redo stack manager for HyprMod."""

from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class OptionChange:
    """Undo entry for a single option change."""

    key: str
    old_value: Any
    new_value: Any
    old_managed: bool = True
    new_managed: bool = True


@dataclass(slots=True)
class AnimationUndoEntry:
    """Undo entry for an animation state change."""

    anim_name: str
    anim_old: Any
    anim_new: Any


@dataclass(slots=True)
class BindsUndoEntry:
    """Undo entry for a keybinds snapshot."""

    old_items: list
    new_items: list
    old_baselines: list
    new_baselines: list
    old_session_overrides: dict
    new_session_overrides: dict


@dataclass(slots=True)
class MonitorsUndoEntry:
    """Undo entry for a monitors snapshot."""

    old_monitors: list
    new_monitors: list
    old_owned: set
    new_owned: set


@dataclass(slots=True)
class CursorUndoEntry:
    """Undo entry for a cursor theme/size change."""

    old_theme: str
    old_size: int
    new_theme: str
    new_size: int


type UndoEntry = (
    OptionChange | AnimationUndoEntry | BindsUndoEntry | MonitorsUndoEntry | CursorUndoEntry
)


class UndoManager:
    """Simple linear undo/redo stack."""

    def __init__(self, max_size: int = 100):
        self._undo_stack: deque[UndoEntry] = deque(maxlen=max_size)
        self._redo_stack: deque[UndoEntry] = deque(maxlen=max_size)

    def push(self, entry: UndoEntry, *, merge: bool = True) -> None:
        """Push an entry onto the undo stack, clearing the redo stack.

        Consecutive OptionChange entries for the same key are merged into
        one entry (keeps the original old_value with the latest new_value).
        Set *merge=False* to force a separate entry (e.g. for discards).
        """
        prev = self._undo_stack[-1] if self._undo_stack else None
        if merge and isinstance(entry, OptionChange) and isinstance(prev, OptionChange):
            if prev.key == entry.key:
                prev.new_value = entry.new_value
                prev.new_managed = entry.new_managed
                if prev.old_value == prev.new_value and prev.old_managed == prev.new_managed:
                    self._undo_stack.pop()
                self._redo_stack.clear()
                return
        if merge and isinstance(entry, MonitorsUndoEntry) and isinstance(prev, MonitorsUndoEntry):
            prev.new_monitors = entry.new_monitors
            prev.new_owned = entry.new_owned
            self._redo_stack.clear()
            return
        if merge and isinstance(entry, CursorUndoEntry) and isinstance(prev, CursorUndoEntry):
            prev.new_theme = entry.new_theme
            prev.new_size = entry.new_size
            if prev.old_theme == prev.new_theme and prev.old_size == prev.new_size:
                self._undo_stack.pop()
            self._redo_stack.clear()
            return
        self._undo_stack.append(entry)
        self._redo_stack.clear()

    def pop_undo(self) -> UndoEntry | None:
        """Pop the most recent undo entry (does NOT move to redo yet)."""
        if not self._undo_stack:
            return None
        return self._undo_stack.pop()

    def pop_redo(self) -> UndoEntry | None:
        """Pop the most recent redo entry (does NOT move to undo yet)."""
        if not self._redo_stack:
            return None
        return self._redo_stack.pop()

    def confirm_undo(self, entry: UndoEntry) -> None:
        """Confirm that an undo was successfully applied; move entry to redo stack."""
        self._redo_stack.append(entry)

    def confirm_redo(self, entry: UndoEntry) -> None:
        """Confirm that a redo was successfully applied; move entry to undo stack."""
        self._undo_stack.append(entry)

    def clear(self) -> None:
        """Clear both stacks."""
        self._undo_stack.clear()
        self._redo_stack.clear()

    def peek(self) -> UndoEntry | None:
        """Return the most recent undo entry without removing it."""
        return self._undo_stack[-1] if self._undo_stack else None

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_stack)
