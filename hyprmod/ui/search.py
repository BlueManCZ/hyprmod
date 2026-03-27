"""Global option search — finds options across all schema groups."""

from html import escape as html_escape

from gi.repository import Adw, Gtk

from hyprmod.core import schema as schema_mod

MIN_QUERY_LENGTH = 2


class SearchResultRow(Adw.ActionRow):
    """A search result row showing option label, group, and key."""

    def __init__(self, option: dict, group_label: str, section_label: str):
        super().__init__(
            title=html_escape(option.get("label", option["key"])),
            subtitle=html_escape(f"{group_label} \u203a {section_label}"),
        )
        self.set_activatable(True)
        self.option_key = option["key"]
        self.group_id = option.get("_group_id", "")

        # Show the key as a dim suffix
        key_label = Gtk.Label(label=option["key"])
        key_label.add_css_class("dim-label")
        key_label.add_css_class("caption")
        self.add_suffix(key_label)
        self.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))


class SearchPage:
    """Builds a search results page from query text."""

    def __init__(self, schema: dict):
        self._schema = schema
        self._all_options = self._index_options()

    def _index_options(self) -> list[dict]:
        """Build a flat index of all options with group/section context."""
        result = []
        for group in schema_mod.get_groups(self._schema):
            for section in group.get("sections", []):
                for option in section.get("options", []):
                    entry = dict(option)
                    if group.get("hidden"):
                        entry["_group_id"] = group.get("parent_page", group["id"])
                        entry["_group_label"] = group.get("parent_label", group["label"])
                    else:
                        entry["_group_id"] = group["id"]
                        entry["_group_label"] = group["label"]
                    entry["_section_label"] = section.get("label", "")
                    # Searchable text
                    entry["_search_text"] = (
                        f"{option.get('label', '')} "
                        f"{option.get('description', '')} "
                        f"{option.get('key', '')}"
                    ).lower()
                    result.append(entry)
        return result

    def add_entries(self, entries: list[dict]):
        """Add extra searchable entries (e.g. from custom pages like Monitors).

        Each entry should have: key, label, description (optional),
        _group_id, _group_label, _section_label.
        """
        for entry in entries:
            entry["_search_text"] = (
                f"{entry.get('label', '')} {entry.get('description', '')} {entry.get('key', '')}"
            ).lower()
            self._all_options.append(entry)

    def search(self, query: str) -> list[dict]:
        """Return matching options for a query."""
        if not query or len(query) < MIN_QUERY_LENGTH:
            return []
        terms = query.lower().split()
        results = []
        for opt in self._all_options:
            if all(t in opt["_search_text"] for t in terms):
                results.append(opt)
        return results

    def build_results_widget(self, results: list[dict], on_activate) -> Gtk.Widget:
        """Build a widget showing search results."""
        if not results:
            status = Adw.StatusPage(
                title="No Results",
                description="Try a different search term.",
                icon_name="edit-find-symbolic",
            )
            return status

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        group = Adw.PreferencesGroup(
            title=f"{len(results)} result{'s' if len(results) != 1 else ''}",
        )

        for opt in results:
            row = SearchResultRow(
                opt,
                group_label=opt["_group_label"],
                section_label=opt["_section_label"],
            )
            row.connect("activated", lambda r: on_activate(r.group_id, r.option_key))
            group.add(row)

        box.append(group)
        return box
