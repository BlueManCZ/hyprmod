"""Abstract base for special pages that own their dirty/save/discard lifecycle.

The "section pages" (animations, binds, cursor, monitors, autostart,
env-vars, window-rules, layer-rules) all manage their own state
independently of ``AppState``: they load their slice of the config on
init, expose ``is_dirty``/``mark_saved``/``discard`` to the window, and
most of them push undo entries when state changes.

This base class consolidates the constructor boilerplate (``window``,
``on_dirty_changed``, ``push_undo``), provides ``_notify_dirty`` and the
``_undo_track`` context manager, and leaves only the page-specific snapshot
plumbing for subclasses to fill in.

:class:`SavedListSectionPage` is a more specialised base for the pages
backed by a :class:`SavedList[T]` of line-serialisable items (autostart,
env-vars, window-rules, layer-rules). It absorbs the byte-identical
keyboard reorder, deleted-restore, pending-change roll-up, and the
``is_dirty``/``mark_saved``/``discard``/``reload_from_saved`` lifecycle
methods so each page keeps only the row/group rendering and item-level
actions.

:class:`DragDropReorderMixin` adds whole-row drag-and-drop on top of
``SavedListSectionPage``, used by autostart and env-vars. Window-rules
and layer-rules use keyboard-only reorder for now.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from gi.repository import Adw, Gdk, GLib, GObject, Gtk

from hyprmod.core.change_tracking import (
    LineSerialisable,
    count_pending_changes,
    detect_reorder,
    drop_target_idx,
)
from hyprmod.core.ownership import SavedList

if TYPE_CHECKING:
    from hyprmod.core.undo import UndoEntry
    from hyprmod.window import HyprModWindow


class SectionPage(ABC):
    """Base class for pages independent of ``AppState``."""

    def __init__(
        self,
        window: "HyprModWindow",
        on_dirty_changed: Callable[[], None] | None = None,
        push_undo: Callable[["UndoEntry"], None] | None = None,
    ):
        self._window = window
        self._on_dirty_changed = on_dirty_changed
        self._push_undo = push_undo

    # ── lifecycle (subclasses MUST implement) ──

    @abstractmethod
    def is_dirty(self) -> bool: ...

    @abstractmethod
    def mark_saved(self) -> None: ...

    @abstractmethod
    def discard(self) -> None: ...

    # ── shared scaffolding ──

    def _notify_dirty(self) -> None:
        """Notify the parent window that this page's dirty state may have changed."""
        if self._on_dirty_changed is not None:
            self._on_dirty_changed()

    @contextmanager
    def _undo_track(self):
        """Capture before/after snapshots and push an undo entry on change.

        Subclasses opt in by overriding ``_capture_undo``, ``_undo_key``, and
        ``_build_undo_entry``. Calling this without those overrides raises
        ``NotImplementedError``.

        The "new" snapshot is captured only after the key check passes, so
        subclasses with expensive snapshots (deep copies, etc.) don't pay
        when nothing changed.
        """
        old = self._capture_undo()
        old_key = self._undo_key()
        yield
        if self._push_undo is None:
            return
        if old_key is not None and self._undo_key() == old_key:
            return
        new = self._capture_undo()
        self._push_undo(self._build_undo_entry(old, new))

    # ── undo hooks (override to enable _undo_track) ──

    def _capture_undo(self) -> Any:
        """Snapshot state for undo. Override to enable ``_undo_track``."""
        raise NotImplementedError("override _capture_undo() to use _undo_track()")

    def _undo_key(self) -> object | None:
        """Cheap comparable key for change detection.

        Returning ``None`` disables the fast-path comparison; the
        ``_build_undo_entry`` override is then responsible for any change
        detection it needs (otherwise an entry will be pushed on every yield).
        """
        return None

    def _build_undo_entry(self, old: Any, new: Any) -> "UndoEntry":
        """Build the undo entry from old + new snapshots.

        Override to enable ``_undo_track``. The base class handles the push.
        """
        raise NotImplementedError("override _build_undo_entry() to use _undo_track()")


class SavedListSectionPage[T: LineSerialisable](SectionPage):
    """SectionPage backed by a :class:`SavedList[T]` of line-serialisable items.

    Specialisation of :class:`SectionPage` for the autostart, env-vars,
    window-rules, and layer-rules pages — each maintains a single
    ``_owned: SavedList[T]`` plus a parallel ``_rows_by_idx`` widget map
    and rebuilds the list with ``_rebuild_list``. The operations that all
    four pages duplicated verbatim live here:

    - **Alt+↑/↓ keyboard reorder** (``_attach_keyboard_reorder``,
      ``_on_row_key_pressed``, ``_move_relative``) — subclasses with
      cross-group constraints override :meth:`_is_valid_move`.
    - **Deleted-baseline detection** (``_deleted_baselines``) for the
      "Removed (pending save)" group.
    - **Reorder roll-up** (``is_reordered``, ``revert_reorder``) for
      pending-changes display.
    - **Pending-change count** (``pending_change_count``) for the
      sidebar badge.
    - **SectionPage lifecycle** (``is_dirty``, ``mark_saved``,
      ``discard``, ``reload_from_saved``) — pages that need extra
      runtime sync (e.g. window-rules) override and call ``super``.

    Subclasses must initialise ``self._owned`` and ``self._rows_by_idx``
    in ``__init__``, implement ``_rebuild_list`` and ``_load``.
    """

    # Subclasses set these in __init__ before any base method runs.
    _owned: SavedList[T]
    _rows_by_idx: list[Adw.ActionRow | None]

    # ── List rendering & loading (subclass implements) ──

    def _rebuild_list(self, focus_idx: int = -1) -> None:
        """Repaint the list. ``focus_idx`` re-focuses the row at that index."""
        raise NotImplementedError

    def _load(self, saved_sections: dict[str, list[str]] | None) -> None:
        """Re-read ``self._owned`` from *saved_sections* (or the live config)."""
        raise NotImplementedError

    # ── SectionPage lifecycle (default implementations) ──

    def is_dirty(self) -> bool:
        return self._owned.is_dirty()

    def mark_saved(self) -> None:
        self._owned.mark_saved()
        self._rebuild_list()

    def discard(self) -> None:
        self._owned.discard_all()
        self._rebuild_list()

    def reload_from_saved(self, saved_sections: dict[str, list[str]]) -> None:
        """Re-load baseline from the given saved sections (after profile switch)."""
        self._load(saved_sections)
        self._rebuild_list()

    # ── Keyboard reorder ──

    def _attach_keyboard_reorder(self, row: Adw.ActionRow, idx: int) -> None:
        """Bind Alt+Up / Alt+Down on *row* to move it within the list."""
        controller = Gtk.EventControllerKey.new()
        controller.connect("key-pressed", self._on_row_key_pressed, idx)
        row.add_controller(controller)

    def _on_row_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        state: Gdk.ModifierType,
        idx: int,
    ) -> bool:
        # Require Alt only — Shift/Ctrl/Super combos are reserved for
        # future shortcuts (e.g. Alt+Shift+Up = move-to-top).
        wanted = Gdk.ModifierType.ALT_MASK
        relevant = (
            Gdk.ModifierType.ALT_MASK
            | Gdk.ModifierType.CONTROL_MASK
            | Gdk.ModifierType.SHIFT_MASK
            | Gdk.ModifierType.SUPER_MASK
        )
        if state & relevant != wanted:
            return False

        if keyval == Gdk.KEY_Up:
            delta = -1
        elif keyval == Gdk.KEY_Down:
            delta = 1
        else:
            return False
        return self._move_relative(idx, delta)

    def _move_relative(self, idx: int, delta: int) -> bool:
        """Move the entry at *idx* by *delta* slots (typically ±1).

        Returns ``True`` when the move was performed, ``False`` when the
        move would have been illegal (out of range, no-op, or refused by
        :meth:`_is_valid_move`). Keyboard handlers propagate this as the
        "event consumed" flag so unhandled arrows fall through to default
        focus traversal.
        """
        target = idx + delta
        if not self._is_valid_move(idx, target):
            return False
        with self._undo_track():
            self._owned.move(idx, target)
        self._notify_dirty()
        self._rebuild_list(focus_idx=target)
        return True

    def _is_valid_move(self, src_idx: int, dst_idx: int) -> bool:
        """True if moving *src_idx* to *dst_idx* is a legal reorder.

        Default: any distinct, in-range pair. Override to add cross-group
        constraints — e.g. autostart's same-keyword check that prevents
        flipping ``exec`` ↔ ``exec-once`` by reordering.
        """
        n = len(self._owned)
        if src_idx < 0 or dst_idx < 0:
            return False
        if src_idx == dst_idx:
            return False
        return src_idx < n and dst_idx < n

    @staticmethod
    def _grab_focus_once(widget: Gtk.Widget) -> bool:
        """One-shot ``GLib.idle_add`` callback for post-rebuild focus restore.

        ``Widget.grab_focus`` returns ``True`` on success — an idle handler
        reading that as "fire me again" produces an infinite focus-grab loop
        that freezes Tab navigation, so this wrapper hard-returns
        ``GLib.SOURCE_REMOVE``.
        """
        widget.grab_focus()
        return GLib.SOURCE_REMOVE

    # ── Deleted-baseline detection ──

    def _deleted_baselines(self) -> list[T]:
        """Return saved entries that are no longer in the owned list."""
        current = {e.to_line() for e in self._owned}
        return [b for b in self._owned.saved if b.to_line() not in current]

    # ── Reorder / pending-change roll-ups ──

    def is_reordered(self) -> bool:
        """True if the *common* items between saved and current differ in order."""
        return detect_reorder(self._owned.saved, list(self._owned))

    def pending_change_count(self) -> int:
        """Number of distinct pending-change entries the page would surface.

        Drives the sidebar badge; mirrors the iterator the pending-changes
        page uses, so the badge count and pending-list length stay in
        lockstep by construction.
        """
        if not self.is_dirty():
            return 0
        baselines = [self._owned.get_baseline(i) for i in range(len(self._owned))]
        return count_pending_changes(self._owned.saved, list(self._owned), baselines)

    def revert_reorder(self) -> None:
        """Restore the saved order while preserving other dirty changes.

        - Items present in both saved and current are repositioned to
          their saved-order slots; any in-flight value edits to those
          items are kept.
        - Newly-added items (no baseline) keep their values and slot in
          at the end.
        - Items the user removed stay removed; this revert isn't a
          general "undo all".

        Pushes a single undo entry so Ctrl+Z restores the pre-revert
        order in one step.
        """
        # Map saved-line -> (current_item, baseline) for items that
        # originated from the saved snapshot. ``baseline.to_line()`` is
        # the stable identity even if the user has edited the value.
        by_saved_line: dict[str, tuple[T, T | None]] = {}
        new_pairs: list[tuple[T, T | None]] = []

        for idx in range(len(self._owned)):
            item = self._owned[idx]
            baseline = self._owned.get_baseline(idx)
            if baseline is None:
                new_pairs.append((item, baseline))
            else:
                by_saved_line[baseline.to_line()] = (item, baseline)

        rebuilt_items: list[T] = []
        rebuilt_baselines: list[T | None] = []
        for saved in self._owned.saved:
            pair = by_saved_line.get(saved.to_line())
            if pair is None:
                # User removed this entry; not coming back from a reorder revert.
                continue
            item, baseline = pair
            rebuilt_items.append(item)
            rebuilt_baselines.append(baseline)
        # Newly-added rows keep their existing positions at the end —
        # they have no saved-order to revert to.
        for item, baseline in new_pairs:
            rebuilt_items.append(item)
            rebuilt_baselines.append(baseline)

        with self._undo_track():
            self._owned.restore(rebuilt_items, rebuilt_baselines)
        self._notify_dirty()
        self._rebuild_list()


class DragDropReorderMixin[T: LineSerialisable](SavedListSectionPage[T]):
    """Whole-row drag-and-drop reorder for :class:`SavedListSectionPage`.

    Each row becomes both a ``Gtk.DragSource`` and a ``Gtk.DropTarget``.
    The drag carries the source-row index as ``GObject.TYPE_INT``;
    drop computes a between-rows insertion point from the cursor's
    vertical position within the hover row, with CSS classes painting
    the indicator line.

    Used by autostart and env-vars. Window-rules and layer-rules use
    keyboard-only reorder for now.

    A plain click on the row still routes to ``activated`` (the edit
    dialog) — ``Gtk.DragSource`` only claims the input sequence once
    motion crosses its threshold.
    """

    # Index of the row currently being dragged (``None`` when no drag is in
    # progress). Read by ``motion`` to validate drops synchronously without
    # waiting for the drag value to resolve.
    _dragging_idx: int | None
    # ``(x, y)`` of the press that started the current drag, in source-row-local
    # coords. Stashed by ``drag-prepare`` for ``drag-begin`` to use as the
    # icon's hot spot. ``None`` when no drag is active.
    _drag_press: tuple[float, float] | None

    def _init_drag_state(self) -> None:
        """Initialise drag-state attributes — call from subclass ``__init__``."""
        self._dragging_idx = None
        self._drag_press = None

    def _attach_drag_source(self, row: Adw.ActionRow, idx: int) -> None:
        """Make *row* the source of a same-list reorder drag."""
        source = Gtk.DragSource.new()
        source.set_actions(Gdk.DragAction.MOVE)
        source.connect("prepare", self._on_drag_prepare, idx)
        source.connect("drag-begin", self._on_drag_begin, idx)
        source.connect("drag-end", self._on_drag_end)
        row.add_controller(source)

    def _attach_drop_target(self, row: Adw.ActionRow, idx: int) -> None:
        """Make *row* a drop target for same-list reorder."""
        target = Gtk.DropTarget.new(int, Gdk.DragAction.MOVE)
        target.connect("motion", self._on_drop_motion, idx)
        target.connect("leave", self._on_drop_leave)
        target.connect("drop", self._on_drop, idx)
        row.add_controller(target)

    def _on_drag_prepare(
        self,
        _source: Gtk.DragSource,
        x: float,
        y: float,
        idx: int,
    ) -> Gdk.ContentProvider | None:
        # Stash the press coords so ``drag-begin`` can use them as the
        # icon's hot spot. Setting the icon in ``prepare`` doesn't always
        # stick — some compositors apply it only once the drag is fully
        # initialised, between ``prepare`` and ``drag-begin``.
        self._drag_press = (x, y)
        val = GObject.Value(GObject.TYPE_INT, idx)
        return Gdk.ContentProvider.new_for_value(val)

    def _on_drag_begin(self, source: Gtk.DragSource, drag: Gdk.Drag, idx: int) -> None:
        self._dragging_idx = idx
        press = self._drag_press or (0.0, 0.0)
        hot_x, hot_y = int(press[0]), int(press[1])
        # ``Adw.ActionRow`` has no intrinsic background — the visible "card"
        # appearance comes from the parent ``PreferencesGroup``'s
        # ``boxed-list`` styling. Painted in isolation the row would be
        # transparent, so we add a short-lived CSS class that gives it a
        # solid background + corner radius for the duration of the drag.
        # ``Gtk.WidgetPaintable`` is a *live* view, so it picks up the new
        # CSS class on the next paint.
        widget = source.get_widget()
        if widget is not None:
            widget.add_css_class("dragging-row")
            paintable = Gtk.WidgetPaintable.new(widget)
            source.set_icon(paintable, hot_x, hot_y)
        # Belt-and-suspenders: also set the hot spot directly on the
        # ``Gdk.Drag``. ``GtkDragSource.set_icon`` calls this internally
        # but at least one Wayland compositor (Hyprland) appears to ignore
        # the hot spot at that point — repeating the call against the
        # live ``Gdk.Drag`` after it's been initialised is harmless if
        # redundant and effective when the earlier call was lost.
        drag.set_hotspot(hot_x, hot_y)

    def _on_drag_end(
        self,
        source: Gtk.DragSource,
        _drag: Gdk.Drag,
        _delete: bool,
    ) -> None:
        self._dragging_idx = None
        self._drag_press = None
        widget = source.get_widget()
        if widget is not None:
            widget.remove_css_class("dragging-row")
        # If the drop completed and rebuilt the list before ``leave``
        # fired, dangling indicator classes would carry over to other
        # rows that happen to land at the same widget pointer.
        self._clear_drop_indicators()

    def _on_drop_motion(
        self,
        target: Gtk.DropTarget,
        _x: float,
        y: float,
        hover_idx: int,
    ) -> Gdk.DragAction:
        # ``motion`` doesn't have access to the dragged value — that's
        # only resolved at drop time — so we read ``_dragging_idx`` set
        # in ``drag-begin`` to validate the move synchronously.
        src = self._dragging_idx
        if src is None or src == hover_idx:
            return Gdk.DragAction(0)
        if not self._is_valid_move(src, hover_idx):
            return Gdk.DragAction(0)

        widget = target.get_widget()
        if widget is None:
            return Gdk.DragAction(0)

        before = self._is_above_half(widget, y)
        # Top-edge or bottom-edge insertion line via inset box-shadow.
        # Only one class at a time per row, so flicking across the
        # midpoint cleanly swaps the indicator.
        if before:
            widget.add_css_class("drop-above")
            widget.remove_css_class("drop-below")
        else:
            widget.add_css_class("drop-below")
            widget.remove_css_class("drop-above")
        return Gdk.DragAction.MOVE

    def _on_drop_leave(self, target: Gtk.DropTarget) -> None:
        widget = target.get_widget()
        if widget is not None:
            widget.remove_css_class("drop-above")
            widget.remove_css_class("drop-below")

    def _on_drop(
        self,
        target: Gtk.DropTarget,
        value: object,
        _x: float,
        y: float,
        hover_idx: int,
    ) -> bool:
        # PyGObject normally unwraps ``GObject.TYPE_INT`` to a plain ``int``,
        # but the signal contract is ``object`` so the type checker can't
        # see that. Fall back to ``int(value)`` for the rare wrapper case;
        # the ``type: ignore`` covers the int() call against an arbitrary
        # object.
        src_idx = value if isinstance(value, int) else int(value)  # type: ignore[arg-type]
        if not self._is_valid_move(src_idx, hover_idx):
            return False
        widget = target.get_widget()
        if widget is None:
            return False
        before = self._is_above_half(widget, y)

        target_idx = drop_target_idx(src_idx, hover_idx, before)

        # ``move()`` itself rejects out-of-range targets, but compute
        # cleanly here so a same-position no-op doesn't push an empty
        # undo entry.
        if target_idx == src_idx:
            return False
        n = len(self._owned)
        if not 0 <= target_idx < n:
            return False
        if not 0 <= src_idx < n:
            return False

        with self._undo_track():
            self._owned.move(src_idx, target_idx)
        self._notify_dirty()
        self._rebuild_list()
        return True

    @staticmethod
    def _is_above_half(widget: Gtk.Widget, y: float) -> bool:
        """True if *y* falls in the upper half of *widget*.

        Used to choose between insert-above and insert-below for a drop
        on this widget. Falls back to "above" for zero-height widgets
        (shouldn't happen, but cheap to handle).
        """
        height = widget.get_height() or widget.get_allocated_height()
        if height <= 0:
            return True
        return y < height / 2

    def _clear_drop_indicators(self) -> None:
        """Remove insertion-line classes from every tracked row.

        Belt-and-suspenders: ``leave`` should clear them per row, but if
        the drop completed and ``_rebuild_list`` ran before the leave
        signal fired, the freshly-rebuilt rows shouldn't inherit any
        stale state. Iterating the rows we already track avoids a
        recursive widget-tree walk.
        """
        for row in self._rows_by_idx:
            if row is not None:
                row.remove_css_class("drop-above")
                row.remove_css_class("drop-below")
