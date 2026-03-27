"""Shared ownership-tracking helpers for section pages.

Section pages (animations, keybinds) manage items whose "owned by HyprMod"
state is independent of the item's value.  These helpers centralise the
saved-vs-live ownership lifecycle so every page behaves consistently:

- *Remove override* clears ownership (pending until save).
- *Discard* restores ownership to the saved state.
- *Save* snapshots current ownership as the new saved state.
- *Dirty* = current ownership differs from saved.
"""

import copy
from collections.abc import Callable


class OwnershipSet:
    """Track a set of owned names with a saved baseline.

    Used by AnimationsPage where each animation is identified by name and
    "owned" means HyprMod has a config line for it.
    """

    def __init__(self, owned: set[str] | None = None):
        self._owned: set[str] = set(owned) if owned else set()
        self._saved: set[str] = set(self._owned)

    def is_owned(self, name: str) -> bool:
        return name in self._owned

    def is_saved(self, name: str) -> bool:
        return name in self._saved

    def is_item_dirty(self, name: str) -> bool:
        """True if ownership of *name* differs from saved."""
        return (name in self._owned) != (name in self._saved)

    def is_dirty(self) -> bool:
        """True if any ownership changed since last save."""
        return self._owned != self._saved

    def own(self, name: str):
        self._owned.add(name)

    def disown(self, name: str):
        self._owned.discard(name)

    def discard(self, name: str):
        """Restore a single name to its saved ownership state."""
        if name in self._saved:
            self._owned.add(name)
        else:
            self._owned.discard(name)

    def discard_all(self):
        """Restore all ownership to the saved state."""
        self._owned = set(self._saved)

    def mark_saved(self):
        """Snapshot current ownership as the new saved state."""
        self._saved = set(self._owned)

    @property
    def owned(self) -> set[str]:
        return self._owned


class SavedList[T]:
    """A list of items with per-item saved baselines.

    Used by BindsPage where each keybind is tracked by position and
    "saved" means the bind existed in the config at last save.

    Uses composition (not inheritance) so that only the explicit mutation
    helpers are available — raw ``list`` methods like ``insert()`` or
    ``remove()`` can't accidentally bypass baseline tracking.
    """

    def __init__(
        self,
        items: list[T],
        *,
        key: Callable[[T], object] = id,
        copy_item: Callable[[T], T] = copy.deepcopy,
    ):
        self._items: list[T] = list(items)
        self._key = key
        self._copy_item = copy_item
        self._saved: list[T] = [copy_item(x) for x in items]
        self._baselines: list[T | None] = list(self._saved)

    # -- Sequence access --

    def __len__(self) -> int:
        return len(self._items)

    def __getitem__(self, idx: int) -> T:
        return self._items[idx]

    def __setitem__(self, idx: int, value: T) -> None:
        self._items[idx] = value

    def __iter__(self):
        return iter(self._items)

    # -- Baseline access --

    def get_baseline(self, idx: int) -> T | None:
        if 0 <= idx < len(self._baselines):
            return self._baselines[idx]
        return None

    def is_item_dirty(self, idx: int) -> bool:
        """True if item at *idx* differs from its saved baseline."""
        if idx < 0 or idx >= len(self._baselines):
            return False
        baseline = self._baselines[idx]
        if baseline is None:
            return True  # new item, not yet saved
        return self._key(self._items[idx]) != self._key(baseline)

    def is_dirty(self) -> bool:
        """True if the list differs from the saved snapshot."""
        if len(self._items) != len(self._saved):
            return True
        return any(
            self._key(a) != self._key(b) for a, b in zip(self._items, self._saved, strict=True)
        )

    # -- Mutation helpers (keep baselines aligned) --

    def append_new(self, item: T):
        """Append a new item with no saved baseline."""
        self._items.append(item)
        self._baselines.append(None)

    def pop_at(self, idx: int) -> T:
        """Remove item and its baseline at *idx*."""
        if 0 <= idx < len(self._baselines):
            self._baselines.pop(idx)
        return self._items.pop(idx)

    def discard_at(self, idx: int) -> T | None:
        """Restore item at *idx* to its saved baseline.  Returns the baseline."""
        baseline = self.get_baseline(idx)
        if baseline is not None:
            self._items[idx] = self._copy_item(baseline)
        return baseline

    # -- Lifecycle --

    def mark_saved(self):
        """Snapshot current list as the new saved state."""
        self._saved = [self._copy_item(x) for x in self._items]
        self._baselines = list(self._saved)

    def discard_all(self) -> tuple[list[T], list[T]]:
        """Restore to saved state.  Returns (old_items, saved_items)."""
        old = list(self._items)
        saved = [self._copy_item(x) for x in self._saved]
        self._items.clear()
        self._items.extend(saved)
        self._baselines = list(self._saved)
        return old, saved

    @property
    def saved(self) -> list[T]:
        return self._saved

    @property
    def saved_set(self) -> set:
        """Set of saved item keys, for quick membership checks."""
        return {self._key(b) for b in self._saved}
