"""Keybind edit dialog — add/edit a keybind with category/action cascade."""

from gi.repository import Adw, Gdk, Gtk
from hyprland_config import BindData
from hyprland_socket import MOD_BITS, HyprlandError

from hyprmod.binds.dispatchers import (
    BIND_TYPES,
    CATEGORY_BY_ID,
    DISPATCHER_CATEGORIES,
    DISPATCHER_INFO,
    DispatcherCategory,
    categorize_dispatcher,
    format_action,
)
from hyprmod.binds.helpers import MODIFIER_KEYVALS, gdk_state_to_mods
from hyprmod.ui import clear_children

BIND_TYPE_KEYS = list(BIND_TYPES.keys())
BIND_TYPE_LABELS = [v["label"] for v in BIND_TYPES.values()]

# UI-specific filtered views: exclude the "advanced" catch-all from dialog dropdowns.
DIALOG_CATEGORIES = [c for c in DISPATCHER_CATEGORIES if c["id"] != "advanced"]
DIALOG_CATEGORY_LABELS = [c["label"] for c in DIALOG_CATEGORIES]

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
    """Dialog for adding/editing a keybind with category -> action cascade."""

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
        self._focus_handler = None
        self._current_dispatcher_keys: list[str] = []

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

        key_group = Adw.PreferencesGroup(title="Key Combination")
        self._build_key_section(key_group)
        content.append(key_group)

        action_group = Adw.PreferencesGroup(title="Action")
        self._build_action_section(action_group)
        content.append(action_group)

        self._arg_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self._arg_container)

        self._adv_group = Adw.PreferencesGroup(title="Advanced")
        self._build_type_section(self._adv_group)
        content.append(self._adv_group)

        scrolled.set_child(content)
        toolbar.set_content(scrolled)
        self.set_child(toolbar)

        self._refresh_arg_widget()

    # -- Key section --

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

        self._key_entry = Adw.EntryRow(title="Key")
        self._key_entry.set_text(current_key)
        self._key_entry.connect("changed", self._on_manual_key_changed)
        manual_expander.add_row(self._key_entry)
        group.add(manual_expander)

    def _on_start_capture(self, _btn):
        if self._capturing:
            self._stop_capture()
            return
        self._capturing = True
        self._capture_label.set_label("Press a key combination\u2026")
        self._capture_btn.set_label("Cancel")
        self._capture_btn.add_css_class("destructive-action")
        self._capture_btn.remove_css_class("suggested-action")

        self._register_capture_submap()
        try:
            self._window.hypr.dispatch("submap", "hyprmod_capture")
        except HyprlandError:
            pass

        toplevel = self._window.get_root()
        if toplevel:
            self._focus_handler = toplevel.connect(
                "notify::is-active", self._on_window_focus_changed
            )

        self._key_controller = Gtk.EventControllerKey.new()
        self._key_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self._key_controller.connect("key-pressed", self._on_key_captured)
        self._capture_btn.add_controller(self._key_controller)
        self._capture_btn.grab_focus()

    def _stop_capture(self):
        self._capturing = False
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
        try:
            self._window.hypr.dispatch("submap", "reset")
        except HyprlandError:
            pass

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
            except HyprlandError:
                pass

    def _on_key_captured(self, controller, keyval, keycode, state):
        key_name = Gdk.keyval_name(keyval)
        if not key_name:
            return True
        if key_name == "Escape":
            self._stop_capture()
            return True
        if key_name in MODIFIER_KEYVALS:
            mods = gdk_state_to_mods(state)
            preview_parts = [m.upper() for m in mods]
            preview_parts.append("...")
            self._capture_label.set_label(" + ".join(preview_parts))
            return True
        mods = gdk_state_to_mods(state)
        display_key = key_name.upper() if len(key_name) == 1 else key_name
        for mod_name, switch in self._mod_checks.items():
            switch.set_active(mod_name in mods)
        self._key_entry.set_text(display_key)
        self._update_capture_display()
        self._stop_capture()
        return True

    def _on_manual_mod_changed(self, *_args):
        if not self._capturing:
            self._update_capture_display()

    def _on_manual_key_changed(self, *_args):
        if not self._capturing:
            self._update_capture_display()

    def _update_capture_display(self):
        self._capture_label.set_label(self._get_current_key_combo().format_shortcut())

    def _get_current_key_combo(self) -> BindData:
        mods = [name for name, row in self._mod_checks.items() if row.get_active()]
        key = self._key_entry.get_text().strip()
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
            model=Gtk.StringList.new(BIND_TYPE_LABELS),
        )
        current_type = self._bind.bind_type
        if current_type in BIND_TYPE_KEYS:
            self._type_combo.set_selected(BIND_TYPE_KEYS.index(current_type))
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
        dialog = Adw.AlertDialog(
            heading="Duplicate keybind",
            body=f"This key combination is already used by:\n{detail}\n\n"
            f"Hyprland will trigger all matching binds.",
        )
        dialog.add_response("back", "Go Back")
        dialog.add_response("add", "Add Anyway")
        dialog.set_response_appearance("add", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("back")
        dialog.set_close_response("back")

        def on_response(_dialog, response):
            if response == "add":
                if self._on_apply_callback:
                    self._on_apply_callback(bind)
                self.close()

        dialog.connect("response", on_response)
        dialog.present(self._window)

    def get_bind(self) -> BindData:
        combo = self._get_current_key_combo()
        dispatcher = self._get_selected_dispatcher()
        type_idx = self._type_combo.get_selected()
        bind_type = BIND_TYPE_KEYS[type_idx] if 0 <= type_idx < len(BIND_TYPE_KEYS) else "bind"
        return BindData(
            bind_type=bind_type,
            mods=combo.mods,
            key=combo.key,
            dispatcher=dispatcher,
            arg=self._arg_getter(),
            owned=True,
        )
