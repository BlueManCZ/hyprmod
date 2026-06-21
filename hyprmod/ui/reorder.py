"""Page-agnostic whole-row reorder controller.

Wires a ``Gtk.DragSource`` + ``Gtk.DropTarget`` + Alt+↑/↓ key handler onto
each row of an ordered list, so a row can be moved by dragging it between
its neighbours or with the keyboard. The drag carries the source-row index
as ``GObject.TYPE_INT``; the drop computes a between-rows insertion point
from the cursor's vertical position within the hover row, painting the
insertion line with the ``drop-above`` / ``drop-below`` / ``dragging-row``
CSS classes (defined in ``style.css``).

The host supplies the two things that vary per list: how a move is actually
applied (``move``) and how to enumerate the live rows for indicator clearing
(``iter_rows``). An optional ``can_move`` predicate rejects illegal moves
(e.g. autostart's same-keyword constraint) before they show an indicator or
commit. Two hosts share it: the ``SavedList``-backed section pages and the
keyboard-layouts dialog.

A plain click on a row still routes to ``activated`` (e.g. an edit dialog):
``Gtk.DragSource`` only claims the input sequence once motion crosses its
drag threshold.
"""

from collections.abc import Callable, Iterable

from gi.repository import Adw, Gdk, GObject, Gtk

from hyprmod.core.change_tracking import drop_target_idx


class RowReorderController:
    """Attaches whole-row drag-and-drop + keyboard reorder to list rows.

    Parameters
    ----------
    move:
        ``move(src, target) -> bool`` performs the reorder and returns
        whether it happened. ``target`` is the final index in the list, not
        the hovered row. Keyboard handling propagates the return value as the
        "event consumed" flag.
    iter_rows:
        Returns the live row widgets, used to clear stale insertion-line
        classes after a drop rebuilds the list.
    can_move:
        Optional ``can_move(src, dst) -> bool`` gate. ``dst`` is the hovered
        row (drag) or the neighbour (keyboard); both are adjacent to the
        eventual target, so a contiguous-group constraint reads the same
        either way. Defaults to allowing every move.
    """

    def __init__(
        self,
        *,
        move: Callable[[int, int], bool],
        iter_rows: Callable[[], Iterable[Gtk.Widget]],
        can_move: Callable[[int, int], bool] | None = None,
    ):
        self._move = move
        self._iter_rows = iter_rows
        self._can_move = can_move or (lambda _src, _dst: True)
        # Index of the row currently being dragged (``None`` when no drag is
        # in progress). ``motion`` reads it to validate drops synchronously,
        # since the dragged value only resolves at drop time.
        self._dragging_idx: int | None = None
        # ``(x, y)`` of the press that began the drag, in source-row-local
        # coords. Stashed by ``prepare`` for ``drag-begin`` to use as the
        # icon hot spot. ``None`` when no drag is active.
        self._drag_press: tuple[float, float] | None = None

    # ── Attachment ──

    def attach(self, row: Adw.ActionRow, idx: int) -> None:
        """Attach drag, drop, and keyboard reorder to *row* at position *idx*."""
        self.attach_keyboard(row, idx)
        self._attach_drag_source(row, idx)
        self._attach_drop_target(row, idx)

    def attach_keyboard(self, row: Adw.ActionRow, idx: int) -> None:
        """Attach Alt+↑/↓ reorder only, for rows that don't support dragging."""
        controller = Gtk.EventControllerKey.new()
        controller.connect("key-pressed", self._on_key_pressed, idx)
        row.add_controller(controller)

    def _attach_drag_source(self, row: Adw.ActionRow, idx: int) -> None:
        source = Gtk.DragSource.new()
        source.set_actions(Gdk.DragAction.MOVE)
        source.connect("prepare", self._on_drag_prepare, idx)
        source.connect("drag-begin", self._on_drag_begin, idx)
        source.connect("drag-end", self._on_drag_end)
        row.add_controller(source)

    def _attach_drop_target(self, row: Adw.ActionRow, idx: int) -> None:
        target = Gtk.DropTarget.new(int, Gdk.DragAction.MOVE)
        target.connect("motion", self._on_drop_motion, idx)
        target.connect("leave", self._on_drop_leave)
        target.connect("drop", self._on_drop, idx)
        row.add_controller(target)

    # ── Drag source ──

    def _on_drag_prepare(
        self, _source: Gtk.DragSource, x: float, y: float, idx: int
    ) -> Gdk.ContentProvider:
        # Stash the press coords so ``drag-begin`` can use them as the icon's
        # hot spot. Setting the icon in ``prepare`` doesn't always stick: some
        # compositors apply it only once the drag is fully initialised,
        # between ``prepare`` and ``drag-begin``.
        self._drag_press = (x, y)
        return Gdk.ContentProvider.new_for_value(GObject.Value(GObject.TYPE_INT, idx))

    def _on_drag_begin(self, source: Gtk.DragSource, drag: Gdk.Drag, idx: int) -> None:
        self._dragging_idx = idx
        press = self._drag_press or (0.0, 0.0)
        hot_x, hot_y = int(press[0]), int(press[1])
        # ``Adw.ActionRow`` has no intrinsic background: the visible "card"
        # comes from the parent group's ``boxed-list`` styling. Painted in
        # isolation the row would be transparent, so a short-lived CSS class
        # gives it a solid background for the drag. ``Gtk.WidgetPaintable`` is
        # a live view, so it picks up the class on the next paint.
        widget = source.get_widget()
        if widget is not None:
            widget.add_css_class("dragging-row")
            source.set_icon(Gtk.WidgetPaintable.new(widget), hot_x, hot_y)
        # ``set_icon`` calls ``set_hotspot`` internally, but at least one
        # Wayland compositor (Hyprland) ignores the hot spot at that point;
        # repeating it against the live ``Gdk.Drag`` is harmless if redundant
        # and effective when the earlier call was lost.
        drag.set_hotspot(hot_x, hot_y)

    def _on_drag_end(self, source: Gtk.DragSource, _drag: Gdk.Drag, _delete: bool) -> None:
        self._dragging_idx = None
        self._drag_press = None
        widget = source.get_widget()
        if widget is not None:
            widget.remove_css_class("dragging-row")
        # If the drop rebuilt the list before ``leave`` fired, dangling
        # indicator classes would carry over to rows reusing the same widgets.
        self._clear_indicators()

    # ── Drop target ──

    def _on_drop_motion(
        self, target: Gtk.DropTarget, _x: float, y: float, hover_idx: int
    ) -> Gdk.DragAction:
        # ``motion`` has no access to the dragged value, so validate against
        # ``_dragging_idx`` set in ``drag-begin``.
        src = self._dragging_idx
        if src is None or src == hover_idx or not self._can_move(src, hover_idx):
            return Gdk.DragAction(0)
        widget = target.get_widget()
        if widget is None:
            return Gdk.DragAction(0)
        # Only one class at a time per row, so flicking across the midpoint
        # cleanly swaps the insertion line.
        if _is_above_half(widget, y):
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
        self, target: Gtk.DropTarget, value: object, _x: float, y: float, hover_idx: int
    ) -> bool:
        # PyGObject normally unwraps ``GObject.TYPE_INT`` to a plain ``int``,
        # but the signal contract is ``object``; fall back to ``int()`` for
        # the rare wrapper case.
        src = value if isinstance(value, int) else int(value)  # type: ignore[arg-type]
        if not self._can_move(src, hover_idx):
            return False
        widget = target.get_widget()
        if widget is None:
            return False
        before = _is_above_half(widget, y)
        return self._move(src, drop_target_idx(src, hover_idx, before))

    # ── Keyboard ──

    def _on_key_pressed(
        self,
        _controller: Gtk.EventControllerKey,
        keyval: int,
        _keycode: int,
        state: Gdk.ModifierType,
        idx: int,
    ) -> bool:
        # Require Alt only: Shift/Ctrl/Super combos are reserved for future
        # shortcuts (e.g. Alt+Shift+Up = move-to-top).
        relevant = (
            Gdk.ModifierType.ALT_MASK
            | Gdk.ModifierType.CONTROL_MASK
            | Gdk.ModifierType.SHIFT_MASK
            | Gdk.ModifierType.SUPER_MASK
        )
        if state & relevant != Gdk.ModifierType.ALT_MASK:
            return False
        if keyval == Gdk.KEY_Up:
            target = idx - 1
        elif keyval == Gdk.KEY_Down:
            target = idx + 1
        else:
            return False
        if not self._can_move(idx, target):
            return False
        return self._move(idx, target)

    def _clear_indicators(self) -> None:
        for row in self._iter_rows():
            row.remove_css_class("drop-above")
            row.remove_css_class("drop-below")


def _is_above_half(widget: Gtk.Widget, y: float) -> bool:
    """True if *y* falls in the upper half of *widget*.

    Chooses between insert-above and insert-below for a drop on this widget.
    Falls back to "above" for zero-height widgets (shouldn't happen, but
    cheap to handle).
    """
    height = widget.get_height() or widget.get_allocated_height()
    if height <= 0:
        return True
    return y < height / 2
