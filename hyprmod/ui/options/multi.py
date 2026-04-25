"""Multi-select option row: comma-separated values from a dynamic source."""

from gi.repository import Adw, Gtk

from hyprmod.ui.options.base import OptionRow
from hyprmod.ui.options.combo import _wrapping_label_setup
from hyprmod.ui.sources import MissingDependencyError, get_source_values

_MULTI_SEP = "\x1f"  # separator between group and label in model strings


class MultiSourceOptionRow(OptionRow):
    """Row for comma-separated multi-value options with a searchable add dropdown."""

    def __init__(self, option, value, on_change, on_reset, on_discard=None):
        self._source_name = option["source"]
        self._selected: list[str] = []
        self._selected_rows: list[Adw.ActionRow] = []
        self._unavailable = False

        try:
            source_values = get_source_values(self._source_name)
        except MissingDependencyError as exc:
            self._unavailable = True
            self._all_items = {}
            row = Adw.ExpanderRow(
                title=option.get("label", option["key"]),
                subtitle=exc.message,
            )
            row.set_sensitive(False)
            super().__init__(row, option, on_change, on_reset, on_discard)
            return

        self._all_items = {v["id"]: v for v in source_values}

        # Main expander row
        row = Adw.ExpanderRow(
            title=option.get("label", option["key"]),
            subtitle=option.get("description", ""),
        )

        super().__init__(row, option, on_change, on_reset, on_discard)

        # Picker row with searchable combo inside the expander
        self._picker_row = Adw.ActionRow(title="Add option…")
        self._picker_row.add_css_class("option-default")

        self._combo = Gtk.DropDown()
        self._combo.set_enable_search(True)
        self._combo.set_search_match_mode(Gtk.StringFilterMatchMode.SUBSTRING)
        self._combo.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
        self._combo.set_valign(Gtk.Align.CENTER)

        # Factory for the selected item display (strip encoded group prefix)
        selected_factory = Gtk.SignalListItemFactory()
        selected_factory.connect("setup", _wrapping_label_setup)
        selected_factory.connect("bind", self._on_selected_bind)
        self._combo.set_factory(selected_factory)

        # List item factory (just the label, no group — headers handle that)
        list_factory = Gtk.SignalListItemFactory()
        list_factory.connect("setup", self._on_list_setup)
        list_factory.connect("bind", self._on_list_bind)
        self._combo.set_list_factory(list_factory)

        # Header factory for section separators
        header_factory = Gtk.SignalListItemFactory()
        header_factory.connect("setup", self._on_header_setup)
        header_factory.connect("bind", self._on_header_bind)
        self._combo.set_header_factory(header_factory)

        self._combo.add_css_class("wide-dropdown")

        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.add_css_class("flat")
        add_btn.set_tooltip_text("Add selected option")
        add_btn.connect("clicked", self._on_add_clicked)

        self._picker_row.add_suffix(self._combo)
        self._picker_row.add_suffix(add_btn)
        row.add_row(self._picker_row)

        # Parse initial value and populate
        current = str(value) if value else ""
        if current:
            for item_id in current.split(","):
                item_id = item_id.strip()
                if item_id and item_id in self._all_items:
                    self._selected.append(item_id)
        self._rebuild_selected_rows()
        self._rebuild_picker_model()

    @staticmethod
    def _section_sort(a, b, _user_data):
        """Section sorter: group items by the group prefix."""
        ga = a.get_string().split(_MULTI_SEP)[0]
        gb = b.get_string().split(_MULTI_SEP)[0]
        return (ga > gb) - (ga < gb)

    def _rebuild_picker_model(self):
        """Rebuild the dropdown model, excluding already-selected items.

        Each model string is encoded as "group SEP item_id SEP display_label".
        """
        selected_set = set(self._selected)
        model_strings = []
        for item_id, item in sorted(
            self._all_items.items(),
            key=lambda kv: (kv[1].get("group", ""), kv[1]["label"].casefold()),
        ):
            if item_id not in selected_set:
                group = item.get("group", "")
                label = f"{item['label']} ({item_id})"
                model_strings.append(f"{group}{_MULTI_SEP}{item_id}{_MULTI_SEP}{label}")
        string_list = Gtk.StringList.new(model_strings)
        section_model = Gtk.SortListModel(model=string_list)
        section_model.set_section_sorter(Gtk.CustomSorter.new(self._section_sort))
        self._combo.set_model(section_model)
        if model_strings:
            self._combo.set_selected(0)

    def _rebuild_selected_rows(self):
        """Rebuild the child rows showing selected options."""
        for r in self._selected_rows:
            self.row.remove(r)
        self._selected_rows = []

        for item_id in self._selected:
            item = self._all_items.get(item_id, {})
            label = item.get("label", item_id)
            group = item.get("group", "")

            child_row = Adw.ActionRow(title=label)
            if group:
                child_row.set_subtitle(group)

            code_label = Gtk.Label(label=item_id)
            code_label.add_css_class("dim-label")
            code_label.add_css_class("caption")
            child_row.add_suffix(code_label)

            remove_btn = Gtk.Button(icon_name="edit-clear-symbolic")
            remove_btn.set_valign(Gtk.Align.CENTER)
            remove_btn.add_css_class("flat")
            remove_btn.set_tooltip_text("Remove")
            remove_btn.connect("clicked", self._on_remove_clicked, item_id)
            child_row.add_suffix(remove_btn)

            self.row.add_row(child_row)  # type: ignore[attr-defined]
            self._selected_rows.append(child_row)

        self._update_subtitle()

    def _update_subtitle(self):
        """Update the expander subtitle with count of selected options."""
        if self._selected:
            self.row.set_subtitle(f"{len(self._selected)} option(s) selected")  # type: ignore[attr-defined]
        else:
            self.row.set_subtitle(self.option.get("description", ""))  # type: ignore[attr-defined]

    def _on_add_clicked(self, _button):
        idx = self._combo.get_selected()
        model = self._combo.get_model()
        if model is None or idx == Gtk.INVALID_LIST_POSITION or idx >= model.get_n_items():
            return
        item = model.get_item(idx)
        if item is None:
            return
        raw: str = item.get_string()  # type: ignore[attr-defined]
        parts = raw.split(_MULTI_SEP, 2)
        if len(parts) == 3:
            item_id = parts[1]
            self._selected.append(item_id)
            self._rebuild_selected_rows()
            self._rebuild_picker_model()
            self._emit_value()

    def _on_remove_clicked(self, _button, item_id):
        if item_id in self._selected:
            self._selected.remove(item_id)
            self._rebuild_selected_rows()
            self._rebuild_picker_model()
            self._emit_value()

    @staticmethod
    def _on_selected_bind(_factory, list_item):
        """Show just the display label for the selected item."""
        label = list_item.get_child()
        raw = list_item.get_item().get_string()
        parts = raw.split(_MULTI_SEP, 2)
        label.set_label(parts[2] if len(parts) == 3 else raw)

    _on_list_setup = staticmethod(_wrapping_label_setup)

    @staticmethod
    def _on_list_bind(_factory, list_item):
        label = list_item.get_child()
        raw = list_item.get_item().get_string()
        parts = raw.split(_MULTI_SEP, 2)
        label.set_label(parts[2] if len(parts) == 3 else raw)

    @staticmethod
    def _on_header_setup(_factory, list_header):
        label = Gtk.Label(xalign=0)
        label.add_css_class("heading")
        label.add_css_class("dim-label")
        label.set_margin_top(8)
        label.set_margin_bottom(4)
        label.set_margin_start(6)
        list_header.set_child(label)

    @staticmethod
    def _on_header_bind(_factory, list_header):
        label = list_header.get_child()
        raw = list_header.get_item().get_string()
        group = raw.split(_MULTI_SEP)[0]
        label.set_label(group)

    def _emit_value(self):
        self._emit_change(",".join(self._selected))

    def refresh_source(self, **kwargs):
        """Re-populate the available items from the source."""
        if self._unavailable:
            return
        source_values = get_source_values(self._source_name, **kwargs)
        self._all_items = {v["id"]: v for v in source_values}
        self._rebuild_selected_rows()
        self._rebuild_picker_model()

    def _set_widget_value(self, value):
        if self._unavailable:
            return
        self._selected.clear()
        val_str = str(value) if value else ""
        if val_str:
            for item_id in val_str.split(","):
                item_id = item_id.strip()
                if item_id and item_id in self._all_items:
                    self._selected.append(item_id)
        self._rebuild_selected_rows()
        self._rebuild_picker_model()
