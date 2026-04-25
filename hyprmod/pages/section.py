"""Abstract base for special pages that own their dirty/save/discard lifecycle.

The four "section pages" (animations, binds, cursor, monitors) all manage
their own state independently of ``AppState``: they load their slice of the
config on init, expose ``is_dirty``/``mark_saved``/``discard`` to the window,
and most of them push undo entries when state changes.

This base class consolidates the constructor boilerplate (``window``,
``on_dirty_changed``, ``push_undo``), provides ``_notify_dirty`` and the
``_undo_track`` context manager, and leaves only the page-specific snapshot
plumbing for subclasses to fill in.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

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
