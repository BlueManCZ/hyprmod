"""Plugin Settings page — manage custom plugin configurations.

Plugins define their own settings nested inside the `plugin { ... }` block.
This page provides a list editor to add, edit, and remove those settings.
"""

from html import escape as html_escape

from gi.repository import Adw, Gtk
from hyprland_socket import HyprlandError

from hyprmod.core import config, schema
from hyprmod.core.ownership import SavedList
from hyprmod.core.plugins import (
    ExternalPluginSetting,
    PluginSetting,
    load_external_plugins,
    parse_plugin_options,
)
from hyprmod.pages.section import SavedListSectionPage
from hyprmod.ui import make_page_layout, try_with_toast
from hyprmod.ui.empty_state import EmptyState
from hyprmod.ui.icons import PLUGINS_ICON
from hyprmod.ui.plugin_setting_edit_dialog import PluginSettingEditDialog
from hyprmod.ui.row_actions import RowActions


class PluginsPage(SavedListSectionPage[PluginSetting]):
    """List editor for `plugin { ... }` config entries."""

    _unit_singular = "setting"
    _unit_plural = "settings"
    _page_attr = "_plugins_page"
    _pending_category = "Plugin Settings"
    _pending_navigate_to = "plugins"
    _pending_icon = PLUGINS_ICON
    _group_title = "Plugin Settings"
    _group_add_tooltip = "Add a plugin setting"

    def __init__(
        self,
        window,
        on_dirty_changed=None,
        push_undo=None,
    ):
        super().__init__(window, on_dirty_changed, push_undo)
        self._content_box: Gtk.Box
        self._scrolled: Gtk.ScrolledWindow
        self._owned: SavedList[PluginSetting]
        self._overridden_external: set[str] = set()
        self._load()

    # ── Loading ──

    def _load(self, saved_sections: dict[str, list[str]] | None = None) -> None:
        del saved_sections
        saved_values = self._window.saved_values
        items = parse_plugin_options(saved_values)

        custom_items = []
        for e in items:
            full_key = f"plugin:{e.plugin_name}:{e.key}"
            if full_key not in self._window.options_flat:
                custom_items.append(e)

        self._owned = SavedList(custom_items, key=lambda e: f"{e.plugin_name}:{e.key}")
        self._external = load_external_plugins(config.user_entry_path(), config.managed_path())

    @property
    def custom_plugins(self) -> list[PluginSetting]:
        """Return the list of custom plugin settings managed by this page."""
        return list(self._owned)

    def reload_from_saved(self, saved_sections: dict[str, list[str]]) -> None:
        self._load(saved_sections)
        self._rebuild_list()

    # ── Build ──

    def build(self, header: Adw.HeaderBar | None = None) -> Adw.ToolbarView:
        page_header = header or Adw.HeaderBar()

        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.set_tooltip_text("Add plugin setting")
        add_btn.connect("clicked", lambda _b: self._on_add())
        page_header.pack_start(add_btn)

        toolbar_view, _, self._content_box, self._scrolled = make_page_layout(header=page_header)

        self._rebuild_list()
        return toolbar_view

    # ── List rendering ──

    def _pre_rebuild(self) -> None:
        self._overridden_external = set()
        owned_keys = {f"{e.plugin_name}:{e.key}" for e in self._owned}
        for ext in self._external:
            unprefixed_key = f"{ext.setting.plugin_name}:{ext.setting.key}"
            full_key = f"plugin:{unprefixed_key}"
            state = self._window.app_state.get(full_key)
            if unprefixed_key in owned_keys or (state is not None and state.managed):
                self._overridden_external.add(unprefixed_key)

    def _build_empty_state(self) -> EmptyState:
        return EmptyState(
            title="No Custom Plugins",
            description="Add arbitrary settings for unsupported plugins.",
            icon_name=PLUGINS_ICON,
        )

    def _build_managed_groups(self) -> list[Gtk.Widget]:
        groups = []
        supported = schema.load_plugin_schemas()
        any_not_loaded = False

        if supported:
            supported_group = Adw.PreferencesGroup(title="Supported Plugins")
            for schema_group in supported:
                plugin_keys = []
                main_toggle_key = None
                main_toggle_default = True

                for section in schema_group.get("sections", []):
                    for option in section.get("options", []):
                        k = option["key"]
                        plugin_keys.append(k)
                        if k.endswith(":enabled") and k.count(":") == 2:
                            main_toggle_key = k
                            main_toggle_default = option.get("default", True)

                is_enabled = main_toggle_default
                status_text = ""
                is_loaded = True
                if main_toggle_key:
                    state = self._window.app_state.get(main_toggle_key)
                    if state is None or not state.available:
                        status_text = "Not loaded"
                        is_loaded = False
                        any_not_loaded = True
                    else:
                        is_enabled = bool(state.live_value)

                if not status_text:
                    status_text = "Enabled" if is_enabled else "Disabled"

                def is_managed(k: str) -> bool:
                    state = self._window.app_state.get(k)
                    return state.managed if state else k in self._window.saved_values

                managed_count = sum(1 for k in plugin_keys if is_managed(k))
                if managed_count > 0:
                    plural = "s" if managed_count > 1 else ""
                    status_text += f" • {managed_count} custom setting{plural}"

                desc = schema_group.get("description")
                subtitle = f"{status_text}\n{desc}" if desc else status_text

                row = Adw.ActionRow(
                    title=schema_group["label"],
                    subtitle=subtitle,
                )
                row.set_subtitle_lines(0)

                if "icon" in schema_group:
                    icon = Gtk.Image.new_from_icon_name(schema_group["icon"])
                    row.add_prefix(icon)

                row.set_activatable(is_loaded)
                if is_loaded:
                    row.connect(
                        "activated",
                        lambda _r, g=schema_group: self._show_supported_plugin_dialog(g),
                    )
                supported_group.add(row)
            groups.append(supported_group)

        import shutil

        has_hyprpm = shutil.which("hyprpm") is not None

        def has_hyprpm_autostart() -> bool:
            autostart_page = getattr(self._window, "_autostart_page", None)
            if not autostart_page:
                return True
            return autostart_page.has_command("exec-once", "hyprpm reload")

        if any_not_loaded and has_hyprpm and not has_hyprpm_autostart():

            def on_add_autostart(_btn) -> None:
                autostart_page = getattr(self._window, "_autostart_page", None)
                if autostart_page:
                    autostart_page.add_command("exec-once", "hyprpm reload -n")
                    self._rebuild_list()

            from hyprmod.ui import make_inline_hint

            hint = make_inline_hint(
                "Plugins must be loaded to configure them. Add hyprpm reload to autostart?",
                button_label="Add to Autostart",
                button_callback=on_add_autostart,
            )
            groups.insert(0, hint)

        if len(self._owned) > 0:
            custom = self._build_managed_group("Custom Plugins", range(len(self._owned)))
            groups.append(custom)

        return groups

    def _show_supported_plugin_dialog(self, schema_group: dict):
        dialog = Adw.PreferencesDialog()
        dialog.set_title(f"{schema_group['label']} Settings")
        dialog.set_content_width(500)
        dialog.set_content_height(600)

        page = Adw.PreferencesPage(title="")
        page.set_icon_name(schema_group.get("icon", ""))

        for pref_group in self._window.build_schema_group_widgets(schema_group):
            page.add(pref_group)

        dialog.add(page)
        dialog.connect("closed", lambda _d: self._rebuild_list())
        dialog.present(self._window)

    def _deleted_row_summary(self, item: PluginSetting) -> tuple[str, str]:
        return f"{item.plugin_name}: {item.key}", item.value or "(empty)"

    # ── Pending-changes summarizers ──

    def _summarize_item(self, item: PluginSetting) -> tuple[str, str]:
        return f"{item.plugin_name}: {item.key}", item.value or "(empty)"

    def _summarize_modified(self, baseline: PluginSetting, item: PluginSetting) -> tuple[str, str]:
        if baseline.plugin_name != item.plugin_name or baseline.key != item.key:
            return (
                f"{item.plugin_name}: {item.key}",
                f"{baseline.plugin_name}:{baseline.key} → {item.plugin_name}:{item.key}",
            )
        return (
            f"{item.plugin_name}: {item.key}",
            f"{baseline.value or '(empty)'} → {item.value or '(empty)'}",
        )

    def _make_row(self, idx: int, item: PluginSetting) -> Adw.ActionRow:
        row = Adw.ActionRow(
            title=html_escape(f"{item.key} = {item.value}"),
            subtitle=html_escape(f"Plugin: {item.plugin_name}"),
        )
        row.set_title_lines(1)
        row.set_subtitle_lines(1)

        prefix = Gtk.Image.new_from_icon_name(PLUGINS_ICON)
        prefix.set_opacity(0.6)
        prefix.set_pixel_size(28)
        row.add_prefix(prefix)

        self._reorder.attach(row, idx)
        if idx < len(self._rows_by_idx):
            self._rows_by_idx[idx] = row

        is_dirty = self._owned.is_item_dirty(idx)
        is_saved = self._owned.get_baseline(idx) is not None

        actions = RowActions(
            row,
            on_discard=lambda i=idx: self._discard_at(i),
            on_reset=lambda i=idx: self._on_delete_at(i),
            reset_icon="user-trash-symbolic",
            reset_tooltip="Remove this setting",
        )
        row.add_suffix(actions.box)
        actions.update(is_managed=True, is_dirty=is_dirty, is_saved=is_saved)

        row.set_activatable(True)
        row.connect("activated", lambda _r, i=idx: self._on_edit_at(i))
        row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        return row

    def _build_external_hint(self) -> Gtk.Widget:
        from hyprmod.ui import make_inline_hint

        return make_inline_hint(
            "Settings below come from your hyprland.conf or its "
            "sourced files. Click the edit button to override them — "
            "your managed entry will take precedence on the next "
            "Hyprland session."
        )

    def _make_external_row(self, ext: ExternalPluginSetting) -> Adw.ActionRow:
        key = f"{ext.setting.plugin_name}:{ext.setting.key}"
        is_overridden = key in self._overridden_external
        subtitle = f"{ext.setting.value}  ·  line {ext.lineno}"

        row = Adw.ActionRow(
            title=html_escape(f"Plugin: {ext.setting.plugin_name} · {ext.setting.key}"),
            subtitle=html_escape(subtitle),
        )
        row.set_title_lines(1)
        row.set_subtitle_lines(1)
        row.add_css_class("option-default")
        row.set_opacity(0.65)
        row.set_tooltip_text(f"{ext.source_path}:{ext.lineno}")

        prefix = Gtk.Image.new_from_icon_name(PLUGINS_ICON)
        prefix.set_opacity(0.4)
        prefix.set_pixel_size(28)
        row.add_prefix(prefix)

        if is_overridden:
            badge = Gtk.Label(label="Overridden")
            badge.add_css_class("pending-badge")
            badge.add_css_class("pending-badge-modified")
            badge.set_valign(Gtk.Align.CENTER)
            row.add_suffix(badge)
            lock_icon = Gtk.Image.new_from_icon_name("changes-prevent-symbolic")
            lock_icon.set_opacity(0.4)
            lock_icon.set_valign(Gtk.Align.CENTER)
            row.add_suffix(lock_icon)
            return row

        override_btn = Gtk.Button(icon_name="document-edit-symbolic")
        override_btn.set_valign(Gtk.Align.CENTER)
        override_btn.add_css_class("flat")
        override_btn.set_tooltip_text("Override this setting")
        override_btn.connect("clicked", lambda _b, e=ext: self._on_override(e))
        row.add_suffix(override_btn)

        lock_icon = Gtk.Image.new_from_icon_name("changes-prevent-symbolic")
        lock_icon.set_opacity(0.4)
        lock_icon.set_valign(Gtk.Align.CENTER)
        row.add_suffix(lock_icon)
        return row

    def _on_override(self, ext: ExternalPluginSetting) -> None:
        PluginSettingEditDialog.present_singleton(
            self._window,
            entry=ext.setting,
            on_apply=self._commit_appended,
        )

    # ── Actions ──

    def _on_add(self) -> None:
        PluginSettingEditDialog.present_singleton(
            self._window,
            on_apply=self._commit_appended,
        )

    def _on_edit_at(self, idx: int) -> None:
        if idx < 0 or idx >= len(self._owned):
            return
        current = self._owned[idx]

        def on_apply(new_item: PluginSetting) -> None:
            if new_item != current:
                self._commit_replaced(idx, new_item)

        PluginSettingEditDialog.present_singleton(
            self._window,
            entry=current,
            on_apply=on_apply,
        )

    def _apply_live(self, key: str, value: str) -> None:
        try_with_toast(
            self._window.show_bug_toast,
            "Plugin setting failed",
            lambda: self._window.hypr.keyword(key, value),
            catch=HyprlandError,
        )

    def _commit_appended(self, item: PluginSetting) -> None:
        super()._commit_appended(item)
        self._apply_live(f"plugin:{item.plugin_name}:{item.key}", item.value or "")

    def _commit_replaced(self, idx: int, item: PluginSetting) -> None:
        super()._commit_replaced(idx, item)
        self._apply_live(f"plugin:{item.plugin_name}:{item.key}", item.value or "")

    def _on_delete_at(self, idx: int) -> None:
        super()._on_delete_at(idx)

    def _discard_at(self, idx: int) -> None:
        item = self._owned[idx]
        baseline = self._owned.get_baseline(idx)
        super()._discard_at(idx)
        if baseline:
            self._apply_live(f"plugin:{item.plugin_name}:{item.key}", baseline.value or "")

    def _on_restore_deleted(self, item: PluginSetting) -> None:
        super()._on_restore_deleted(item)
        self._apply_live(f"plugin:{item.plugin_name}:{item.key}", item.value or "")

    # ── Save Integration ──
