"""Main application window with sidebar navigation."""

import subprocess
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk
from hyprland_config import Rule, coerce_config_value
from hyprland_socket import HyprlandError
from hyprland_state import ANIM_LOOKUP, HyprlandState

from hyprmod.constants import APPLICATION_ID
from hyprmod.core import config, profiles, schema
from hyprmod.core.bug_report import build_bug_report_url
from hyprmod.core.state import AppState
from hyprmod.core.undo import OptionChange, PairedOptionChange, UndoManager
from hyprmod.data import bundled_data_dir
from hyprmod.data.bezier_data import get_curve_store
from hyprmod.pages.animations import AnimationsPage
from hyprmod.pages.autostart import AutostartPage
from hyprmod.pages.binds import BindsPage
from hyprmod.pages.cursor import CursorPage
from hyprmod.pages.env_vars import EnvVarsPage
from hyprmod.pages.layer_rules import LayerRulesPage
from hyprmod.pages.layouts import LayoutsPage
from hyprmod.pages.monitors import MonitorsPage
from hyprmod.pages.pending import PendingChangesPage
from hyprmod.pages.profiles import ProfilesPage
from hyprmod.pages.section import SectionPage
from hyprmod.pages.settings import SettingsPage
from hyprmod.pages.window_rules import WindowRulesPage
from hyprmod.pages.workspaces import WorkspacesPage
from hyprmod.ui import (
    KeyboardLayoutsOptionRow,
    OptionRow,
    clear_children,
    confirm,
    create_option_row,
    make_page_layout,
)
from hyprmod.ui.about import build_about_dialog
from hyprmod.ui.banner import DirtyBanner
from hyprmod.ui.deprecation_controller import ACTION_NAME as DEPRECATIONS_ACTION
from hyprmod.ui.deprecation_controller import DeprecationController
from hyprmod.ui.lua_migration_controller import ACTION_NAME as LUA_MIGRATION_ACTION
from hyprmod.ui.lua_migration_controller import LuaMigrationController
from hyprmod.ui.options import digits_for_step
from hyprmod.ui.pending_chip import PendingChipGroup
from hyprmod.ui.search import MIN_QUERY_LENGTH, SearchPage
from hyprmod.ui.shortcuts import build_shortcuts_window
from hyprmod.ui.sidebar import Sidebar
from hyprmod.ui.timer import Timer

# Hyprland option keys
ANIMATIONS_ENABLED = "animations:enabled"
INPUT_TOUCHPAD = "input:touchpad"


CSS_PATH = Path(__file__).parent / "style.css"
# The GSettings schema id matches our application id by convention; aliasing
# here keeps the schema lookup explicit at point of use.
SETTINGS_SCHEMA_ID = APPLICATION_ID


class HyprModWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.set_title("HyprMod")
        self.set_default_size(900, 650)

        self._init_settings()
        self._apply_saved_config_path()

        # Warm the managed-config cache so the first ``saved_sections`` access
        # below doesn't pay for a parse synchronously during widget construction.
        config.read_cached()
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
        # Track the PreferencesGroup that owns each option row so we can hide
        # whole groups that turn out empty on the running Hyprland version
        # (e.g. all rows in a "Frame rate" group are options removed in 0.55).
        self._row_owner_group: dict[str, Adw.PreferencesGroup] = {}
        self._dependents: dict[str, list[str]] = {}  # parent_key -> [dependent_keys]
        self._options_flat: dict[str, dict] = schema.get_options_flat(self._schema)
        self._key_to_group: dict[str, str] = {}  # option key -> sidebar group_id
        self._auto_save_timer = Timer()
        self._undo = UndoManager()

        # Optional page/widget references (populated during _build_ui)
        self._anim_details_box: Gtk.Box | None = None
        self._animations_page: AnimationsPage | None = None
        self._monitors_page: MonitorsPage | None = None
        self._workspaces_page: WorkspacesPage | None = None
        self._binds_page: BindsPage | None = None
        self._cursor_page: CursorPage | None = None
        self._autostart_page: AutostartPage | None = None
        self._env_vars_page: EnvVarsPage | None = None
        self._window_rules_page: WindowRulesPage | None = None
        self._layer_rules_page: LayerRulesPage | None = None
        self._layouts_page: LayoutsPage | None = None
        self._profiles_page: ProfilesPage | None = None
        self._settings_page: SettingsPage | None = None
        self._pending_page: PendingChangesPage | None = None
        self._pre_search_page_id: str | None = None
        self._search_results: list | None = None
        # Populated at the end of _build_ui() once section pages exist;
        # initialized empty so has_dirty() is safe during initial builds.
        self._section_pages: list[SectionPage] = []

        self._load_css()
        self._build_ui()
        self._register_state()
        self._refresh_all_modified_indicators()

    def _init_settings(self):
        """Load GSettings for app preferences (auto-save, etc.)."""
        schema_dir = bundled_data_dir()
        self._recompile_schemas_if_stale(schema_dir)
        schema_source = Gio.SettingsSchemaSource.new_from_directory(
            str(schema_dir),
            Gio.SettingsSchemaSource.get_default(),
            False,
        )
        schema_obj = schema_source.lookup(SETTINGS_SCHEMA_ID, False)
        if schema_obj:
            self._settings = Gio.Settings.new_full(schema_obj, None, None)
        else:
            self._settings = None

    @staticmethod
    def _recompile_schemas_if_stale(schema_dir: Path):
        """Recompile GSettings schemas if the compiled file is stale or missing."""
        xml_files = list(schema_dir.glob("*.gschema.xml"))
        if not xml_files:
            return
        compiled = schema_dir / "gschemas.compiled"
        latest_xml_mtime = max(xml.stat().st_mtime for xml in xml_files)
        if not compiled.exists() or compiled.stat().st_mtime < latest_xml_mtime:
            subprocess.run(
                ["glib-compile-schemas", str(schema_dir)],
                check=False,
            )

    def _apply_saved_config_path(self):
        """Apply the config-path setting from GSettings on startup."""
        if not self._settings:
            return
        path = self._settings.get_string("config-path")
        if not path:
            return
        # User may have switched Hyprland's config language out-of-band
        # since the path was stored — re-align the suffix (converting
        # file content if needed) so we don't silently write to a file
        # the live compositor never loads.
        repointed = config.ensure_managed_path_matches_mode(path)
        if repointed is not None:
            self._settings.set_string("config-path", repointed)
            path = repointed
        config.set_managed_path(Path(path))

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
    def saved_sections(self) -> dict[str, list[str]]:
        """The keyword sections parsed from the managed config on disk.

        Delegates to :func:`config.read_cached` so pages and the save flow
        share one parse instead of re-reading per call. The cache is
        invalidated whenever hyprmod writes the managed file, so reads
        always reflect on-disk state without explicit refresh.
        """
        return config.read_cached()[1]

    @property
    def saved_values(self) -> dict[str, str]:
        """The option ``key = value`` assignments parsed from the managed config."""
        return config.read_cached()[0]

    @property
    def saved_rules(self) -> list[Rule]:
        """Structured window/layer rules parsed from the managed config."""
        return config.read_cached()[2]

    @property
    def option_rows(self) -> dict[str, OptionRow]:
        """Read-only view of option-key → row mapping for cross-page navigation."""
        return self._option_rows

    @property
    def section_pages(self) -> list[SectionPage]:
        """The section pages whose dirty/save/discard the window orchestrates."""
        return self._section_pages

    @property
    def options_flat(self) -> dict[str, dict]:
        """Flattened option catalog keyed by dotted option name."""
        return self._options_flat

    def group_for_option(self, key: str) -> str | None:
        """Return the sidebar group id that contains *key* (or ``None``)."""
        return self._key_to_group.get(key)

    @property
    def config_path(self) -> str:
        return str(config.managed_path())

    @config_path.setter
    def config_path(self, value: str):
        default = str(config.default_managed_path())
        path = None if value == default else Path(value)
        config.set_managed_path(path)
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

        # Report-a-bug action: opens a prefilled GitHub issue in the browser.
        report_action = Gio.SimpleAction.new("report-bug", None)
        report_action.connect("activate", self._on_report_bug)
        self.add_action(report_action)

        # Lua migration: owns its banner, action, dialog, completion flow.
        self._lua_migration = LuaMigrationController(
            self,
            self._settings,
            show_toast=self.show_toast,
            get_hyprland_version=lambda: self.hypr.version,
        )
        self._lua_migration.install_action(self)

        # Deprecation assistant: scans the managed file + the user's main
        # hyprland.conf for fixable deprecated syntax and offers a guided
        # apply with backup. The version getter lets the scan ignore rules
        # whose ``version_deprecated`` is newer than the running Hyprland —
        # those deprecations haven't taken effect for this user yet.
        self._deprecations = DeprecationController(
            self,
            self._settings,
            show_toast=self.show_toast,
            get_hyprland_version=lambda: self.hypr.version,
        )
        self._deprecations.install_action(self)

        # Hyprland status banner
        self._hyprland_banner = Adw.Banner(
            title="Hyprland not detected — changes will be saved to config files "
            "but not applied live"
        )
        self._hyprland_banner.set_revealed(not self._hyprland_available)
        self._main_box.append(self._hyprland_banner)

        # Lua-migration offer — shown when the running Hyprland is 0.55+
        # but the user still has a hyprland.conf entrypoint. Custom widget
        # rather than Adw.Banner because we need two actions (migrate +
        # don't-show-again), and Adw.Banner only allows one button.
        self._main_box.append(self._lua_migration.banner)
        # Deprecation banner — visible when a scan finds fixable rules.
        self._main_box.append(self._deprecations.banner)

        # Navigation split view
        self._split_view = Adw.NavigationSplitView()
        self._split_view.set_vexpand(True)
        self._main_box.append(self._split_view)

        self._sidebar = Sidebar(
            on_page_selected=self._on_sidebar_selected,
            on_search_changed=self._on_search_changed,
            on_search_activate=self._on_search_activate,
            on_search_dismissed=self._on_search_dismissed,
        )
        self._split_view.set_sidebar(self._sidebar.nav_page)

        # Pending-changes chip lives in every page header (except the Pending
        # Changes page itself); the group keeps every chip's count in sync.
        self._pending_chips = PendingChipGroup(
            on_click=self._show_pending,
        )

        self._search_page_builder = SearchPage(self._schema)

        self._build_content_pane()
        groups, groups_by_id = self._build_pages()
        self._sidebar.populate(groups_by_id)
        self._build_search_page()

        # Cache the list of section pages (animations, monitors, binds) — stable after build
        self._section_pages = [
            p
            for p in (
                self._animations_page,
                self._monitors_page,
                self._workspaces_page,
                self._binds_page,
                self._cursor_page,
                self._autostart_page,
                self._env_vars_page,
                self._window_rules_page,
                self._layer_rules_page,
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

        # SectionPage subclasses that follow the standard
        # ``(window, on_dirty_changed, push_undo, saved_sections)``
        # constructor. ``slug`` is the page-stack key, ``title`` shows in
        # the header.
        section_page_specs: list[tuple[type, str, str, str]] = [
            (BindsPage, "_binds_page", "binds", "Keybinds"),
            (MonitorsPage, "_monitors_page", "monitors", "Monitors"),
            (WorkspacesPage, "_workspaces_page", "workspaces", "Workspaces"),
            (AutostartPage, "_autostart_page", "autostart", "Autostart"),
            (EnvVarsPage, "_env_vars_page", "env_vars", "Env Variables"),
            (WindowRulesPage, "_window_rules_page", "window_rules", "Window Rules"),
            (LayerRulesPage, "_layer_rules_page", "layer_rules", "Layer Rules"),
        ]
        for cls, attr, slug, title in section_page_specs:
            page = cls(
                self,
                on_dirty_changed=self._on_section_dirty,
                push_undo=self._undo.push,
                saved_sections=self.saved_sections,
            )
            setattr(self, attr, page)
            self._page_stack.add_named(page.build(header=self._make_page_header(title)), slug)
            self._page_titles[slug] = title
            if cls is MonitorsPage:
                self._search_page_builder.add_entries(page.get_search_entries())

        self._search_page_builder.add_entries(CursorPage.get_search_entries())

        # Standalone pages (no dirty/undo wiring; built from ``self`` only).
        # ``LayoutsPage`` is schema-driven but doesn't take the section-page
        # constructor — it embeds Dwindle/Master/Scrolling option groups
        # behind a ViewSwitcher, registering their option rows via
        # ``build_schema_group_widgets`` during ``build()``.
        standalone_page_specs: list[tuple[type, str, str, str]] = [
            (LayoutsPage, "_layouts_page", "layouts", "Layouts"),
            (ProfilesPage, "_profiles_page", "profiles", "Profiles"),
            (PendingChangesPage, "_pending_page", "pending", "Pending Changes"),
            (SettingsPage, "_settings_page", "settings", "Settings"),
        ]
        for cls, attr, slug, title in standalone_page_specs:
            page = cls(self)
            setattr(self, attr, page)
            # The Pending Changes page header omits the pending chip — it
            # would just be a no-op shortcut to the page the user is on.
            with_chip = cls is not PendingChangesPage
            header = self._make_page_header(title, with_pending_chip=with_chip)
            self._page_stack.add_named(page.build(header=header), slug)
            self._page_titles[slug] = title

        return groups, groups_by_id

    def _build_search_page(self):
        """Build the search results page in the content stack."""
        toolbar, _, self._search_content_box, _ = make_page_layout(
            header=self._make_page_header("Search Results")
        )
        self._page_stack.add_named(toolbar, "search")
        self._page_titles["search"] = "Search Results"

    def _make_page_header(self, title: str, *, with_pending_chip: bool = True) -> Adw.HeaderBar:
        """Create a content page header with menu button.

        When *with_pending_chip* is true (the default), a fresh
        :class:`PendingChip` from ``self._pending_chips`` is added before
        the menu button. The Pending Changes page itself opts out — the
        chip would just navigate back to the same page.
        """
        header = Adw.HeaderBar()
        header.set_title_widget(Adw.WindowTitle(title=title))

        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("menu-symbolic")

        menu = Gio.Menu()
        prefs_section = Gio.Menu()
        prefs_section.append("Auto-save", "win.auto-save")
        menu.append_section(None, prefs_section)

        tools_section = Gio.Menu()
        # ``hidden-when="action-disabled"`` lets GTK hide the item
        # entirely (not just grey it out) when the controller disables
        # the action on pre-0.55 Hyprland or once the user is on Lua.
        migrate_item = Gio.MenuItem.new("Migrate to Lua…", f"win.{LUA_MIGRATION_ACTION}")
        migrate_item.set_attribute_value("hidden-when", GLib.Variant.new_string("action-disabled"))
        tools_section.append_item(migrate_item)
        deprecations_item = Gio.MenuItem.new(
            "Review deprecated syntax…", f"win.{DEPRECATIONS_ACTION}"
        )
        deprecations_item.set_attribute_value(
            "hidden-when", GLib.Variant.new_string("action-disabled")
        )
        tools_section.append_item(deprecations_item)
        menu.append_section(None, tools_section)

        help_section = Gio.Menu()
        help_section.append("Keyboard Shortcuts", "win.show-help-overlay")
        help_section.append("Report a bug", "win.report-bug")
        help_section.append("About HyprMod", "win.show-about")
        menu.append_section(None, help_section)

        menu_button.set_menu_model(menu)
        header.pack_end(menu_button)

        if with_pending_chip:
            # ``pack_end`` stacks right-to-left, so the chip appears to the
            # left of the menu button — like a status indicator next to the
            # primary action.
            header.pack_end(self._pending_chips.new_chip())

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
                saved_sections=self.saved_sections,
            )
            content_box.append(self._animations_page.build_curve_editor_widget())

        if is_cursor:
            self._cursor_page = CursorPage(
                self,
                on_dirty_changed=self._on_section_dirty,
                push_undo=self._undo.push,
                saved_sections=self.saved_sections,
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
                # Companion keys are driven by another row (e.g. kb_variant by
                # the keyboard-layouts row); they stay tracked but render no row.
                if option.get("managed_by"):
                    continue
                value = option.get("default")
                opt_row = create_option_row(
                    option,
                    value,
                    on_change=self._on_option_changed,
                    on_reset=self._on_option_reset,
                    on_discard=self.discard_option,
                )
                if opt_row:
                    self._option_rows[option["key"]] = opt_row
                    self._row_owner_group[option["key"]] = pref_group
                    pref_group.add(opt_row.row)
                    parent = option.get("depends_on")
                    if parent:
                        self._dependents.setdefault(parent, []).append(option["key"])
                    companion = option.get("companion_key")
                    if companion and isinstance(opt_row, KeyboardLayoutsOptionRow):
                        # The row owns a second key for value/state sync; bind
                        # state so it can read both keys' live + managed values,
                        # and apply both as one atomic undo step.
                        self._option_rows[companion] = opt_row
                        opt_row.bind_state(self.app_state, self._on_paired_option_changed)

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
        pref_groups = self._build_section_widgets(group)
        # _register_state only runs once at startup; rebuilds (e.g. monitor
        # refresh) need to re-push live values and the managed indicator.
        self._sync_group_widgets_to_state(group, pref_groups)
        return pref_groups

    def _sync_group_widgets_to_state(
        self, group: dict, pref_groups: list[Adw.PreferencesGroup]
    ) -> None:
        groups_with_visible: set[Adw.PreferencesGroup] = set()
        any_state = False
        for section in group.get("sections", []):
            for option in section.get("options", []):
                key = option["key"]
                opt_row = self._option_rows.get(key)
                state = self.app_state.get(key)
                if opt_row is None or state is None:
                    continue
                any_state = True
                if not state.available:
                    opt_row.row.set_visible(False)
                    continue
                owner = self._row_owner_group.get(key)
                if owner is not None:
                    groups_with_visible.add(owner)
                if state.live_value is not None:
                    opt_row.set_value_silent(state.live_value)
                opt_row.update_modified_state(state.managed, state.is_dirty, state.saved_managed)
        if not any_state:
            return
        for pref_group in pref_groups:
            if pref_group not in groups_with_visible:
                pref_group.set_visible(False)

    def _register_state(self):
        options_flat = self._options_flat
        saved_values = self.saved_values
        for key, option in options_flat.items():
            saved = saved_values.get(key)
            if saved is not None:
                saved = coerce_config_value(saved, option.get("type", ""))
            # Compute display digits for float options so AppState can
            # normalize values to widget precision on ingress.
            digits = None
            if option.get("type") == "float":
                digits = digits_for_step(option.get("step", 0.01))
            self.app_state.register(key, option.get("default"), saved, digits=digits)

        # Hide rows for options the running Hyprland doesn't recognise —
        # both removed-in-this-version options and not-yet-introduced ones.
        # We used to grey them out with a "not available" tooltip, but a
        # disabled row is noisier than just dropping it: cross-version
        # support is built from version-paired entries in ``options.json``
        # (e.g. ``misc:vfr`` and ``debug:vfr`` both labelled "Variable
        # frame rate") and only the right one for this Hyprland should
        # render.
        groups_with_visible: set[Adw.PreferencesGroup] = set()
        for key, opt_row in self._option_rows.items():
            state = self.app_state.get(key)
            owner = self._row_owner_group.get(key)
            if state and not state.available:
                opt_row.row.set_visible(False)
            elif owner is not None:
                groups_with_visible.add(owner)
        # A group whose every row was hidden becomes a stray title with no
        # content. Hide the whole group so the page doesn't show an empty
        # "Frame rate" / "Glow" / ... section header.
        for group in set(self._row_owner_group.values()):
            if group not in groups_with_visible:
                group.set_visible(False)

        # Push AppState's authoritative values to widgets (AppState normalizes
        # floats and hex strings, so the widget must show the same value).
        for key, opt_row in self._option_rows.items():
            state = self.app_state.get(key)
            if state and state.live_value is not None:
                opt_row.set_value_silent(state.live_value)

        self.app_state.on_change(self._on_state_changed)

        # Set initial visibility of animation details based on animations:enabled
        if self._anim_details_box is not None:
            state = self.app_state.get(ANIMATIONS_ENABLED)
            self._anim_details_box.set_visible(bool(state and state.live_value))

    def _notify_ui_change(self):
        """Update banner and sidebar badges after an option change."""
        self._update_banner()
        self._update_sidebar_badges()

    def _schedule_pending_refresh(self):
        """Coalesce a Pending Changes page rebuild if the page exists."""
        if self._pending_page is not None:
            self._pending_page.schedule_refresh()

    def _refresh_all_modified_indicators(self):
        for key, opt_row in self._option_rows.items():
            state = self.app_state.get(key)
            if state:
                opt_row.update_modified_state(state.managed, state.is_dirty, state.saved_managed)
        self._refresh_all_dependents()
        self._update_sidebar_badges()

    def _update_sidebar_badges(self):
        """Update pending-change count badges on sidebar rows."""
        if self.auto_save:
            # Dirty state is just the 800ms debounce window with auto-save
            # on; surfacing it would flicker the chip and badges on every
            # change. Mirrors the dirty banner's gate in _update_banner.
            self._sidebar.update_badges({})
            self._pending_chips.set_count(0)
            return

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
        if self._workspaces_page and self._workspaces_page.is_dirty():
            counts["workspaces"] += self._workspaces_page.pending_change_count()
        if self._cursor_page and self._cursor_page.is_dirty():
            counts["cursor"] += 1
        if self._autostart_page and self._autostart_page.is_dirty():
            counts["autostart"] += self._autostart_page.pending_change_count()
        if self._env_vars_page and self._env_vars_page.is_dirty():
            counts["env_vars"] += self._env_vars_page.pending_change_count()
        if self._window_rules_page and self._window_rules_page.is_dirty():
            counts["window_rules"] += self._window_rules_page.pending_change_count()
        if self._layer_rules_page and self._layer_rules_page.is_dirty():
            counts["layer_rules"] += self._layer_rules_page.pending_change_count()

        # The pending-changes chip totals everything else
        counts["pending"] = sum(counts.values())

        self._sidebar.update_badges(counts)
        self._pending_chips.set_count(counts["pending"])

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

    def _on_report_bug(self, *_args):
        """Open a prefilled GitHub issue in the browser (Help → Report a bug)."""
        self._open_bug_report()

    # -- Search --

    def _on_show_search(self, *_args):
        """Focus the always-visible search entry (Ctrl+F)."""
        self._sidebar.focus_search()

    def _on_hide_search(self, *_args):
        """Clear search and restore the previously visible page (Escape)."""
        self._sidebar.clear_search()

    def _on_search_dismissed(self):
        """Restore the previous page when search is cleared.

        Invoked synchronously by ``Sidebar.clear_search()`` so the page
        switch doesn't wait for the 150 ms ``search-changed`` debounce.
        """
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

    def _on_search_result_activate(self, group_id: str, option_key: str):
        """Navigate to the group containing the selected search result.

        ``group_id`` already accounts for ``parent_page`` redirects (see
        ``SearchPage._index_options``), so hidden schema groups like
        ``monitor_globals`` or ``dwindle`` arrive here as ``monitors`` /
        ``layouts`` — no extra mapping needed.
        """
        # Clear the entry text and bypass the page-restore in
        # ``_on_search_dismissed`` — the navigate() call below sends the
        # user where they actually want to go.
        self._pre_search_page_id = None
        self._sidebar.clear_search()
        self.navigate(group_id, option_key=option_key)

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
            if gid == "pending" and self._pending_page is not None:
                # Catch up on any changes that happened while the page wasn't visible
                self._pending_page.refresh()
            self._page_stack.set_visible_child_name(gid)
            self._content_nav.set_title(self._page_titles[gid])

    def _show_pending(self):
        # Pending Changes is a non-sidebar page; clearing the sidebar
        # selection avoids the previous category's row staying highlighted
        # while the content pane shows a different page. Mirrors how the
        # search flow deselects on entry (see _on_search_changed).
        self.show_page("pending")
        self._sidebar.deselect_all()

    def navigate(self, group_id: str, *, option_key: str | None = None) -> None:
        """Switch to *group_id* and reflect it in the sidebar selection.

        ``show_page`` only swaps the visible content; the sidebar's selected
        row stays where it was (which looks broken when the navigation came
        from a non-sidebar source like search results or pending changes).
        Routing through one method keeps the two in sync.

        When *option_key* is provided and the destination hosts a sub-view
        (currently just the Layouts page's ViewSwitcher), the corresponding
        sub-tab is selected before the caller focuses the option row —
        otherwise the row lives in a hidden child and ``grab_focus`` is a
        no-op.
        """
        self.show_page(group_id)
        self._sidebar.select_row(group_id)

        if group_id == "layouts" and option_key and self._layouts_page is not None:
            self._layouts_page.focus_layout_for_option(option_key)

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
            self.show_bug_toast(f"Failed to set {key} — {e}", detail=str(e), timeout=5)
            return
        if entry is None and opt_row:
            opt_row.flash_error()
            if state:
                opt_row.set_value_silent(state.live_value)
        elif entry is not None:
            self._undo.push(entry)
            if self.auto_save:
                self._schedule_auto_save()

    def _on_paired_option_changed(self, changes: list[tuple[str, object]]):
        """Apply several keys as one atomic undo step (keyboard layouts own two).

        Used where a single user action writes more than one config key, so
        undo/redo moves them together instead of one key per step.
        """
        entries = []
        for key, value in changes:
            state = self.app_state.get(key)
            if state and value == state.live_value:
                continue
            opt_row = self._option_rows.get(key)
            try:
                entry = self.app_state.set_live(key, value)
            except HyprlandError as e:
                if opt_row:
                    opt_row.flash_error()
                self.show_bug_toast(f"Failed to set {key} — {e}", detail=str(e), timeout=5)
                continue
            if entry is not None:
                entries.append(entry)
        if entries:
            self._undo.push(PairedOptionChange(entries))
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

        fallback = self.hypr.get_fallback_value(key, config.managed_path())
        self.app_state.reset_to_value(key, fallback)
        self._sync_option_row(key, flash=True)
        self._notify_ui_change()
        if self.auto_save:
            self._schedule_auto_save()

    def discard_option(self, key: str):
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
        self._schedule_pending_refresh()

        if key == ANIMATIONS_ENABLED and self._anim_details_box is not None:
            state = self.app_state.get(key)
            self._anim_details_box.set_visible(bool(state and state.live_value))

    def _on_section_dirty(self):
        """Called when any section (animations, binds, monitors) changes."""
        self._update_banner()
        self._update_sidebar_badges()
        self._schedule_pending_refresh()
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
        if entry.apply(self, undo=undo):
            (self._undo.confirm_undo if undo else self._undo.confirm_redo)(entry)

    # -- Save with animation --

    def collect_save_sections(self) -> config.ConfigSections:
        """Collect sections to save: dirty sections + previously saved sections.

        A section is only included if it was already in hyprmod's managed
        config (HyprMod owns it) or the user changed it in this session.
        Reads through :func:`config.read_cached` — the on-disk file is
        unchanged between the last invalidation and the user clicking Save.
        """
        saved_sections = self.saved_sections
        sections = config.ConfigSections()

        def emit_if[T: SectionPage](
            page: T | None,
            has_managed: Callable[[T], bool],
            get_lines: Callable[[T], list[str]],
        ) -> list[str] | None:
            """Return ``page.get_lines()`` when the section is owned or dirty."""
            if page is None:
                return None
            if has_managed(page) or page.is_dirty():
                return get_lines(page)
            return None

        sections.binds = emit_if(
            self._binds_page,
            lambda _p: bool(config.collect_bind_section(saved_sections)),
            lambda p: p.get_bind_lines(),
        )
        sections.monitors = emit_if(
            self._monitors_page,
            lambda _p: bool(config.collect_section(saved_sections, config.KEYWORD_MONITOR)),
            lambda p: p.get_monitor_lines(),
        )
        sections.workspaces = emit_if(
            self._workspaces_page,
            lambda _p: WorkspacesPage.has_managed_section(saved_sections),
            lambda p: p.get_workspace_lines(),
        )

        # Animations: bezier extraction is bespoke (curves used by emitted
        # animations need their definitions emitted alongside), so this one
        # stays inline.
        if self._animations_page is not None:
            anim_dirty = self._animations_page.is_dirty()
            existing_anims = config.collect_section(saved_sections, config.KEYWORD_ANIMATION)
            if anim_dirty or existing_anims:
                sections.animations, used_curves = self._animations_page.get_animation_lines()
                if used_curves:
                    sections.beziers = get_curve_store().get_curve_definitions(used_curves)

        # Cursor and env-vars pages both contribute to ``sections.env``.
        # Cursor owns the four ``XCURSOR_*`` / ``HYPRCURSOR_*`` names;
        # env-vars owns everything else. Cursor lines come first by
        # convention (theme + size are session-defining; later vars may
        # reference them indirectly).
        cursor_env = (
            emit_if(
                self._cursor_page,
                lambda p: p.has_managed_env(saved_sections),
                lambda p: p.get_env_lines(),
            )
            or []
        )
        general_env = (
            emit_if(
                self._env_vars_page,
                lambda _p: EnvVarsPage.has_managed_section(saved_sections),
                lambda p: p.get_env_lines(),
            )
            or []
        )
        if cursor_env or general_env:
            sections.env = [*cursor_env, *general_env]

        sections.exec_ = emit_if(
            self._autostart_page,
            lambda _p: AutostartPage.has_managed_section(saved_sections),
            lambda p: p.get_exec_lines(),
        )
        # Rule pages live in the structured ``saved_rules`` list, not
        # ``saved_sections``, so check there for ownership. Legacy v1
        # layer rules that didn't normalise into Rule nodes still appear
        # in ``saved_sections`` so the fallback covers them.
        saved_rules = self.saved_rules
        has_owned_window_rules = any(r.kind == "windowrule" for r in saved_rules) or bool(
            saved_sections.get(config.KEYWORD_WINDOWRULE)
        )
        has_owned_layer_rules = any(r.kind == "layerrule" for r in saved_rules) or bool(
            saved_sections.get(config.KEYWORD_LAYERRULE)
        )
        sections.window_rules = emit_if(
            self._window_rules_page,
            lambda _p: has_owned_window_rules,
            lambda p: p.get_window_rule_lines(),
        )
        sections.layer_rules = emit_if(
            self._layer_rules_page,
            lambda _p: has_owned_layer_rules,
            lambda p: p.get_layer_rule_lines(),
        )

        return sections

    def _perform_save(self, *, update_active_profile: bool = True):
        # ``write_all`` invalidates ``config.read_cached`` internally, so any
        # subsequent ``saved_sections`` access reflects what we just wrote.
        config.write_all(
            self.app_state.get_all_live_values(),
            self.collect_save_sections(),
            hyprland_version=self.hypr.version,
        )
        self.app_state.mark_saved()
        self.hypr.clear_pending()
        for section in self._section_pages:
            section.mark_saved()
        self._undo.clear()
        self._refresh_all_modified_indicators()
        self._schedule_pending_refresh()
        # Keep the active profile in sync with disk on every save. Callers
        # that want the saved state to intentionally diverge from the
        # active profile pass ``update_active_profile=False``.
        if update_active_profile:
            active_id = profiles.get_active_id()
            if active_id is not None:
                profiles.update(active_id)
        if self._profiles_page is not None:
            self._profiles_page.rebuild()

    def save(self, *, update_active_profile: bool = True):
        """Public save API — performs save and shows banner animation."""
        self._perform_save(update_active_profile=update_active_profile)
        self._banner.show_saved()

    def reload_after_profile(self):
        """Refresh all state after profile activation.

        ``profiles.activate_meta`` already invalidated the managed-config
        cache, so the ``saved_sections`` accesses below see the new file.
        Schema state is refreshed first, then per-page reloads, then
        Hyprland's live state is re-read.
        """
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

        # Section pages that take a sections dict — read the freshly-invalidated
        # cache once and hand it down so all pages see the same snapshot.
        sections = self.saved_sections
        if self._cursor_page is not None:
            self._cursor_page.reload_from_saved(sections)

        if self._autostart_page is not None:
            self._autostart_page.reload_from_saved(sections)

        if self._env_vars_page is not None:
            self._env_vars_page.reload_from_saved(sections)

        if self._window_rules_page is not None:
            self._window_rules_page.reload_from_saved(sections)

        if self._layer_rules_page is not None:
            self._layer_rules_page.reload_from_saved(sections)

        if self._workspaces_page is not None:
            self._workspaces_page.reload_from_saved(sections)

        self._undo.clear()
        self._banner.hide()

    def _update_managed_flags(self):
        """Update managed flags and saved values from the current saved config."""
        options_flat = self._options_flat
        saved_values = self.saved_values
        for key, state in self.app_state.options.items():
            saved = saved_values.get(key)
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

    def show_toast(self, message: str, timeout: int = 2, *, copy: bool = False):
        """Show a transient toast. With *copy*, add a Copy button for *message*."""
        toast = Adw.Toast(title=message, timeout=timeout)
        if copy:
            toast.set_button_label("Copy")
            toast.connect("button-clicked", lambda _t: self.get_clipboard().set(message))
        self.add_toast(toast)

    def show_bug_toast(self, message: str, *, detail: str | None = None, timeout: int = 5) -> None:
        """Toast with a Report button for errors that likely indicate a hyprmod bug.

        Use at sites where the failure path is internal (caught ``except
        Exception``, an IPC roundtrip we initiated, a post-condition that
        the user can't influence) rather than user input that didn't
        validate. The Report button opens a prefilled GitHub issue with
        *message* above the environment block.

        *message* is shown in the toast and forms the issue body. *detail*,
        when given (typically ``str(exc)``), becomes the issue title tagged
        ``[Bug]``; without it the title falls back to *message*.
        """
        toast = Adw.Toast(title=message, timeout=timeout)
        toast.set_button_label("Report")
        toast.connect(
            "button-clicked",
            lambda _t: self._open_bug_report(title=detail or message, body_extra=message),
        )
        self.add_toast(toast)

    def _open_bug_report(self, *, title: str = "", body_extra: str = "") -> None:
        url = build_bug_report_url(
            title=title,
            body_extra=body_extra,
            running_hyprland_version=self.hypr.version,
        )
        Gtk.UriLauncher.new(url).launch(self, None, None)

    def _on_save(self, *_args):
        # Save now keeps the active profile in sync internally; no need
        # to branch on whether a profile is active.
        self.save()

    def _on_save_without_update_profile(self, *_args):
        """Save config but deactivate the profile (it no longer matches)."""
        self.save(update_active_profile=False)
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
        self._schedule_pending_refresh()

    # -- Auto-save --

    def set_auto_save(self, value: bool) -> None:
        """Update auto-save preference and keep the menu action in sync.

        Single entry point for the settings-row toggle and the menu-item
        action handler — both flow through here so the GSettings value,
        the ``win.auto-save`` action state, and the settings-page row
        stay in lock-step.
        """
        if self.auto_save == value:
            return
        self.auto_save = value
        self._auto_save_action.set_state(GLib.Variant.new_boolean(value))
        if self._settings_page is not None:
            self._settings_page.sync_auto_save(value)
        # If just enabled and there are unsaved changes, save immediately.
        if value and self.has_dirty():
            self.save()
        # Reflect the toggle in the chip/badges/banner. Enabling clears them
        # (gated on self.auto_save in the update helpers); disabling reveals
        # any in-flight dirty state — e.g. changes from a pending debounce.
        self._notify_ui_change()

    def _on_toggle_auto_save(self, action, _param):
        self.set_auto_save(not action.get_state().get_boolean())

    def _schedule_auto_save(self):
        """Debounced auto-save: wait 800ms after last change before writing."""
        if self._monitors_page is not None and self._monitors_page.is_confirm_pending():
            # The monitors confirm/revert banner is the safety net for
            # potentially-unviewable monitor configs. Writing now would
            # persist an unconfirmed config and mark_saved would cancel
            # the auto-revert. Cancel (not just skip) so a timer scheduled
            # by an earlier non-monitor change can't fire mid-confirm.
            # When the user keeps the change, _on_confirmed → _notify_dirty
            # → _on_section_dirty re-enters this method; a revert clears
            # dirty state, so there's nothing to save.
            self._auto_save_timer.cancel()
            return
        self._auto_save_timer.schedule(800, self._auto_save_fire)

    def _auto_save_fire(self):
        self._perform_save()
        self._banner.hide()
