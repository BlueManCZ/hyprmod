"""Bulk signal block/unblock helper."""

from gi.repository import Gtk


class SignalBlocker:
    """Manages a set of GObject signal connections for bulk block/unblock.

    Usage:
        self._signals = SignalBlocker()
        sid = widget.connect("notify::active", handler)
        self._signals.add(widget, sid)

        with self._signals:      # block all registered signals
            ...                  # update widgets without triggering handlers
                                 # automatically unblocked on exit
    """

    __slots__ = ("_handlers", "_blocked")

    def __init__(self):
        self._handlers: list[tuple[Gtk.Widget, int]] = []
        self._blocked = False

    def add(self, widget: Gtk.Widget, handler_id: int) -> int:
        """Register a widget/handler pair. Returns the handler_id for convenience."""
        self._handlers.append((widget, handler_id))
        if self._blocked:
            widget.handler_block(handler_id)
        return handler_id

    def connect(self, widget: Gtk.Widget, signal: str, callback, *args) -> int:
        """Connect a signal and register it in one step."""
        if args:
            handler_id = widget.connect(signal, callback, *args)
        else:
            handler_id = widget.connect(signal, callback)
        self._handlers.append((widget, handler_id))
        if self._blocked:
            widget.handler_block(handler_id)
        return handler_id

    def block(self):
        if self._blocked:
            return
        self._blocked = True
        for widget, sid in self._handlers:
            widget.handler_block(sid)

    def unblock(self):
        if not self._blocked:
            return
        self._blocked = False
        for widget, sid in self._handlers:
            widget.handler_unblock(sid)

    def __enter__(self):
        self.block()
        return self

    def __exit__(self, *_exc):
        self.unblock()
        return False
