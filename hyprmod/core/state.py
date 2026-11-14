"""Application state model tracking live, saved, and default values."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from hyprland_config import keyword_to_lua, value_to_conf
from hyprland_socket import HyprlandError
from hyprland_state import HyprlandState

from hyprmod.core.undo import OptionChange

log = logging.getLogger(__name__)


@dataclass(slots=True)
class OptionState:
    """State of a single option being tracked by AppState."""

    key: str
    live_value: Any = None
    saved_value: Any = None
    default_value: Any = None
    initial_value: Any = None
    managed: bool = False
    saved_managed: bool = False
    available: bool = True

    @property
    def is_dirty(self) -> bool:
        """True if live value differs from saved value."""
        return self.live_value != self.saved_value or self.managed != self.saved_managed


class AppState:
    """Holds the state of all options.

    Float values are normalized to widget display precision on ingress,
    so state and widget always agree — no rounding mismatches.
    """

    def __init__(self, hypr: HyprlandState):
        self._hypr = hypr
        self.options: dict[str, OptionState] = {}
        self._precisions: dict[str, int] = {}
        self._change_callbacks: list[Callable[[str], Any]] = []
        # Options holding a value IPC could not send, which the compositor
        # only picks up from a reload (see needs_reload_to_set).
        self.awaiting_reload: set[str] = set()

    def normalize(self, key: str, value: Any) -> Any:
        """Round floats to widget display precision if registered."""
        digits = self._precisions.get(key)
        if digits is not None and isinstance(value, float):
            return round(value, digits)
        return value

    def register(
        self,
        key: str,
        default_value: Any,
        was_managed_value: Any,
        *,
        digits: int | None = None,
    ):
        """Register an option, seeding the saved baseline from the live value.

        *was_managed_value* is the on-disk value (or ``None`` if absent).
        Its truthy-ness decides whether hyprmod currently *manages* this
        key — but the actual baseline becomes the live IPC value, so
        ``is_dirty`` tracks "user changed something this session" rather
        than "live differs from disk." Without that, every startup where
        a user previously ran ``hyprctl keyword …`` by hand would show
        the unsaved-changes banner immediately.
        """
        if digits is not None:
            self._precisions[key] = digits

        live_value, available = self._hypr.get_live(key, default_value)
        if not available:
            live_value = was_managed_value if was_managed_value is not None else default_value
        live_value = self.normalize(key, live_value)
        managed = was_managed_value is not None

        self.options[key] = OptionState(
            key=key,
            live_value=live_value,
            saved_value=live_value,
            default_value=default_value,
            initial_value=live_value,
            managed=managed,
            saved_managed=managed,
            available=available,
        )

    def get(self, key: str) -> OptionState | None:
        return self.options.get(key)

    def needs_reload_to_set(self, key: str, value: Any) -> bool:
        """Whether the compositor takes *value* for *key* only from its config.

        Hyprland's Lua API refuses an empty value for some options, so their
        default comes back only when the config is reloaded with the key
        left out.
        """
        if not isinstance(value, str) or value.strip() or not self._hypr.is_live_lua_mode():
            return False
        # TODO: ask hyprland-config's lua_accepts_empty() once it is exported,
        # instead of probing the emitter for its ValueError.
        try:
            keyword_to_lua(key, value)
        except ValueError:
            return True
        return False

    def set_live(self, key: str, value: Any) -> OptionChange | None:
        """Update live value and apply via IPC.

        A value only a reload can set is held in ``awaiting_reload`` instead.
        Returns an OptionChange on success (for undo history), or None on failure.
        """
        state = self.options.get(key)
        if state is None:
            return None

        value = self.normalize(key, value)
        old_value = state.live_value
        old_managed = state.managed

        if self.needs_reload_to_set(key, value):
            # Nothing to manage either: the saved config leaves the key out.
            self.awaiting_reload.add(key)
            state.managed = False
        elif self._hypr.apply(key, value, validate=False):
            self.awaiting_reload.discard(key)
            state.managed = state.saved_managed or value != state.saved_value
        else:
            return None
        state.live_value = value
        self.notify(key)
        return OptionChange(
            key=key,
            old_value=old_value,
            new_value=value,
            old_managed=old_managed,
            new_managed=state.managed,
        )

    def apply_option_value(self, key: str, value: Any, managed: bool) -> bool:
        """Set an option to a specific value (used by undo/redo). Returns True on success."""
        state = self.options.get(key)
        if state is None:
            return False
        value = self.normalize(key, value)
        # String values come in raw and need to be canonicalised before
        # they round-trip back through IPC; non-strings are already in
        # their target Python type and pass through unchanged.
        coerced = value_to_conf(value) if isinstance(value, str) else value
        if self.needs_reload_to_set(key, coerced):
            self.awaiting_reload.add(key)
        elif self._hypr.apply(key, coerced, validate=False):
            self.awaiting_reload.discard(key)
        else:
            return False
        state.live_value = coerced
        state.managed = managed
        self.notify(key)
        return True

    def unmanage(self, key: str):
        """Remove a key from HyprMod management and mark as permanently unmanaged.

        Updates both live and saved managed states, so the option is no longer
        considered modified after the next save cycle.
        """
        state = self.options.get(key)
        if state:
            state.managed = False
            state.saved_managed = False
            self.notify(key)

    def reset_to_value(self, key: str, fallback: Any) -> bool:
        """Remove an option from management and apply a fallback value.

        Sends *fallback* to the compositor, or holds it in
        ``awaiting_reload`` when only a reload can set it, updates the live
        value, and clears the managed flag. Returns True on success.
        """
        state = self.options.get(key)
        if state is None:
            return False
        if isinstance(fallback, str):
            fallback = value_to_conf(fallback)
        if self.needs_reload_to_set(key, fallback):
            self.awaiting_reload.add(key)
            # With no saved override the reload restores the saved value,
            # which is not always the empty one: an unset gradient reads
            # back as white.
            state.live_value = fallback if state.saved_managed else state.saved_value
        elif fallback is not None:
            try:
                self._hypr.apply(key, fallback, validate=False)
                state.live_value = self.normalize(key, fallback)
                self.awaiting_reload.discard(key)
            except HyprlandError:
                log.warning("Failed to apply fallback for %s via IPC", key)
        state.managed = False
        self.notify(key)
        return True

    def discard_one(self, key: str) -> bool:
        """Discard changes on a single option — revert to saved state.

        Returns True if the option was dirty and was reverted.
        """
        state = self.options.get(key)
        if state is None or not state.is_dirty:
            return False
        state.live_value = state.saved_value
        state.managed = state.saved_managed
        if self.needs_reload_to_set(key, state.saved_value):
            self.awaiting_reload.add(key)
        else:
            self.awaiting_reload.discard(key)
            if state.saved_value is not None:
                try:
                    self._hypr.keyword(key, value_to_conf(state.saved_value))
                except HyprlandError:
                    log.warning("Failed to revert %s via IPC", key)
        self.notify(key)
        return True

    def reload_preserving_dirty(self):
        """Reload Hyprland config, then re-apply any unsaved live values.

        A reload resets Hyprland to what's on disk, losing in-memory
        dirty values. This method re-applies them after the reload.
        """
        self._hypr.reload_compositor()
        for key, value in self.get_dirty_values().items():
            if key in self.awaiting_reload:
                continue
            try:
                self._hypr.keyword(key, value)
            except HyprlandError:
                log.warning("Failed to re-apply %s after reload", key)

    def reload_compositor(self):
        """Reload Hyprland config and re-read the options that waited on it.

        A reload drops every live preview, so this is for when nothing is
        unsaved and the config on disk is the state to show.
        """
        self._hypr.reload_compositor()
        # Emptied first: the notifications below can lead back here.
        waiting, self.awaiting_reload = self.awaiting_reload, set()
        for key in waiting:
            state = self.options[key]
            value, available = self._hypr.get_live(key, state.default_value)
            if available:
                state.live_value = state.saved_value = self.normalize(key, value)
            self.notify(key)

    def refresh_all_live(self):
        """Re-read all registered options from Hyprland and reset baselines.

        Used after profile activation to sync state with the new live values.
        Fires change notifications for each option that changed, so
        subscribed widgets update reactively.
        """
        self.awaiting_reload.clear()
        changed_keys: list[str] = []
        for key, state in self.options.items():
            value, available = self._hypr.get_live(key, state.default_value)
            if not available:
                continue
            value = self.normalize(key, value)
            if value != state.live_value:
                changed_keys.append(key)
            state.live_value = value
            state.saved_value = value
            state.initial_value = value
        # Fire notifications after all values are updated
        for key in changed_keys:
            self.notify(key)

    def has_dirty(self) -> bool:
        """True if any option has unsaved changes."""
        return any(s.is_dirty for s in self.options.values())

    def get_dirty_values(self) -> dict[str, Any]:
        """Return all options where live != saved."""
        return {key: s.live_value for key, s in self.options.items() if s.is_dirty}

    def get_all_live_values(self) -> dict[str, str]:
        """Return all live values as strings for config writing."""
        return {key: value_to_conf(s.live_value) for key, s in self.options.items() if s.managed}

    def mark_saved(self):
        """After a save, update all saved values to match live."""
        for s in self.options.values():
            s.saved_value = s.live_value
            s.saved_managed = s.managed

    def discard_dirty(self) -> dict[str, Any]:
        """Revert all dirty options to their saved values via IPC.

        ``HyprlandState.discard()`` handles the IPC revert (using on-disk
        values, falling back to schema defaults); this method then mirrors
        the result into the per-option ``OptionState`` so the UI reflects
        the post-revert state.

        Returns the dict ``HyprlandState.discard()`` produced — key →
        reverted compositor value (or ``None`` when neither the document
        nor the schema had a value to revert to).
        """
        reverted = self._hypr.discard()
        # A value held for a reload never reached the library's pending list.
        for key in self.awaiting_reload - reverted.keys():
            reverted[key] = self.options[key].saved_value
        self.awaiting_reload.clear()
        for key, s in self.options.items():
            if key in reverted:
                s.live_value = s.saved_value
                if self.needs_reload_to_set(key, s.saved_value):
                    self.awaiting_reload.add(key)
            # Restore managed flags for all dirty options (including those
            # where only the managed flag changed without a value change).
            if s.managed != s.saved_managed:
                s.managed = s.saved_managed
        return reverted

    def on_change(self, callback):
        self._change_callbacks.append(callback)

    def notify(self, key: str):
        """Fire change callbacks for a key. Public so pages can trigger updates."""
        for cb in self._change_callbacks:
            cb(key)
