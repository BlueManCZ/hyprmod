"""Keybind edit dialog — add/edit a keybind with category/action cascade."""

import logging

from gi.repository import Adw, Gdk, Gtk
from hyprland_config import BindData
from hyprland_socket import MOD_BITS, HyprlandError

from hyprmod.binds.dispatchers import (
    BIND_TYPES,
    BINDM_DISPATCHERS,
    CATEGORY_BY_ID,
    DISPATCHER_CATEGORIES,
    DISPATCHER_INFO,
    GDK_BUTTON_TO_MOUSE_KEY,
    KEY_BIND_TYPES,
    MOUSE_BUTTON_PRESETS,
    DispatcherCategory,
    categorize_dispatcher,
    format_action,
)
from hyprmod.binds.gdk_modifiers import (
    MODIFIER_KEYVALS,
    gdk_state_to_mods,
    unshifted_keyval,
)
from hyprmod.ui import clear_children, confirm

log = logging.getLogger(__name__)

# Bind types selectable in "Key combination" trigger mode (everything except
# ``bindm``, which is reached via the dedicated "Mouse button" trigger mode).
KEY_BIND_TYPE_KEYS = list(KEY_BIND_TYPES.keys())
KEY_BIND_TYPE_LABELS = [v["label"] for v in KEY_BIND_TYPES.values()]

# UI-specific filtered views: exclude the catch-all "advanced" group AND the
# bindm-only "mouse_button" group — the latter has its own trigger mode and
# isn't selectable from the keyboard-action category combo.
DIALOG_CATEGORIES = [
    c for c in DISPATCHER_CATEGORIES if c["id"] not in ("advanced", "mouse_button")
]
DIALOG_CATEGORY_LABELS = [c["label"] for c in DIALOG_CATEGORIES]

# bindm dispatcher list ordered by definition in BINDM_DISPATCHERS.
_BINDM_DISPATCHER_KEYS = list(BINDM_DISPATCHERS.keys())
_BINDM_DISPATCHER_LABELS = list(BINDM_DISPATCHERS.values())

# Mouse-button picker model: [placeholder, ...presets, "Custom…"].
# The leading placeholder keeps new bindm binds from auto-selecting the
# first preset on open.
_MOUSE_BUTTON_VALUES = [v for v, _ in MOUSE_BUTTON_PRESETS]
_MOUSE_BUTTON_NONE_INDEX = 0
_MOUSE_BUTTON_PRESET_OFFSET = 1
_MOUSE_BUTTON_CUSTOM_INDEX = _MOUSE_BUTTON_PRESET_OFFSET + len(_MOUSE_BUTTON_VALUES)
_MOUSE_BUTTON_DISPLAY_LABELS = (
    ["Pick a button…"]
    + [f"{label} ({value})" for value, label in MOUSE_BUTTON_PRESETS]
    + ["Custom…"]
)


# Kept for back-compat with any external import; equals the full BIND_TYPES.
BIND_TYPE_KEYS = list(BIND_TYPES.keys())
BIND_TYPE_LABELS = [v["label"] for v in BIND_TYPES.values()]

# ---------------------------------------------------------------------------
# Argument widget builders
# ---------------------------------------------------------------------------

_WORKSPACE_PRESETS = [
    ("1", "Workspace 1"),
    ("2", "Workspace 2"),
    ("3", "Workspace 3"),
    ("4", "Workspace 4"),
    ("5", "Workspace 5"),
    ("6", "Workspace 6"),
    ("7", "Workspace 7"),
    ("8", "Workspace 8"),
    ("9", "Workspace 9"),
    ("10", "Workspace 10"),
    ("+1", "Next workspace"),
    ("-1", "Previous workspace"),
    ("previous", "Last visited"),
    ("empty", "First empty"),
    ("special", "Special (scratchpad)"),
]

_FULLSCREEN_MODES = [("0", "Full"), ("1", "Maximize"), ("2", "No gaps")]

_DIRECTION_CHOICES = [
    ("l", "go-previous-symbolic", "Left"),
    ("d", "go-down-symbolic", "Down"),
    ("u", "go-up-symbolic", "Up"),
    ("r", "go-next-symbolic", "Right"),
]

_GROUP_DIR_CHOICES = [("f", "Forward"), ("b", "Back")]

_DPMS_CHOICES = [("on", "On"), ("off", "Off"), ("toggle", "Toggle")]


def _build_combo_arg(title: str, choices: list[tuple[str, str]], current_value: str, fallback: str):
    """Build a ComboRow from a list of (value, label) pairs. Returns (widget, getter)."""
    labels = [c[1] for c in choices]
    values = [c[0] for c in choices]
    combo = Adw.ComboRow(title=title, model=Gtk.StringList.new(labels))
    for i, v in enumerate(values):
        if v == current_value:
            combo.set_selected(i)
            break
    return (
        combo,
        lambda: (
            values[combo.get_selected()] if 0 <= combo.get_selected() < len(values) else fallback
        ),
    )


def _build_arg_widget(arg_type: str, current_value: str):
    """Build argument widget for a dispatcher. Returns (widget, getter_callable)."""
    if arg_type == "none":
        return None, lambda: ""

    if arg_type == "command":
        row = Adw.EntryRow(title="Command")
        row.set_text(current_value)
        return row, lambda: row.get_text().strip()

    if arg_type == "workspace":
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        preset_labels = [p[1] for p in _WORKSPACE_PRESETS]
        preset_values = [p[0] for p in _WORKSPACE_PRESETS]
        group = Adw.PreferencesGroup()
        combo = Adw.ComboRow(title="Workspace", model=Gtk.StringList.new(preset_labels))
        custom_row = Adw.EntryRow(title="Custom value")
        custom_row.set_text("")
        selected_preset = -1
        for i, val in enumerate(preset_values):
            if val == current_value:
                selected_preset = i
                break
        if selected_preset >= 0:
            combo.set_selected(selected_preset)
        elif current_value:
            combo.set_selected(Gtk.INVALID_LIST_POSITION)
            custom_row.set_text(current_value)
        group.add(combo)
        group.add(custom_row)
        box.append(group)

        def getter():
            custom = custom_row.get_text().strip()
            if custom:
                return custom
            idx = combo.get_selected()
            if 0 <= idx < len(preset_values):
                return preset_values[idx]
            return current_value

        return box, getter

    if arg_type == "fullscreen_mode":
        return _build_combo_arg("Mode", _FULLSCREEN_MODES, current_value, "0")

    if arg_type == "direction":
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_halign(Gtk.Align.CENTER)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        buttons = {}
        for val, icon, tooltip in _DIRECTION_CHOICES:
            btn = Gtk.ToggleButton()
            btn.set_icon_name(icon)
            btn.set_tooltip_text(tooltip)
            btn.add_css_class("circular")
            if val == current_value:
                btn.set_active(True)
            btn.connect("toggled", lambda b, v=val: _on_direction_toggled(b, v, buttons))
            buttons[val] = btn
            box.append(btn)
        return box, lambda: next((v for v, b in buttons.items() if b.get_active()), current_value)

    if arg_type == "group_dir":
        return _build_combo_arg("Direction", _GROUP_DIR_CHOICES, current_value, "f")

    if arg_type == "dpms":
        return _build_combo_arg("Action", _DPMS_CHOICES, current_value, "toggle")

    if arg_type == "optional_text":
        row = Adw.EntryRow(title="Name (optional)")
        row.set_text(current_value)
        return row, lambda: row.get_text().strip()

    # Fallback: generic text entry
    row = Adw.EntryRow(title="Argument")
    row.set_text(current_value)
    return row, lambda: row.get_text().strip()


def _on_direction_toggled(active_btn, active_val, buttons):
    if not active_btn.get_active():
        return
    for val, btn in buttons.items():
        if val != active_val and btn.get_active():
            btn.set_active(False)


# ---------------------------------------------------------------------------
# BindEditDialog
# ---------------------------------------------------------------------------


class BindEditDialog(Adw.Dialog):
    """Dialog for adding/editing a keybind with trigger mode + action cascade."""

    def __init__(
        self,
        bind: BindData | None = None,
        *,
        window,
        initial_category: str = "",
        on_apply=None,
        conflict_finder=None,
    ):
        super().__init__()
        self._is_new = bind is None
        self._initial_category = initial_category
        self._bind = bind or BindData()
        self._window = window
        self._arg_getter = lambda: ""
        self._capturing = False
        self._on_apply_callback = on_apply
        self._conflict_finder = conflict_finder
        self._key_controller = None
        self._mouse_gesture = None
        self._focus_handler = None
        self._current_dispatcher_keys: list[str] = []

        # Trigger mode: ``"mouse"`` for ``bindm`` (mouse drag), else ``"key"``.
        # When opening from the "Mouse Button" category's add button, default
        # to mouse mode for new binds too.
        if not self._is_new and self._bind.bind_type == "bindm":
            self._is_mouse_mode = True
        elif self._is_new and initial_category == "mouse_button":
            self._is_mouse_mode = True
        else:
            self._is_mouse_mode = False

        self.connect("closed", self._on_dialog_closed)

        self.set_title("Add Keybind" if self._is_new else "Edit Keybind")
        self.set_content_width(520)
        self.set_content_height(620)
        self.set_follows_content_size(True)

        toolbar = Adw.ToolbarView()
        toolbar.set_size_request(500, -1)

        header = Adw.HeaderBar()
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda _: self.close())
        header.pack_start(cancel_btn)

        self._apply_btn = Gtk.Button(label="Apply")
        self._apply_btn.add_css_class("suggested-action")
        self._apply_btn.connect("clicked", self._on_apply)
        header.pack_end(self._apply_btn)
        toolbar.add_top_bar(header)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_propagate_natural_height(True)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        content.set_margin_top(18)
        content.set_margin_bottom(18)
        content.set_margin_start(18)
        content.set_margin_end(18)
        self._content = content

        trigger_group = Adw.PreferencesGroup(title="Trigger")
        self._build_trigger_section(trigger_group)
        content.append(trigger_group)

        self._key_group = Adw.PreferencesGroup()
        self._build_key_section(self._key_group)
        content.append(self._key_group)

        self._action_group = Adw.PreferencesGroup(title="Action")
        self._build_action_section(self._action_group)
        content.append(self._action_group)

        self._arg_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self._arg_container)

        self._adv_group = Adw.PreferencesGroup(title="Advanced")
        self._build_type_section(self._adv_group)
        content.append(self._adv_group)

        scrolled.set_child(content)
        toolbar.set_content(scrolled)
        self.set_child(toolbar)

        # Mouse click capture controller. Lives on the content box so
        # it can intercept clicks anywhere in the dialog body during
        # mouse-drag recording.
        self._mouse_gesture = Gtk.GestureClick.new()
        self._mouse_gesture.set_button(0)  # any button
        self._mouse_gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self._mouse_gesture.connect("pressed", self._on_mouse_captured)
        content.add_controller(self._mouse_gesture)

        self._apply_trigger_mode()
        self._refresh_arg_widget()

    # -- Trigger mode --

    def _build_trigger_section(self, group):
        row = Adw.ActionRow(title="Trigger")
        row.set_subtitle("How this binding is activated")

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        box.add_css_class("linked")
        box.set_valign(Gtk.Align.CENTER)

        self._trigger_key_btn = Gtk.ToggleButton(label="Key combination")
        self._trigger_mouse_btn = Gtk.ToggleButton(label="Mouse button")
        self._trigger_mouse_btn.set_group(self._trigger_key_btn)
        if self._is_mouse_mode:
            self._trigger_mouse_btn.set_active(True)
        else:
            self._trigger_key_btn.set_active(True)
        self._trigger_key_btn.connect("toggled", self._on_trigger_toggled)
        self._trigger_mouse_btn.connect("toggled", self._on_trigger_toggled)

        box.append(self._trigger_key_btn)
        box.append(self._trigger_mouse_btn)
        row.add_suffix(box)
        group.add(row)

    def _on_trigger_toggled(self, btn):
        # Both buttons share a group, so each toggle fires twice; only act on
        # the activation, not the deactivation, to avoid flicker.
        if not btn.get_active():
            return
        new_mode = self._trigger_mouse_btn.get_active()
        if new_mode == self._is_mouse_mode:
            return
        if self._capturing:
            self._stop_capture()
        self._is_mouse_mode = new_mode
        # Switching domains invalidates the captured key — modifiers are kept
        # because they apply to both domains.
        self._key_entry.set_text("")
        self._mouse_combo.set_selected(_MOUSE_BUTTON_NONE_INDEX)
        self._mouse_custom_entry.set_text("")
        # Toggling domains needs a fresh action list (and a fresh selection,
        # since e.g. ``killactive`` makes no sense in mouse mode).
        self._apply_trigger_mode(rebuild_actions=True)
        self._refresh_arg_widget()
        self._update_capture_display()

    def _apply_trigger_mode(self, rebuild_actions: bool = False):
        """Show/hide widgets and adjust labels for the active trigger mode.

        When *rebuild_actions* is true the action combo is repopulated for
        the new mode (used when the user toggles the trigger). At dialog
        initialisation the action combo has already been built by
        :meth:`_build_action_section` with the correct selection, so the
        rebuild is skipped to avoid clobbering it.
        """
        is_mouse = self._is_mouse_mode

        self._key_group.set_title("Mouse Button" if is_mouse else "Key Combination")
        self._capture_row.set_title("Click to record" if is_mouse else "Shortcut")

        # Manual-edit picker rows
        self._key_entry.set_visible(not is_mouse)
        self._mouse_combo.set_visible(is_mouse)
        self._mouse_custom_entry.set_visible(
            is_mouse and self._mouse_combo.get_selected() == _MOUSE_BUTTON_CUSTOM_INDEX
        )

        # Action layout
        self._category_combo.set_visible(not is_mouse)
        if rebuild_actions:
            if is_mouse:
                self._update_action_model_mouse()
            else:
                self._update_action_model(self._get_selected_category()["id"])

        # Bind type advanced section is only meaningful for keyboard binds
        self._adv_group.set_visible(not is_mouse)

    # -- Key / mouse section --

    def _build_key_section(self, group):
        current_mods = [m.upper() for m in self._bind.mods]
        current_key = self._bind.key

        self._capture_row = Adw.ActionRow(title="Shortcut")
        shortcut_text = self._bind.format_shortcut()
        self._capture_label = Gtk.Label(label=shortcut_text)
        self._capture_label.add_css_class("dim-label")
        self._capture_row.add_suffix(self._capture_label)

        capture_btn = Gtk.Button(label="Record")
        capture_btn.set_valign(Gtk.Align.CENTER)
        capture_btn.add_css_class("suggested-action")
        capture_btn.add_css_class("keybind-capture-button")
        capture_btn.connect("clicked", self._on_start_capture)
        self._capture_btn = capture_btn
        self._capture_row.add_suffix(capture_btn)
        group.add(self._capture_row)

        manual_expander = Adw.ExpanderRow(title="Manual Edit")
        self._mod_checks = {}
        for mod_name in MOD_BITS:
            row = Adw.SwitchRow(title=mod_name)
            row.set_active(mod_name in current_mods)
            row.connect("notify::active", self._on_manual_mod_changed)
            self._mod_checks[mod_name] = row
            manual_expander.add_row(row)

        # Key text entry (visible when trigger mode is "key").
        self._key_entry = Adw.EntryRow(title="Key")
        if not self._is_mouse_mode:
            self._key_entry.set_text(current_key)
        self._key_entry.connect("changed", self._on_manual_key_changed)
        manual_expander.add_row(self._key_entry)

        # Mouse-button preset combo (visible when trigger mode is "mouse").
        self._mouse_combo = Adw.ComboRow(
            title="Mouse button",
            model=Gtk.StringList.new(_MOUSE_BUTTON_DISPLAY_LABELS),
        )
        if self._is_mouse_mode and current_key in _MOUSE_BUTTON_VALUES:
            self._mouse_combo.set_selected(
                _MOUSE_BUTTON_PRESET_OFFSET + _MOUSE_BUTTON_VALUES.index(current_key)
            )
        elif self._is_mouse_mode and current_key:
            # Existing custom button — drop into "Custom…" with the value preserved.
            self._mouse_combo.set_selected(_MOUSE_BUTTON_CUSTOM_INDEX)
        else:
            # New bind in mouse mode (or non-mouse mode): no button preselected.
            self._mouse_combo.set_selected(_MOUSE_BUTTON_NONE_INDEX)
        self._mouse_combo.connect("notify::selected", self._on_mouse_button_changed)
        manual_expander.add_row(self._mouse_combo)

        self._mouse_custom_entry = Adw.EntryRow(title="Custom button (e.g. mouse:277)")
        if self._is_mouse_mode and current_key and current_key not in _MOUSE_BUTTON_VALUES:
            self._mouse_custom_entry.set_text(current_key)
        self._mouse_custom_entry.connect("changed", self._on_mouse_custom_changed)
        manual_expander.add_row(self._mouse_custom_entry)

        group.add(manual_expander)

    def _on_start_capture(self, _btn):
        if self._capturing:
            self._stop_capture()
            return
        # Set ``_capturing`` first so the dialog-close safety net
        # (:meth:`_on_dialog_closed`) will always try to reset the submap
        # if anything in the rest of this method blows up after we've
        # entered the capture submap.
        self._capturing = True
        prompt = (
            "Click any mouse button\u2026"
            if self._is_mouse_mode
            else "Press a key combination\u2026"
        )
        self._capture_label.set_label(prompt)
        self._capture_btn.set_label("Cancel")
        self._capture_btn.add_css_class("destructive-action")
        self._capture_btn.remove_css_class("suggested-action")

        self._register_capture_submap()
        try:
            self._window.hypr.dispatch("submap", "hyprmod_capture")
        except HyprlandError as e:
            log.warning("entering capture submap failed; aborting capture: %s", e)
            self._capturing = False
            self._capture_btn.set_label("Record")
            self._capture_btn.remove_css_class("destructive-action")
            self._capture_btn.add_css_class("suggested-action")
            return

        toplevel = self._window.get_root()
        if toplevel:
            self._focus_handler = toplevel.connect(
                "notify::is-active", self._on_window_focus_changed
            )

        # Key controller is active in both modes: in mouse mode it still
        # handles Escape (cancel) and modifier-only presses (preview), so
        # the user can hold SUPER and then click to record SUPER + click.
        self._key_controller = Gtk.EventControllerKey.new()
        self._key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self._key_controller.connect("key-pressed", self._on_key_captured)
        self._capture_btn.add_controller(self._key_controller)
        self._capture_btn.grab_focus()

    def _stop_capture(self):
        self._capture_btn.set_label("Record")
        self._capture_btn.remove_css_class("destructive-action")
        self._capture_btn.add_css_class("suggested-action")
        if self._key_controller is not None:
            self._capture_btn.remove_controller(self._key_controller)
            self._key_controller = None
        self._update_capture_display()
        if self._focus_handler is not None:
            toplevel = self._window.get_root()
            if toplevel:
                toplevel.disconnect(self._focus_handler)
            self._focus_handler = None
        # Only clear ``_capturing`` once the submap reset has actually
        # landed; if it raises we leave the flag set so the dialog-close
        # handler retries the reset rather than leaving Hyprland stuck.
        try:
            self._window.hypr.dispatch("submap", "reset")
        except HyprlandError as e:
            log.warning("submap reset failed; will retry on dialog close: %s", e)
            # Surface visibly: without a toast the user sees the capture
            # button revert but doesn't realise their compositor is still
            # in the capture submap (every keypress would no-op until the
            # dialog closes and the retry fires).
            self._window.show_toast(
                f"Couldn't leave capture mode — {e}. Close this dialog to retry.",
                timeout=5,
            )
            return
        self._capturing = False

    def _on_window_focus_changed(self, window, _pspec):
        if self._capturing and not window.is_active():
            self._stop_capture()

    def _register_capture_submap(self):
        try:
            self._window.hypr.keyword("submap", "hyprmod_capture")
            self._window.hypr.keyword("bind", ", catchall, pass,")
            self._window.hypr.keyword("submap", "reset")
        except HyprlandError as e:
            self._window.show_toast(f"Capture setup failed — {e}", timeout=5)

    def _on_dialog_closed(self, _dialog):
        if self._capturing:
            try:
                self._window.hypr.dispatch("submap", "reset")
            except HyprlandError as e:
                log.error("could not reset Hyprland submap after capture; user may be stuck: %s", e)

    def _on_key_captured(self, controller, keyval, keycode, state):
        key_name = Gdk.keyval_name(keyval)
        if not key_name:
            return True
        if key_name == "Escape":
            self._stop_capture()
            return True
        if key_name in MODIFIER_KEYVALS:
            # Modifier-only press updates the live preview in either mode.
            mods = gdk_state_to_mods(state)
            preview_parts = [m.upper() for m in mods]
            preview_parts.append("click…" if self._is_mouse_mode else "...")
            self._capture_label.set_label(" + ".join(preview_parts))
            return True
        if self._is_mouse_mode:
            # Real keys are not valid input while recording a mouse drag —
            # but we let it through so the user can keep typing modifiers
            # via Shift+letter combos without losing capture.
            return True
        mods = gdk_state_to_mods(state)
        if state & Gdk.ModifierType.SHIFT_MASK:
            widget = controller.get_widget()
            display = widget.get_display() if widget is not None else None
            if display is not None:
                kv = unshifted_keyval(display, keycode, state, controller.get_group(), keyval)
                resolved = Gdk.keyval_name(kv)
                if resolved and resolved not in MODIFIER_KEYVALS:
                    key_name = resolved
        display_key = key_name.upper() if len(key_name) == 1 else key_name
        for mod_name, switch in self._mod_checks.items():
            switch.set_active(mod_name in mods)
        self._key_entry.set_text(display_key)
        self._update_capture_display()
        self._stop_capture()
        return True

    def _on_mouse_captured(self, gesture, _n_press, _x, _y):
        if not self._capturing or not self._is_mouse_mode:
            return
        button = gesture.get_current_button()
        mouse_key = GDK_BUTTON_TO_MOUSE_KEY.get(button)
        if mouse_key is None:
            return
        event = gesture.get_current_event()
        state = event.get_modifier_state() if event else Gdk.ModifierType(0)
        mods = gdk_state_to_mods(state)
        for mod_name, switch in self._mod_checks.items():
            switch.set_active(mod_name in mods)
        self._set_mouse_key(mouse_key)
        self._update_capture_display()
        self._stop_capture()
        # Claim the gesture so the click doesn't reach whatever button it
        # happened to land on.
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _set_mouse_key(self, mouse_key: str) -> None:
        """Set the mouse-button picker to *mouse_key*, using a preset or Custom."""
        if mouse_key in _MOUSE_BUTTON_VALUES:
            self._mouse_combo.set_selected(
                _MOUSE_BUTTON_PRESET_OFFSET + _MOUSE_BUTTON_VALUES.index(mouse_key)
            )
            self._mouse_custom_entry.set_text("")
        else:
            self._mouse_combo.set_selected(_MOUSE_BUTTON_CUSTOM_INDEX)
            self._mouse_custom_entry.set_text(mouse_key)

    def _on_manual_mod_changed(self, *_args):
        if not self._capturing:
            self._update_capture_display()

    def _on_manual_key_changed(self, *_args):
        if not self._capturing:
            self._update_capture_display()

    def _on_mouse_button_changed(self, *_args):
        # Show the custom-entry row only when "Custom…" is chosen.
        is_custom = self._mouse_combo.get_selected() == _MOUSE_BUTTON_CUSTOM_INDEX
        self._mouse_custom_entry.set_visible(self._is_mouse_mode and is_custom)
        if not self._capturing:
            self._update_capture_display()

    def _on_mouse_custom_changed(self, *_args):
        if not self._capturing:
            self._update_capture_display()

    def _update_capture_display(self):
        self._capture_label.set_label(self._get_current_key_combo().format_shortcut())

    def _current_key_value(self) -> str:
        """Return the current key value, picking from the mouse combo in mouse mode."""
        if not self._is_mouse_mode:
            return self._key_entry.get_text().strip()
        idx = self._mouse_combo.get_selected()
        if idx == _MOUSE_BUTTON_NONE_INDEX:
            return ""
        if (
            _MOUSE_BUTTON_PRESET_OFFSET
            <= idx
            < _MOUSE_BUTTON_PRESET_OFFSET + len(_MOUSE_BUTTON_VALUES)
        ):
            return _MOUSE_BUTTON_VALUES[idx - _MOUSE_BUTTON_PRESET_OFFSET]
        if idx == _MOUSE_BUTTON_CUSTOM_INDEX:
            return self._mouse_custom_entry.get_text().strip()
        return ""

    def _get_current_key_combo(self) -> BindData:
        mods = [name for name, row in self._mod_checks.items() if row.get_active()]
        key = self._current_key_value()
        return BindData(mods=mods, key=key)

    # -- Action section --

    def _build_action_section(self, group):
        current_dispatcher = self._bind.dispatcher
        current_cat_id = categorize_dispatcher(current_dispatcher)
        if self._is_new and self._initial_category:
            current_cat_id = self._initial_category
        self._category_combo = Adw.ComboRow(
            title="Category", model=Gtk.StringList.new(DIALOG_CATEGORY_LABELS)
        )
        dialog_cat_ids = [c["id"] for c in DIALOG_CATEGORIES]
        if current_cat_id in dialog_cat_ids:
            self._category_combo.set_selected(dialog_cat_ids.index(current_cat_id))
            effective_cat_id = current_cat_id
        else:
            self._category_combo.set_selected(0)
            effective_cat_id = DIALOG_CATEGORIES[0]["id"]
        self._sig_category = self._category_combo.connect(
            "notify::selected", self._on_category_changed
        )
        group.add(self._category_combo)

        self._action_combo = Adw.ComboRow(title="Action")
        self._action_combo.set_expression(
            Gtk.PropertyExpression.new(Gtk.StringObject, None, "string")
        )
        self._sig_action = self._action_combo.connect("notify::selected", self._on_action_changed)
        group.add(self._action_combo)
        if self._is_mouse_mode:
            self._update_action_model_mouse(select_dispatcher=current_dispatcher)
        else:
            self._update_action_model(effective_cat_id, select_dispatcher=current_dispatcher)

    @staticmethod
    def _make_action_factory(nat_chars: int, xalign: float = 0) -> Gtk.SignalListItemFactory:
        factory = Gtk.SignalListItemFactory()

        def on_setup(_factory, list_item):
            label = Gtk.Inscription(xalign=xalign)
            label.set_min_lines(1)
            label.set_nat_chars(nat_chars)
            list_item.set_child(label)

        def on_bind(_factory, list_item):
            label = list_item.get_child()
            label.set_text(list_item.get_item().get_string())

        factory.connect("setup", on_setup)
        factory.connect("bind", on_bind)
        return factory

    def _get_selected_category(self) -> DispatcherCategory:
        idx = self._category_combo.get_selected()
        if 0 <= idx < len(DIALOG_CATEGORIES):
            return DIALOG_CATEGORIES[idx]
        return DIALOG_CATEGORIES[0]

    def _update_action_model(self, category_id: str, select_dispatcher: str = ""):
        self._action_combo.handler_block(self._sig_action)
        cat = CATEGORY_BY_ID.get(category_id, CATEGORY_BY_ID["advanced"])
        dispatcher_items = list(cat["dispatchers"].items())
        self._current_dispatcher_keys = [d[0] for d in dispatcher_items]
        labels = [d[1]["label"] for d in dispatcher_items]
        max_chars = max((len(lbl) for lbl in labels), default=10)
        self._action_combo.set_factory(self._make_action_factory(max_chars, xalign=1))
        self._action_combo.set_list_factory(self._make_action_factory(max_chars, xalign=0))
        self._action_combo.set_model(Gtk.StringList.new(labels))
        sel_idx = 0
        if select_dispatcher in self._current_dispatcher_keys:
            sel_idx = self._current_dispatcher_keys.index(select_dispatcher)
        self._action_combo.set_selected(sel_idx)
        self._action_combo.handler_unblock(self._sig_action)

    def _update_action_model_mouse(self, select_dispatcher: str = ""):
        """Populate the action combo with the curated bindm dispatcher list."""
        self._action_combo.handler_block(self._sig_action)
        self._current_dispatcher_keys = list(_BINDM_DISPATCHER_KEYS)
        labels = list(_BINDM_DISPATCHER_LABELS)
        max_chars = max((len(lbl) for lbl in labels), default=10)
        self._action_combo.set_factory(self._make_action_factory(max_chars, xalign=1))
        self._action_combo.set_list_factory(self._make_action_factory(max_chars, xalign=0))
        self._action_combo.set_model(Gtk.StringList.new(labels))
        sel_idx = 0
        if select_dispatcher in self._current_dispatcher_keys:
            sel_idx = self._current_dispatcher_keys.index(select_dispatcher)
        self._action_combo.set_selected(sel_idx)
        self._action_combo.handler_unblock(self._sig_action)

    def _on_category_changed(self, *_args):
        self._update_action_model(self._get_selected_category()["id"])
        self._refresh_arg_widget()

    def _on_action_changed(self, *_args):
        self._refresh_arg_widget()

    def _get_selected_dispatcher(self) -> str:
        idx = self._action_combo.get_selected()
        if 0 <= idx < len(self._current_dispatcher_keys):
            return self._current_dispatcher_keys[idx]
        return ""

    def _refresh_arg_widget(self):
        clear_children(self._arg_container)

        # Mouse-drag dispatchers (movewindow / resizewindow) take no argument,
        # so the parameters group is always hidden in mouse mode.
        if self._is_mouse_mode:
            self._arg_getter = lambda: ""
            self._arg_container.set_visible(False)
            return

        dispatcher = self._get_selected_dispatcher()
        info = DISPATCHER_INFO.get(dispatcher, {"arg_type": "text"})
        arg_type = info.get("arg_type", "text")
        current_arg = self._bind.arg if dispatcher == self._bind.dispatcher else ""
        widget, getter = _build_arg_widget(arg_type, current_arg)
        self._arg_getter = getter
        if widget is not None:
            arg_group = Adw.PreferencesGroup(title="Parameters")
            if isinstance(widget, Adw.PreferencesRow):
                arg_group.add(widget)
            else:
                wrapper = Adw.ActionRow(title="")
                wrapper.set_child(widget)
                arg_group.add(wrapper)
            self._arg_container.append(arg_group)
            self._arg_container.set_visible(True)
        else:
            self._arg_container.set_visible(False)

    # -- Bind type section --

    def _build_type_section(self, group):
        self._type_combo = Adw.ComboRow(
            title="Bind type",
            subtitle="Normal for most keybinds",
            model=Gtk.StringList.new(KEY_BIND_TYPE_LABELS),
        )
        current_type = self._bind.bind_type
        if current_type in KEY_BIND_TYPE_KEYS:
            self._type_combo.set_selected(KEY_BIND_TYPE_KEYS.index(current_type))
        group.add(self._type_combo)

    # -- Apply --

    def _on_apply(self, _btn):
        bind = self.get_bind()
        if not bind.key or not bind.dispatcher:
            return
        if self._conflict_finder:
            conflicts = self._conflict_finder(bind)
            if conflicts:
                self._show_conflict_warning(bind, conflicts)
                return
        if self._on_apply_callback:
            self._on_apply_callback(bind)
        self.close()

    def _show_conflict_warning(self, bind, conflicts):
        detail_lines = [
            f"  {c.format_shortcut()} \u2192 {format_action(c.dispatcher, c.arg)}"
            for c in conflicts
        ]
        detail = "\n".join(detail_lines)

        def on_confirm():
            if self._on_apply_callback:
                self._on_apply_callback(bind)
            self.close()

        confirm(
            self._window,
            heading="Duplicate keybind",
            body=(
                f"This key combination is already used by:\n{detail}\n\n"
                f"Hyprland will trigger all matching binds."
            ),
            label="Add Anyway",
            cancel_label="Go Back",
            on_confirm=on_confirm,
        )

    def get_bind(self) -> BindData:
        combo = self._get_current_key_combo()
        dispatcher = self._get_selected_dispatcher()
        if self._is_mouse_mode:
            bind_type = "bindm"
        else:
            type_idx = self._type_combo.get_selected()
            bind_type = (
                KEY_BIND_TYPE_KEYS[type_idx] if 0 <= type_idx < len(KEY_BIND_TYPE_KEYS) else "bind"
            )
        return BindData(
            bind_type=bind_type,
            mods=combo.mods,
            key=combo.key,
            dispatcher=dispatcher,
            arg=self._arg_getter(),
        )
