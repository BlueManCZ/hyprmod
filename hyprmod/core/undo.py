"""Undo/redo stack manager for HyprMod.

Each entry knows how to replay itself on the window (``apply(window, *,
undo)``) so the window's redo/undo loop is a one-liner — no central
dispatch table, no per-entry isinstance branches.
"""

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from hyprland_socket import HyprlandError

if TYPE_CHECKING:
    from hyprmod.window import HyprModWindow


class UndoEntry(ABC):
    """Base class for all undo entries.

    Subclasses implement :meth:`apply` to replay themselves on the window
    in the requested direction. Returning ``False`` (e.g. the target page
    doesn't exist or an IPC call failed) skips the confirm step so the
    entry stays put for a retry.
    """

    @abstractmethod
    def apply(self, window: "HyprModWindow", *, undo: bool) -> bool:
        """Replay this entry on *window*.

        Returns ``True`` when the apply succeeded and the entry should
        move to the opposite stack; ``False`` when it should stay put
        (page missing, IPC error already toasted, etc.).
        """


@dataclass(slots=True)
class OptionChange(UndoEntry):
    """Undo entry for a single option change."""

    key: str
    old_value: Any
    new_value: Any
    old_managed: bool = True
    new_managed: bool = True

    def apply(self, window: "HyprModWindow", *, undo: bool) -> bool:
        value = self.old_value if undo else self.new_value
        managed = self.old_managed if undo else self.new_managed
        try:
            success = window.app_state.apply_option_value(self.key, value, managed)
        except HyprlandError as e:
            window.show_bug_toast(f"Failed to set {self.key} — {e}", detail=str(e), timeout=5)
            return False
        if success:
            opt_row = window.option_rows.get(self.key)
            if opt_row:
                opt_row.set_value_silent(value)
        return success


@dataclass(slots=True)
class PairedOptionChange(UndoEntry):
    """Undo entry for option keys that change together as one user action.

    A keyboard-layout edit writes both ``kb_layout`` and ``kb_variant`` from
    a single change; wrapping their per-key :class:`OptionChange` entries keeps
    undo/redo atomic instead of splitting one action across two steps (where
    a half-applied state reads as "redo does nothing").
    """

    changes: list[OptionChange]

    def apply(self, window: "HyprModWindow", *, undo: bool) -> bool:
        applied = False
        for change in self.changes:
            applied = change.apply(window, undo=undo) or applied
        return applied


@dataclass(slots=True)
class AnimationUndoEntry(UndoEntry):
    """Undo entry for an animation state change."""

    anim_name: str
    anim_old: Any
    anim_new: Any

    def apply(self, window: "HyprModWindow", *, undo: bool) -> bool:
        page = window._animations_page
        if page is None:
            return False
        page.restore_state(self.anim_name, self.anim_old if undo else self.anim_new)
        return True


@dataclass(slots=True)
class CursorUndoEntry(UndoEntry):
    """Undo entry for a cursor theme/size change."""

    old_theme: str
    old_size: int
    new_theme: str
    new_size: int

    def apply(self, window: "HyprModWindow", *, undo: bool) -> bool:
        page = window._cursor_page
        if page is None:
            return False
        page.restore_snapshot(
            self.old_theme if undo else self.new_theme,
            self.old_size if undo else self.new_size,
        )
        return True


@dataclass(slots=True)
class MonitorsUndoEntry(UndoEntry):
    """Undo entry for a monitors snapshot."""

    old_monitors: list
    new_monitors: list
    old_owned: set
    new_owned: set

    def apply(self, window: "HyprModWindow", *, undo: bool) -> bool:
        page = window._monitors_page
        if page is None:
            return False
        page.restore_snapshot(
            self.old_monitors if undo else self.new_monitors,
            self.old_owned if undo else self.new_owned,
        )
        return True


@dataclass(slots=True)
class SavedListSnapshot(UndoEntry):
    """Undo entry for any ``SavedList[T]``-backed page.

    Used by autostart, env-vars, window-rules, and layer-rules. Each
    entry carries the page identifier (``page_attr`` is the attribute
    name on ``HyprModWindow``) plus the full owned-list and per-item
    baselines on either side of the change, so add/edit/remove/reorder
    all undo through one entry type per page.

    Binds use a separate :class:`BindsUndoEntry` because they also need
    to snapshot the session-overrides dict.
    """

    page_attr: str
    old_items: list
    new_items: list
    old_baselines: list
    new_baselines: list

    def apply(self, window: "HyprModWindow", *, undo: bool) -> bool:
        page = getattr(window, self.page_attr, None)
        if page is None:
            return False
        page.restore_snapshot(
            self.old_items if undo else self.new_items,
            self.old_baselines if undo else self.new_baselines,
        )
        return True


@dataclass(slots=True)
class BindsUndoEntry(UndoEntry):
    """Undo entry for a keybinds snapshot.

    Distinct from :class:`SavedListSnapshot` because binds also track a
    session-overrides dict alongside the owned list.
    """

    old_items: list
    new_items: list
    old_baselines: list
    new_baselines: list
    old_session_overrides: dict
    new_session_overrides: dict

    def apply(self, window: "HyprModWindow", *, undo: bool) -> bool:
        page = window._binds_page
        if page is None:
            return False
        page.restore_snapshot(
            self.old_items if undo else self.new_items,
            self.old_baselines if undo else self.new_baselines,
            self.old_session_overrides if undo else self.new_session_overrides,
        )
        return True


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
