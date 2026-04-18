"""Main application window with sidebar navigation."""

from collections import Counter
from pathlib import Path
from typing import Protocol

from gi.repository import Adw, Gdk, Gio, GLib, Gtk
from hyprland_config import coerce_config_value, value_to_conf
from hyprland_socket import HyprlandError
from hyprland_state import ANIM_LOOKUP, HyprlandState

from hyprmod.core import config, profiles, schema
from hyprmod.core.state import AppState
from hyprmod.core.undo import (
    AnimationUndoEntry,
    BindsUndoEntry,
    CursorUndoEntry,
    MonitorsUndoEntry,
    OptionChange,
    UndoManager,
)
from hyprmod.data.bezier_data import get_curve_store
from hyprmod.pages.animations import AnimationsPage
from hyprmod.pages.binds import BindsPage
from hyprmod.pages.cursor import CursorPage
from hyprmod.pages.monitors import MonitorsPage
from hyprmod.pages.profiles import ProfilesPage
from hyprmod.pages.settings import SettingsPage
from hyprmod.ui import OptionRow, clear_children, confirm, create_option_row, make_page_layout
from hyprmod.ui.about import build_about_dialog
from hyprmod.ui.banner import DirtyBanner
from hyprmod.ui.options import digits_for_step
from hyprmod.ui.search import MIN_QUERY_LENGTH, SearchPage
from hyprmod.ui.shortcuts import build_shortcuts_window
from hyprmod.ui.sidebar import Sidebar
from hyprmod.ui.timer import Timer

# Hyprland option keys
ANIMATIONS_ENABLED = "animations:enabled"
INPUT_TOUCHPAD = "input:touchpad"


class SectionPage(Protocol):
    """Interface for special pages (animations, monitors, binds) that manage
    their own dirty/save/discard lifecycle independently of AppState."""

    def is_dirty(self) -> bool: ...
    def mark_saved(self) -> None: ...
    def discard(self) -> None: ...


CSS_PATH = Path(__file__).parent / "style.css"
GSETTINGS_DIR = Path(__file__).parent / "data"
SETTINGS_SCHEMA_ID = "io.github.bluemancz.hyprmod"
LEGACY_SETTINGS_SCHEMA_ID = "com.github.hyprmod"


class HyprModWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.set_title("HyprMod")
        self.set_default_size(900, 650)

        self._init_settings()
        self._apply_saved_config_path()

        self._saved_values, self._saved_sections = config.read_all_sections()
        self.hypr = HyprlandState()
        self._hyprland_available = self.hypr.online
        if self._hyprland_available:
            self.hypr.reload_compositor()  # Reset runtime state to match config files
        self._has_touchpad = self.hypr.has_touchpad() if self._hyprland_available else True
        # Load the option catalog matching the running compositor version.
        # Falls back to the bundled catalog when Hyprland is offline or the
        # version cannot be resolved (see core.schema.load_schema).
        self._schema = schema.load_schema(version=self.hypr.version)
        self.app_state = AppState(self.hypr)
        self._option_rows: dict[str, OptionRow] = {}
        self._dependents: dict[str, list[str]] = {}  # parent_key -> [dependent_keys]
        self._options_flat: dict[str, dict] = schema.get_options_flat(self._schema)
        self._key_to_group: dict[str, str] = {}  # option key -> sidebar group_id
        self._auto_save_timer = Timer()
        self._undo = UndoManager()

        # Optional page/widget references (populated during _build_ui)
        self._anim_details_box: Gtk.Box | None = None
        self._animations_page: AnimationsPage | None = None
        self._monitors_page: MonitorsPage | None = None
        self._binds_page: BindsPage | None = None
        self._cursor_page: CursorPage | None = None
        self._profiles_page: ProfilesPage | None = None
        self._settings_page: SettingsPage | None = None
        self._pre_search_page_id: str | None = None
        self._search_results: list | None = None

        self._load_css()
        self._build_ui()
        self._register_state()
        self._refresh_all_modified_indicators()

    def _init_settings(self):
        """Load GSettings for app preferences (auto-save, etc.)."""
        self._recompile_schemas_if_stale()
        schema_source = Gio.SettingsSchemaSource.new_from_directory(
            str(GSETTINGS_DIR),
            Gio.SettingsSchemaSource.get_default(),
            False,
        )
        schema_obj = schema_source.lookup(SETTINGS_SCHEMA_ID, False)
        if schema_obj:
            self._settings = Gio.Settings.new_full(schema_obj, None, None)
            self._migrate_legacy_settings(schema_source, schema_obj)
        else:
            self._settings = None

    def _migrate_legacy_settings(
        self,
        schema_source: Gio.SettingsSchemaSource,
        schema: Gio.SettingsSchema,
    ):
        """Copy user values from the legacy ``com.github.hyprmod`` schema.

        The schema id was renamed in c208927, which also moved the dconf
        path. Without this migration, users upgrading from older versions
        would silently lose their ``auto-save`` / ``config-path``
        preferences. Migrated keys are reset on the old path so this runs
        at most once per user.

        TODO: remove this method (and ``LEGACY_SETTINGS_SCHEMA_ID`` plus
        ``hyprmod/data/com.github.hyprmod.gschema.xml``) after a few
        releases — target ~6 months post-rename, or v0.3+. Users who
        haven't upgraded by then can reconfigure their two preferences
        manually.
        """
        if self._settings is None:
            return
        legacy_schema = schema_source.lookup(LEGACY_SETTINGS_SCHEMA_ID, False)
        if legacy_schema is None:
            return
        legacy = Gio.Settings.new_full(legacy_schema, None, None)
        shared_keys = set(legacy_schema.list_keys()) & set(schema.list_keys())
        for key in shared_keys:
            old_value = legacy.get_user_value(key)
            if old_value is None:
                continue
            # Respect any value the user already set under the new schema.
            if self._settings.get_user_value(key) is None:
                try:
                    self._settings.set_value(key, old_value)
                except (GLib.Error, TypeError):
                    # Type changed between schemas — skip without resetting
                    # so a future migration attempt can try again.
                    continue
            legacy.reset(key)

    @staticmethod
    def _recompile_schemas_if_stale():
        """Recompile GSettings schemas if the compiled file is stale or missing."""
        xml_files = list(GSETTINGS_DIR.glob("*.gschema.xml"))
        if not xml_files:
            return
        compiled = GSETTINGS_DIR / "gschemas.compiled"
        latest_xml_mtime = max(xml.stat().st_mtime for xml in xml_files)
        if not compiled.exists() or compiled.stat().st_mtime < latest_xml_mtime:
            import subprocess

            subprocess.run(
                ["glib-compile-schemas", str(GSETTINGS_DIR)],
                check=False,
            )

    def _apply_saved_config_path(self):
        """Apply the config-path setting from GSettings on startup."""
        if self._settings:
            path = self._settings.get_string("config-path")
            if path:
                config.set_gui_conf(Path(path))

    @property
    def auto_save(self) -> bool:
        if self._settings:
            return self._settings.get_boolean("auto-save")
        return False

    @auto_save.setter
    def auto_save(self, value: bool):
        if self._settings:
            self._settings.set_boolean("auto-save", value)

    @property
    def config_path(self) -> str:
        return str(config.gui_conf())

    @config_path.setter
    def config_path(self, value: str):
        default = str(config._DEFAULT_GUI_CONF)
        path = None if value == default else Path(value)
        config.set_gui_conf(path)
        if self._settings:
            self._settings.set_string("config-path", "" if path is None else value)

    def _load_css(self):
        if CSS_PATH.exists():
            provider = Gtk.CssProvider()
            provider.load_from_path(str(CSS_PATH))
            display = Gdk.Display.get_default()
            if display is not None:
                Gtk.StyleContext.add_provider_for_display(
                    display,
                    provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
                )

    def _build_ui(self):
        self._toast_overlay = Adw.ToastOverlay()
        self._main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._toast_overlay.set_child(self._main_box)
        self.set_content(self._toast_overlay)

        # Auto-save action (window-level, referenced by menu)
        auto_save_action = Gio.SimpleAction.new_stateful(
            "auto-save",
            None,
            GLib.Variant.new_boolean(self.auto_save),
        )
        auto_save_action.connect("activate", self._on_toggle_auto_save)
        self.add_action(auto_save_action)
        self._auto_save_action = auto_save_action

        # About action (window-level, referenced by menu)
        about_action = Gio.SimpleAction.new("show-about", None)
        about_action.connect("activate", self._on_show_about)
        self.add_action(about_action)

        # Hyprland status banner
        self._hyprland_banner = Adw.Banner(
            title="Hyprland not detected — changes will be saved to config files "
            "but not applied live"
        )
        self._hyprland_banner.set_revealed(not self._hyprland_available)
        self._main_box.append(self._hyprland_banner)

        # Navigation split view
        self._split_view = Adw.NavigationSplitView()
        self._split_view.set_vexpand(True)
        self._main_box.append(self._split_view)

        self._sidebar = Sidebar(
            on_page_selected=self._on_sidebar_selected,
            on_search_changed=self._on_search_changed,
            on_search_activate=self._on_search_activate,
            on_search_stop=self._on_search_stop,
            on_search_dismissed=self._on_search_dismissed,
        )
        self._split_view.set_sidebar(self._sidebar.nav_page)

        self._search_page_builder = SearchPage(self._schema)

        self._build_content_pane()
        groups, groups_by_id = self._build_pages()
        self._sidebar.populate(groups_by_id)
        self._build_search_page()

        # Cache the list of section pages (animations, monitors, binds) — stable after build
        self._section_pages: list[SectionPage] = [
            p
            for p in (
                self._animations_page,
                self._monitors_page,
                self._binds_page,
                self._cursor_page,
            )
            if p is not None
        ]

        self._setup_shortcuts()
        self._setup_help_overlay()

        if groups:
            first_id = groups[0]["id"]
            self.show_page(first_id)
            self._sidebar.select_first()

    def _build_content_pane(self):
        """Build the content pane with page stack and banner."""
        self._content_nav = Adw.NavigationPage(title="")

        self._page_stack = Gtk.Stack()
        self._page_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._page_stack.set_transition_duration(150)
        self._page_stack.set_vexpand(True)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_box.append(self._page_stack)

        self._banner = DirtyBanner(
            on_save=self._on_save,
            on_save_update=self._on_save_update_profile,
            on_save_without_update=self._on_save_without_update_profile,
            on_save_as_new=self._on_save_as_new_profile,
            on_discard=self._on_discard,
        )
        content_box.append(self._banner)

        self._content_nav.set_child(content_box)
        self._split_view.set_content(self._content_nav)

    def _build_pages(self) -> tuple[list[dict], dict[str, dict]]:
        """Build schema pages, special pages, and profiles. Returns (groups, groups_by_id)."""
        self._page_titles: dict[str, str] = {}
        groups = schema.get_groups(self._schema)
        groups_by_id: dict[str, dict] = {}

        for group in groups:
            target_group = group.get("parent_page", group["id"])
            for section in group.get("sections", []):
                for option in section.get("options", []):
                    self._key_to_group[option["key"]] = target_group

            if group.get("hidden"):
                continue
            groups_by_id[group["id"]] = group

            page = self._build_page(group)
            self._page_stack.add_named(page, group["id"])
            self._page_titles[group["id"]] = group["label"]

        self._binds_page = BindsPage(
            self,
            on_dirty_changed=self._on_section_dirty,
            push_undo=self._undo.push,
            saved_sections=self._saved_sections,
        )
        binds_nav = self._binds_page.build(header=self._make_page_header("Keybinds"))
        self._page_stack.add_named(binds_nav, "binds")
        self._page_titles["binds"] = "Keybinds"

        self._monitors_page = MonitorsPage(
            self,
            on_dirty_changed=self._on_section_dirty,
            push_undo=self._undo.push,
            saved_sections=self._saved_sections,
        )
        monitors_nav = self._monitors_page.build(header=self._make_page_header("Monitors"))
        self._page_stack.add_named(monitors_nav, "monitors")
        self._page_titles["monitors"] = "Monitors"
        self._search_page_builder.add_entries(self._monitors_page.get_search_entries())
        self._search_page_builder.add_entries(CursorPage.get_search_entries())

        self._profiles_page = ProfilesPage(self)
        profiles_nav = self._profiles_page.build(header=self._make_page_header("Profiles"))
        self._page_stack.add_named(profiles_nav, "profiles")
        self._page_titles["profiles"] = "Profiles"

        self._settings_page = SettingsPage(self)
        settings_nav = self._settings_page.build(header=self._make_page_header("Settings"))
        self._page_stack.add_named(settings_nav, "settings")
        self._page_titles["settings"] = "Settings"

        return groups, groups_by_id

    def _build_search_page(self):
        """Build the search results page in the content stack."""
        toolbar, _, self._search_content_box, _ = make_page_layout(
            header=self._make_page_header("Search Results")
        )
        self._page_stack.add_named(toolbar, "search")
        self._page_titles["search"] = "Search Results"

    def _make_page_header(self, title: str) -> Adw.HeaderBar:
        """Create a content page header with menu button."""
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title=title))

        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("menu-symbolic")

        menu = Gio.Menu()
        prefs_section = Gio.Menu()
        prefs_section.append("Auto-save", "win.auto-save")
        menu.append_section(None, prefs_section)

        help_section = Gio.Menu()
        help_section.append("Keyboard Shortcuts", "win.show-help-overlay")
        help_section.append("About HyprMod", "win.show-about")
        menu.append_section(None, help_section)

        menu_button.set_menu_model(menu)
        header.pack_end(menu_button)

        return header

    def _build_page(self, group: dict) -> Adw.ToolbarView:
        toolbar_view, _, content_box, _ = make_page_layout(
            header=self._make_page_header(group["label"])
        )

        is_animations = group.get("id") == "animations"
        is_cursor = group.get("id") == "cursor"

        if is_animations:
            self._animations_page = AnimationsPage(
                self,
                on_dirty_changed=self._on_section_dirty,
                push_undo=self._undo.push,
                saved_sections=self._saved_sections,
            )
            content_box.append(self._animations_page.build_curve_editor_widget())

        if is_cursor:
            self._cursor_page = CursorPage(
                self,
                on_dirty_changed=self._on_section_dirty,
                push_undo=self._undo.push,
                saved_sections=self._saved_sections,
            )
            content_box.append(self._cursor_page.build_widget())

        for pref_group in self._build_section_widgets(group):
            content_box.append(pref_group)

        if is_animations and self._animations_page is not None:
            self._anim_details_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
            self._anim_details_box.append(self._animations_page.build_widget())
            content_box.append(self._anim_details_box)

        return toolbar_view

    def _build_section_widgets(self, group: dict) -> list[Adw.PreferencesGroup]:
        """Build PreferencesGroup widgets for a schema group's sections.

        Registers option rows in the window's state and row tracking.
        """
        result = []
        for section in group.get("sections", []):
            pref_group = Adw.PreferencesGroup(title=section.get("label", ""))
            if section.get("description"):
                pref_group.set_description(section["description"])

            # Disable sections for unavailable hardware
            section_id = section.get("id", "")
            if section_id == INPUT_TOUCHPAD and not self._has_touchpad:
                pref_group.set_description("No touchpad detected")
                pref_group.set_sensitive(False)
                result.append(pref_group)
                continue

            for option in section.get("options", []):
                value = option.get("default")
                opt_row = create_option_row(
                    option,
                    value,
                    on_change=self._on_option_changed,
                    on_reset=self._on_option_reset,
                    on_discard=self._on_option_discard,
                )
                if opt_row:
                    self._option_rows[option["key"]] = opt_row
                    pref_group.add(opt_row.row)
                    parent = option.get("depends_on")
                    if parent:
                        self._dependents.setdefault(parent, []).append(option["key"])

            result.append(pref_group)
        return result

    def build_schema_group_widgets(self, group_id: str) -> list[Adw.PreferencesGroup]:
        """Build PreferencesGroup widgets for a schema group by ID.

        Used by special pages (e.g. monitors) that embed schema-driven options.
        """
        groups = schema.get_groups(self._schema)
        group = next((g for g in groups if g["id"] == group_id), None)
        if not group:
            return []
        return self._build_section_widgets(group)

    def _register_state(self):
        options_flat = self._options_flat
        for key, option in options_flat.items():
            saved = self._saved_values.get(key)
            if saved is not None:
                saved = coerce_config_value(saved, option.get("type", ""))
            # Compute display digits for float options so AppState can
            # normalize values to widget precision on ingress.
            digits = None
            if option.get("type") == "float":
                digits = digits_for_step(option.get("step", 0.01))
            self.app_state.register(key, option.get("default"), saved, digits=digits)

        # Disable rows for options not recognized by the running Hyprland
        for key, opt_row in self._option_rows.items():
            state = self.app_state.get(key)
            if state and not state.available:
                opt_row.row.set_sensitive(False)
                opt_row.row.set_tooltip_text(
                    f"Option '{key}' is not available in this Hyprland version"
                )

        # Push AppState's authoritative values to widgets (AppState normalizes
        # floats and hex strings, so the widget must show the same value).
        for key, opt_row in self._option_rows.items():
            state = self.app_state.get(key)
            if state and state.live_value is not None:
                opt_row.set_value_silent(state.live_value)

        self.app_state.on_change(self._on_state_changed)
        self._update_dna()

        # Set initial visibility of animation details based on animations:enabled
        if self._anim_details_box is not None:
            state = self.app_state.get(ANIMATIONS_ENABLED)
            self._anim_details_box.set_visible(bool(state and state.live_value))

    def _update_dna(self):
        """Update the sidebar DNA graphic from saved values."""
        saved = {
            key: value_to_conf(s.saved_value)
            for key, s in self.app_state.options.items()
            if s.saved_managed
        }
        self._sidebar.update_dna(saved)

    def _notify_ui_change(self):
        """Update banner and sidebar badges after an option change."""
        self._update_banner()
        self._update_sidebar_badges()

    def _refresh_all_modified_indicators(self):
        for key, opt_row in self._option_rows.items():
            state = self.app_state.get(key)
            if state:
                opt_row.update_modified_state(state.managed, state.is_dirty, state.saved_managed)
        self._refresh_all_dependents()
        self._update_sidebar_badges()

    def _update_sidebar_badges(self):
        """Update pending-change count badges on sidebar rows."""
        # Count dirty options per schema group
        counts: Counter[str] = Counter()
        for key, state in self.app_state.options.items():
            if state.is_dirty:
                group_id = self._key_to_group.get(key)
                if group_id:
                    counts[group_id] += 1

        # Special pages: count dirty items
        if self._animations_page and self._animations_page.is_dirty():
            n = sum(1 for name in ANIM_LOOKUP if self._animations_page.is_anim_dirty(name))
            counts["animations"] += n
        if self._binds_page and self._binds_page.is_dirty():
            counts["binds"] += 1
        if self._monitors_page and self._monitors_page.is_dirty():
            counts["monitors"] += self._monitors_page.dirty_count()
        if self._cursor_page and self._cursor_page.is_dirty():
            counts["cursor"] += 1

        self._sidebar.update_badges(counts)

    def _refresh_all_dependents(self):
        """Show/hide dependent options based on their parent's current value."""
        for parent_key in self._dependents:
            self._update_dependents(parent_key)

    def _is_option_visible(self, key: str) -> bool:
        """Check if an option should be visible (parent enabled and visible)."""
        # Walk up the depends_on chain
        option = self._options_flat.get(key)
        if not option:
            return True
        parent_key = option.get("depends_on")
        if not parent_key:
            return True
        # Parent must be visible itself and have a truthy value
        if not self._is_option_visible(parent_key):
            return False
        parent_state = self.app_state.get(parent_key)
        return bool(parent_state.live_value) if parent_state else True

    def _update_dependents(self, parent_key: str):
        """Update visibility and source values of options that depend on parent_key."""
        parent_state = self.app_state.get(parent_key)
        parent_value = parent_state.live_value if parent_state else None

        for dep_key in self._dependents.get(parent_key, []):
            opt_row = self._option_rows.get(dep_key)
            if opt_row:
                visible = self._is_option_visible(dep_key)
                opt_row.row.set_visible(visible)
                # Refresh dynamic source if the dependent has one
                if parent_value is not None:
                    source_args = opt_row.option.get("source_args", {})
                    # Find which source_arg maps to this parent key
                    refresh_kwargs = {}
                    for arg_name, _default in source_args.items():
                        if opt_row.option.get("depends_on") == parent_key:
                            refresh_kwargs[arg_name] = str(parent_value)
                    if refresh_kwargs:
                        opt_row.refresh_source(**refresh_kwargs)
                        dep_state = self.app_state.get(dep_key)
                        if dep_state and dep_state.live_value is not None:
                            opt_row.set_value_silent(dep_state.live_value)
                # Recurse: if this dependent also has dependents, update them too
                if dep_key in self._dependents:
                    self._update_dependents(dep_key)

    # -- Keyboard shortcuts --

    def _setup_shortcuts(self):
        """Register keyboard shortcuts as window actions with accels.

        Using Gio actions + set_accels_for_action ensures shortcuts are handled
        at the application level, before GTK's built-in widget shortcuts
        (e.g. Ctrl+Z undo in text entries) can intercept them.
        """
        app = self.get_application()
        if app is None:
            return

        shortcuts = [
            ("save", self._on_save, ["<Control>s"]),
            ("undo", self._on_undo, ["<Control>z"]),
            ("redo", self._on_redo, ["<Control><Shift>z"]),
            ("search", self._on_show_search, ["<Control>f"]),
            ("clear-search", self._on_hide_search, ["Escape"]),
        ]

        for name, handler, accels in shortcuts:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, fn=handler: fn())
            self.add_action(action)
            app.set_accels_for_action(f"win.{name}", accels)

    def _setup_help_overlay(self):
        """Attach a ``Gtk.ShortcutsWindow`` and bind ``win.show-help-overlay``.

        ``set_help_overlay`` registers the action automatically; we only need
        to add the accelerators. ``<Control>question`` is the GNOME-standard
        shortcut; ``F1`` is included as a familiar fallback.
        """
        shortcuts_window = build_shortcuts_window()
        self.set_help_overlay(shortcuts_window)

        app = self.get_application()
        if app is not None:
            app.set_accels_for_action("win.show-help-overlay", ["<Control>question", "F1"])

    def _on_show_about(self, *_args):
        """Show the About dialog."""
        build_about_dialog(running_hyprland_version=self.hypr.version).present(self)

    # -- Search --

    def _on_show_search(self, *_args):
        """Show and focus the search entry."""
        self._sidebar.search_button.set_active(True)

    def _on_hide_search(self, *_args):
        """Hide search entry (triggers _on_search_dismissed via sidebar)."""
        self._sidebar.search_button.set_active(False)

    def _on_search_dismissed(self):
        """Restore the previous page when search is closed."""
        if self._pre_search_page_id:
            self.show_page(self._pre_search_page_id)
            self._pre_search_page_id = None

    def _on_search_changed(self, entry):
        query = entry.get_text().strip()
        if not query or len(query) < MIN_QUERY_LENGTH:
            if self._pre_search_page_id:
                self.show_page(self._pre_search_page_id)
            return

        # Save current page before showing search
        if not self._pre_search_page_id:
            self._pre_search_page_id = self._sidebar.get_selected_group_id()

        self._search_results = self._search_page_builder.search(query)
        widget = self._search_page_builder.build_results_widget(
            self._search_results,
            on_activate=self._on_search_result_activate,
        )
        clear_children(self._search_content_box)
        self._search_content_box.append(widget)
        self.show_page("search")
        self._sidebar.deselect_all()

    def _on_search_activate(self, _entry):
        """Enter pressed — focus the first search result row for keyboard navigation."""
        if not self._search_results:
            return
        widget = self._search_content_box.get_first_child()
        if widget:
            widget.child_focus(Gtk.DirectionType.TAB_FORWARD)

    def _on_search_stop(self, *_args):
        self._on_hide_search()

    def _on_search_result_activate(self, group_id: str, option_key: str):
        """Navigate to the group containing the selected search result."""
        if group_id == "monitor_globals":
            group_id = "monitors"
        self._pre_search_page_id = None
        self._sidebar.search_button.set_active(False)
        self.show_page(group_id)
        self._sidebar.select_row(group_id)

        opt_row = self._option_rows.get(option_key)
        if opt_row:

            def _scroll_and_highlight():
                opt_row.row.grab_focus()
                opt_row.flash_highlight()
                return GLib.SOURCE_REMOVE

            GLib.idle_add(_scroll_and_highlight)

    # -- Sidebar --

    def show_page(self, gid: str):
        """Switch the content pane to the given page."""
        if gid in self._page_titles:
            if self._monitors_page and gid != "monitors":
                self._monitors_page.confirm_changes()
            self._page_stack.set_visible_child_name(gid)
            self._content_nav.set_title(self._page_titles[gid])

    def _on_sidebar_selected(self, group_id: str):
        self.show_page(group_id)

    # -- Option changes --

    def _on_option_changed(self, key: str, value):
        # Skip no-op changes (e.g. SpinButton rounding triggers on focus-out)
        state = self.app_state.get(key)
        if state and value == state.live_value:
            return

        # Clear dependent before applying the parent change to avoid invalid configs
        for dep_key in self._dependents.get(key, []):
            dep_option = self._options_flat.get(dep_key)
            if dep_option and dep_option.get("source") and not dep_option.get("multi"):
                self.app_state.set_live(dep_key, dep_option.get("default", ""))

        opt_row = self._option_rows.get(key)
        try:
            entry = self.app_state.set_live(key, value)
        except HyprlandError as e:
            if opt_row:
                opt_row.flash_error()
                if state:
                    opt_row.set_value_silent(state.live_value)
            self.show_toast(f"Failed to set {key} — {e}", timeout=5)
            return
        if entry is None and opt_row:
            opt_row.flash_error()
            if state:
                opt_row.set_value_silent(state.live_value)
        elif entry is not None:
            self._undo.push(entry)
            if self.auto_save:
                self._schedule_auto_save()

    def _sync_option_row(self, key: str, *, flash: bool = False):
        """Push current AppState to the widget and update dependents."""
        opt_row = self._option_rows.get(key)
        state = self.app_state.get(key)
        if opt_row and state:
            if state.live_value is not None:
                opt_row.set_value_silent(state.live_value)
            opt_row.update_modified_state(state.managed, state.is_dirty, state.saved_managed)
            if flash:
                opt_row.flash_highlight(duration_ms=600)
        if key in self._dependents:
            self._update_dependents(key)

    def _on_option_reset(self, key: str, _default_value):
        """Remove override — preview the fallback value and mark pending."""
        if key not in self._option_rows:
            return

        fallback = self.hypr.get_fallback_value(key, config.gui_conf())
        self.app_state.reset_to_value(key, fallback)
        self._sync_option_row(key, flash=True)
        self._notify_ui_change()
        if self.auto_save:
            self._schedule_auto_save()

    def _on_option_discard(self, key: str):
        """Discard changes on a single option — revert to saved state."""
        state = self.app_state.get(key)
        if state and state.is_dirty:
            self._undo.push(
                OptionChange(
                    key=key,
                    old_value=state.live_value,
                    new_value=state.saved_value,
                    old_managed=state.managed,
                    new_managed=state.saved_managed,
                ),
                merge=False,
            )
        if not self.app_state.discard_one(key):
            return
        self._sync_option_row(key, flash=True)
        self._notify_ui_change()

    def has_dirty(self) -> bool:
        """Check if any section has unsaved changes."""
        if self.app_state.has_dirty():
            return True
        return any(s.is_dirty() for s in self._section_pages)

    def _update_banner(self):
        """Show or hide the unsaved changes banner."""
        self._banner.set_active_profile(profiles.get_active_id() is not None)
        has_dirty = self.has_dirty()
        if has_dirty and not self.auto_save:
            self._banner.show_dirty()
        else:
            self._banner.hide()

    def _on_state_changed(self, key: str):
        self._update_banner()
        self._update_sidebar_badges()
        self._sync_option_row(key)

        if key == ANIMATIONS_ENABLED and self._anim_details_box is not None:
            state = self.app_state.get(key)
            self._anim_details_box.set_visible(bool(state and state.live_value))

    def _on_section_dirty(self):
        """Called when any section (animations, binds, monitors) changes."""
        self._update_banner()
        self._update_sidebar_badges()
        if self.auto_save and self.has_dirty():
            self._schedule_auto_save()

    # -- Undo / Redo --

    def _on_undo(self, *_args):
        self._apply_undo_redo(undo=True)

    def _on_redo(self, *_args):
        self._apply_undo_redo(undo=False)

    def _apply_undo_redo(self, undo: bool):
        entry = self._undo.pop_undo() if undo else self._undo.pop_redo()
        if entry is None:
            return
        confirm = self._undo.confirm_undo if undo else self._undo.confirm_redo
        if isinstance(entry, OptionChange):
            value = entry.old_value if undo else entry.new_value
            managed = entry.old_managed if undo else entry.new_managed
            try:
                success = self.app_state.apply_option_value(entry.key, value, managed)
            except HyprlandError as e:
                self.show_toast(f"Failed to set {entry.key} — {e}", timeout=5)
                return
            if success:
                confirm(entry)
                opt_row = self._option_rows.get(entry.key)
                if opt_row:
                    opt_row.set_value_silent(value)
        elif isinstance(entry, AnimationUndoEntry) and self._animations_page is not None:
            anim_state = entry.anim_old if undo else entry.anim_new
            self._animations_page.restore_state(entry.anim_name, anim_state)
            confirm(entry)
        elif isinstance(entry, BindsUndoEntry) and self._binds_page is not None:
            items = entry.old_items if undo else entry.new_items
            baselines = entry.old_baselines if undo else entry.new_baselines
            overrides = entry.old_session_overrides if undo else entry.new_session_overrides
            self._binds_page.restore_snapshot(items, baselines, overrides)
            confirm(entry)
        elif isinstance(entry, MonitorsUndoEntry) and self._monitors_page is not None:
            monitors = entry.old_monitors if undo else entry.new_monitors
            owned = entry.old_owned if undo else entry.new_owned
            self._monitors_page.restore_snapshot(monitors, owned)
            confirm(entry)
        elif isinstance(entry, CursorUndoEntry) and self._cursor_page is not None:
            theme = entry.old_theme if undo else entry.new_theme
            size = entry.old_size if undo else entry.new_size
            self._cursor_page.restore_snapshot(theme, size)
            confirm(entry)

    # -- Save with animation --

    def _collect_save_sections(self):
        """Collect sections to save: dirty sections + previously saved sections.

        A section is only included if it was already in hyprland-gui.conf
        (HyprMod owns it) or the user changed it in this session.
        Parses the config file once to check all sections.
        """
        _, saved_sections = config.read_all_sections()

        bind_lines = None
        if self._binds_page is not None:
            has_saved = config.collect_section(saved_sections, config.BIND_KEYS)
            if has_saved or self._binds_page.is_dirty():
                bind_lines = self._binds_page.get_bind_lines()

        monitor_lines = None
        if self._monitors_page is not None:
            saved_monitors = config.collect_section(saved_sections, config.KEYWORD_MONITOR)
            if saved_monitors or self._monitors_page.is_dirty():
                monitor_lines = self._monitors_page.get_monitor_lines()

        animation_lines = None
        bezier_lines = None
        if self._animations_page is not None:
            anim_dirty = self._animations_page.is_dirty()
            existing_anims = config.collect_section(saved_sections, config.KEYWORD_ANIMATION)
            if anim_dirty or existing_anims:
                animation_lines, used_curves = self._animations_page.get_animation_lines()
                if used_curves:
                    bezier_lines = get_curve_store().get_curve_definitions(used_curves)

        env_lines = None
        if self._cursor_page is not None:
            has_managed = self._cursor_page.has_managed_env(saved_sections)
            if has_managed or self._cursor_page.is_dirty():
                env_lines = self._cursor_page.get_env_lines()

        return bind_lines, monitor_lines, animation_lines, bezier_lines, env_lines

    def _perform_save(self):
        values = self.app_state.get_all_live_values()
        (
            bind_lines,
            monitor_lines,
            animation_lines,
            bezier_lines,
            env_lines,
        ) = self._collect_save_sections()
        config.write_all(
            values,
            bind_lines=bind_lines,
            monitor_lines=monitor_lines,
            animation_lines=animation_lines,
            bezier_lines=bezier_lines,
            env_lines=env_lines,
        )
        self.app_state.mark_saved()
        self.hypr.clear_pending()
        for section in self._section_pages:
            section.mark_saved()
        self._undo.clear()
        self._update_dna()
        self._refresh_all_modified_indicators()

    def save(self):
        """Public save API — performs save and shows banner animation."""
        self._perform_save()
        self._banner.show_saved()

    def reload_after_profile(self):
        """Refresh all state after profile activation.

        Re-reads saved config, updates managed flags, then calls
        ``hypr.sync()`` which re-reads all state from the compositor
        and fires change notifications — widgets and section pages
        update themselves reactively.
        """
        # Re-read saved values from the new config file
        self._saved_values, self._saved_sections = config.read_all_sections()

        # Update managed flags from the new saved config
        self._update_managed_flags()

        # Sync options from live Hyprland (fires _on_state_changed per key)
        self.app_state.refresh_all_live()

        # Sync subsystems — animations and monitors react via on_change
        self.hypr.sync()

        # Reload animations owned names from new config
        if self._animations_page is not None:
            self._animations_page.load_owned_names()
            self._animations_page.load_hyprland_curves()

        # Binds still need manual reload (no library-level state)
        if self._binds_page is not None:
            self._binds_page.reload_from_live()

        # Monitors ownership may differ between profiles
        if self._monitors_page is not None:
            self._monitors_page.reload_from_saved()

        if self._cursor_page is not None:
            self._cursor_page.reload_from_saved(self._saved_sections)

        self._undo.clear()
        self._update_dna()
        self._banner.hide()

    def _update_managed_flags(self):
        """Update managed flags and saved values from the current saved config."""
        options_flat = self._options_flat
        for key, state in self.app_state.options.items():
            saved = self._saved_values.get(key)
            if saved is not None:
                option = options_flat.get(key)
                if option:
                    saved = coerce_config_value(saved, option.get("type", ""))
                state.saved_value = saved
                state.managed = True
                state.saved_managed = True
            else:
                # Not in config — saved value matches live (no override)
                state.saved_value = state.live_value
                state.managed = False
                state.saved_managed = False

    def add_toast(self, toast: Adw.Toast):
        """Add a pre-built toast to the overlay."""
        self._toast_overlay.add_toast(toast)

    def show_toast(self, message: str, timeout: int = 2):
        """Show a transient toast notification."""
        toast = Adw.Toast(title=message, timeout=timeout)
        self.add_toast(toast)

    def _on_save(self, *_args):
        # Entry point for Ctrl+S — the banner's primary button already routes
        # to _on_save_update_profile when a profile is active, but the keyboard
        # shortcut bypasses the banner so we need the check here too.
        if profiles.get_active_id() is not None:
            self._on_save_update_profile()
        else:
            self.save()

    def _on_save_update_profile(self, *_args):
        """Save config and update the active profile to match."""
        self.save()
        active_id = profiles.get_active_id()
        if active_id:
            profiles.update(active_id)
        if self._profiles_page:
            self._profiles_page.rebuild()

    def _on_save_without_update_profile(self, *_args):
        """Save config but deactivate the profile (it no longer matches)."""
        self.save()
        profiles.set_active_id(None)
        if self._profiles_page:
            self._profiles_page.rebuild()

    def _on_save_as_new_profile(self, *_args):
        """Show name dialog, save config as a new profile, navigate to profiles."""
        if self._profiles_page:
            self._profiles_page.save_as_new_and_navigate()

    # -- Discard --

    def _on_discard(self, *_args):
        n = len(self.app_state.get_dirty_values())
        for page in self._section_pages:
            if page.is_dirty():
                n += 1

        confirm(
            self,
            "Discard All Changes?",
            f"{n} unsaved change{'s' if n != 1 else ''} will be reverted.",
            "Discard",
            self._do_discard,
        )

    def _do_discard(self):
        reverted = self.app_state.discard_dirty()
        for key in reverted:
            opt_row = self._option_rows.get(key)
            state = self.app_state.get(key)
            if opt_row and state and state.live_value is not None:
                opt_row.set_value_silent(state.live_value)
        for section in self._section_pages:
            section.discard()
        self._banner.hide()
        self._undo.clear()
        self._refresh_all_modified_indicators()

    # -- Auto-save --

    def _on_toggle_auto_save(self, action, _param):
        new_val = not action.get_state().get_boolean()
        action.set_state(GLib.Variant.new_boolean(new_val))
        self.auto_save = new_val

        if self._settings_page:
            self._settings_page.sync_auto_save(new_val)

        # If just enabled and there are unsaved changes, save immediately
        if new_val and self.has_dirty():
            self._on_save()

    def _schedule_auto_save(self):
        """Debounced auto-save: wait 800ms after last change before writing."""
        self._auto_save_timer.schedule(800, self._auto_save_fire)

    def _auto_save_fire(self):
        self._perform_save()
        self._banner.hide()
