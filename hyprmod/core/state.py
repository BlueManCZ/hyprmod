"""Application state model tracking live, saved, and default values."""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from hyprland_config import value_to_conf
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
        saved_value: Any,
        *,
        digits: int | None = None,
    ):
        """Register an option with its default and saved values."""
        if digits is not None:
            self._precisions[key] = digits

        live_value, available = self._hypr.get_live(key, default_value)
        if not available:
            live_value = saved_value if saved_value is not None else default_value
        # Normalize gradient/color hex tokens so stored values always have the
        # 0x prefix that Hyprland requires when values are sent back via IPC.
        if isinstance(live_value, str):
            live_value = value_to_conf(live_value)
        live_value = self.normalize(key, live_value)
        managed = saved_value is not None

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

    def set_live(self, key: str, value: Any) -> OptionChange | None:
        """Update live value and apply via IPC.

        Returns an OptionChange on success (for undo history), or None on failure.
        """
        state = self.options.get(key)
        if state is None:
            return None

        value = self.normalize(key, value)
        old_value = state.live_value
        old_managed = state.managed

        if self._hypr.apply(key, value, validate=False):
            state.live_value = value
            state.managed = state.saved_managed or value != state.saved_value
            self.notify(key)
            return OptionChange(
                key=key,
                old_value=old_value,
                new_value=value,
                old_managed=old_managed,
                new_managed=state.managed,
            )
        return None

    def apply_option_value(self, key: str, value: Any, managed: bool) -> bool:
        """Set an option to a specific value (used by undo/redo). Returns True on success."""
        state = self.options.get(key)
        if state is None:
            return False
        value = self.normalize(key, value)
        ipc_value = value_to_conf(value) if isinstance(value, str) else value
        if self._hypr.apply(key, ipc_value, validate=False):
            state.live_value = ipc_value
            state.managed = managed
            self.notify(key)
            return True
        return False

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

        Sends *fallback* to the compositor, updates the live value,
        and clears the managed flag. Returns True on success.
        """
        state = self.options.get(key)
        if state is None:
            return False
        if fallback is not None:
            try:
                if isinstance(fallback, str):
                    fallback = value_to_conf(fallback)
                self._hypr.apply(key, fallback, validate=False)
                state.live_value = self.normalize(key, fallback)
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
        if state.saved_value is not None:
            try:
                self._hypr.keyword(key, value_to_conf(state.saved_value))
            except HyprlandError:
                log.warning("Failed to revert %s via IPC", key)
        self.notify(key)
        return True

    def reload_preserving_dirty(self, *, exclude: str | None = None):
        """Reload Hyprland config, then re-apply any unsaved live values.

        A reload resets Hyprland to what's on disk, losing in-memory
        dirty values. This method re-applies them after the reload.
        *exclude* optionally skips one key (the one being reset).
        """
        self._hypr.reload_compositor()
        for key, value in self.get_dirty_values().items():
            if key != exclude:
                try:
                    self._hypr.keyword(key, value)
                except HyprlandError:
                    log.warning("Failed to re-apply %s after reload", key)

    def refresh_live(self, key: str, reset_baseline: bool = False) -> Any | None:
        """Re-read the live value from Hyprland and update the state.

        If reset_baseline is True, also updates initial_value so the option
        is no longer considered modified.

        Returns the new live value, or None if the read failed.
        """
        state = self.options.get(key)
        if state is None:
            return None
        value, available = self._hypr.get_live(key, state.default_value)
        if not available:
            return None
        value = self.normalize(key, value)
        state.live_value = value
        state.saved_value = value
        if reset_baseline:
            state.initial_value = value
        self.notify(key)
        return value

    def refresh_all_live(self):
        """Re-read all registered options from Hyprland and reset baselines.

        Used after profile activation to sync state with the new live values.
        Fires change notifications for each option that changed, so
        subscribed widgets update reactively.
        """
        changed_keys: list[str] = []
        for key, state in self.options.items():
            value, available = self._hypr.get_live(key, state.default_value)
            if not available:
                continue
            if isinstance(value, str):
                value = value_to_conf(value)
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

        Delegates the IPC revert to ``HyprlandState.discard()``, then
        resets managed state to match what's on the disk. For options not
        found in any config file, applies the saved value directly.

        Returns dict of key -> saved_value for options that were reverted.
        """
        reverted = self._hypr.discard()
        for key, s in self.options.items():
            if key in reverted:
                # If the library couldn't revert (option not in any config file),
                # send the saved value via IPC directly.
                if reverted[key] is None and s.saved_value is not None:
                    try:
                        self._hypr.keyword(key, value_to_conf(s.saved_value))
                    except HyprlandError:
                        log.warning("Failed to discard %s via IPC", key)
                s.live_value = s.saved_value
            # Restore managed flags for all dirty options (including those
            # where only the managed flag changed without a value change)
            if s.managed != s.saved_managed:
                s.managed = s.saved_managed
        return reverted

    def on_change(self, callback):
        self._change_callbacks.append(callback)

    def notify(self, key: str):
        """Fire change callbacks for a key. Public so pages can trigger updates."""
        for cb in self._change_callbacks:
            cb(key)
