"""Override tracking — self-contained OverrideTracker for keybind overrides.

Tracks which HyprMod-owned keybinds override Hyprland-runtime keybinds.
"""

from collections.abc import Sequence
from pathlib import Path

from hyprland_config import (
    Assignment,
    BindData,
    Document,
    Keyword,
    is_bind_keyword,
    parse_bind_line,
)
from hyprland_config import load as load_document

from hyprmod.core import config


class OverrideTracker:
    """Track which HyprMod-managed binds override Hyprland's runtime binds.

    This allows HyprMod to emit 'unbind' lines in the config for overridden
    binds, and to filter the Hyprland bind list to avoid showing duplicates.
    """

    def __init__(self, all_hypr_binds: list[BindData], document: Document | None = None):
        self._hypr_binds = list(all_hypr_binds)
        self._hypr_by_combo: dict[tuple, BindData] = {b.combo: b for b in self._hypr_binds}
        self._document = document
        # Session overrides: owned_index -> original BindData
        self._session_overrides: dict[int, BindData] = {}
        # Saved overrides (from config file): owned_index -> original BindData
        self._saved_overrides: dict[int, BindData] = {}

    def add_override(self, owned_index: int, original: BindData) -> None:
        """Mark an owned bind as overriding a Hyprland bind."""
        self._session_overrides[owned_index] = original

    def has_original(self, owned_index: int) -> bool:
        """Check if an owned bind overrides a Hyprland bind (session or saved)."""
        return owned_index in self._session_overrides or owned_index in self._saved_overrides

    def get_original(self, owned_index: int) -> BindData | None:
        """Get the original Hyprland bind for an owned override."""
        return self._session_overrides.get(owned_index, self._saved_overrides.get(owned_index))

    def remove_at(self, owned_index: int, removed_bind: BindData | None = None) -> BindData | None:
        """Remove an owned bind at the given index and re-index remaining.

        *removed_bind*: the owned bind that was removed. When provided, the
        Hyprland bind list is updated: the stale override entry is swapped for
        the restored original (needed after save + restart where live binds
        included the override from GUI_CONF).

        Returns the original BindData if it was an override, else None.
        """
        original = self._session_overrides.pop(owned_index, None)
        if original is None:
            original = self._saved_overrides.pop(owned_index, None)

        # Re-index: shift all indices above owned_index down by 1
        self._session_overrides = {
            (k - 1 if k > owned_index else k): v for k, v in self._session_overrides.items()
        }
        self._saved_overrides = {
            (k - 1 if k > owned_index else k): v for k, v in self._saved_overrides.items()
        }

        if original is not None and removed_bind is not None:
            self._swap_hypr_bind(removed_bind, original)

        return original

    def _swap_hypr_bind(self, removed: BindData, original: BindData) -> None:
        """Replace a stale override entry in the Hyprland bind list with the original.

        Hyprland puts re-bound keys at the end of its list, so after
        save + restart the override entry is in the wrong position.
        We use the config document order to place the restored original
        where it belongs.
        """
        self._hypr_binds = [b for b in self._hypr_binds if b.combo != removed.combo]
        self._hypr_by_combo.pop(removed.combo, None)

        insert_idx = self._config_bind_position(original.combo)
        if insert_idx is not None:
            self._hypr_binds.insert(insert_idx, original)
        elif original.combo not in self._hypr_by_combo:
            self._hypr_binds.append(original)
        self._hypr_by_combo[original.combo] = original

    def _config_bind_position(self, combo: tuple) -> int | None:
        """Find the insertion index for *combo* based on config document order.

        Builds an ordered list of combos from the config (excluding GUI_CONF)
        and returns the index in ``_hypr_binds`` where *combo* should be
        inserted so that the relative order matches the config file.
        """
        if self._document is None:
            return None
        excluded = frozenset({Path(config.GUI_CONF).resolve()})
        config_order: list[tuple] = []
        for kw in self._document.find_all("bind*", exclude_sources=excluded):
            expanded = self._document.expand(kw.raw.strip())
            bd = parse_bind_line(expanded)
            if bd is not None:
                config_order.append(bd.combo)

        if combo not in config_order:
            return None

        target_pos = config_order.index(combo)
        # Find combos that should come AFTER the target in config order
        after_set = set(config_order[target_pos + 1 :])

        # Insert just before the first _hypr_binds entry that comes after
        # the target in config order.
        for i, b in enumerate(self._hypr_binds):
            if b.combo in after_set:
                return i
        return len(self._hypr_binds)

    def filter_hypr_binds(self, owned_binds: Sequence[BindData]) -> list[BindData]:
        """Return Hyprland binds that are NOT overridden by owned binds.

        Filters out binds whose combo matches an owned bind's combo,
        as well as binds that are tracked as overrides (even with changed combos).
        """
        # Collect all original combos that are overridden
        overridden_combos = set()
        # Same-combo overrides
        owned_combos = {b.combo for b in owned_binds}
        for hb in self._hypr_binds:
            if hb.combo in owned_combos:
                overridden_combos.add(hb.combo)
        # Changed-combo overrides (tracked in session or saved)
        for original in self._session_overrides.values():
            overridden_combos.add(original.combo)
        for original in self._saved_overrides.values():
            overridden_combos.add(original.combo)

        return [hb for hb in self._hypr_binds if hb.combo not in overridden_combos]

    def get_bind_lines(self, owned_binds: Sequence[BindData]) -> list[str]:
        """Generate config lines: unbind for overrides, then bind for all owned."""
        lines = []
        unbind_combos: set[tuple] = set()

        # Collect all original combos that need unbinding.
        # Use the original BindData's key/mods_str to preserve casing —
        # Hyprland's unbind is case-sensitive.
        for idx, bind in enumerate(owned_binds):
            original = self.get_original(idx)
            if original is not None:
                combo = original.combo
                if combo not in unbind_combos:
                    unbind_combos.add(combo)
                    lines.append(f"unbind = {original.mods_str}, {original.key}")
            elif bind.combo in self._hypr_by_combo:
                combo = bind.combo
                if combo not in unbind_combos:
                    unbind_combos.add(combo)
                    source = self._hypr_by_combo[combo]
                    lines.append(f"unbind = {source.mods_str}, {source.key}")

        # Emit bind lines
        for bind in owned_binds:
            lines.append(bind.to_line())
        return lines

    def parse_saved_overrides(self, owned_binds: Sequence[BindData]) -> None:
        """Parse unbind+bind pairs from the saved config to restore override tracking.

        Reads the current GUI_CONF and matches unbind lines to owned binds.
        Uses hyprland_config to parse keywords in document order (rather than
        manual line splitting) so parsing stays consistent with the library.
        """
        self._saved_overrides.clear()
        if not config.GUI_CONF.exists():
            return

        doc = load_document(config.GUI_CONF, follow_sources=False)

        # Walk all lines in document order, pairing unbind -> next bind.
        # Assignments between unbind and bind break the pairing.
        unbind_bind_pairs: list[tuple[tuple, tuple]] = []
        pending_unbind: tuple | None = None

        for node in doc.lines:
            if isinstance(node, Assignment):
                pending_unbind = None
                continue
            if not isinstance(node, Keyword):
                continue

            if node.key == "unbind":
                parts = [p.strip() for p in node.value.split(",", 1)]
                if len(parts) == 2:
                    mods = tuple(sorted(m.upper() for m in parts[0].split() if m.strip()))
                    k = parts[1].strip().upper()
                    pending_unbind = (mods, k)
                continue

            if is_bind_keyword(node.key) and pending_unbind is not None:
                bind_data = parse_bind_line(node.raw.strip())
                if bind_data is not None:
                    unbind_bind_pairs.append((pending_unbind, bind_data.combo))
                pending_unbind = None
                continue

            # Any other keyword resets pending unbind
            pending_unbind = None

        # Match unbind_bind_pairs to owned binds.
        # Prefer the config document (excluding GUI_CONF) over live binds:
        # after restart, same-combo overrides mean _hypr_by_combo holds
        # the override action, not the original.
        for idx, bind in enumerate(owned_binds):
            for ub_combo, bind_combo in unbind_bind_pairs:
                if bind.combo == bind_combo:
                    hypr = self._find_bind_in_config(ub_combo)
                    if hypr is None:
                        hypr = self._hypr_by_combo.get(ub_combo)
                    if hypr is not None:
                        self._saved_overrides[idx] = hypr
                    break

    def _find_bind_in_config(self, combo: tuple) -> BindData | None:
        """Find a bind matching *combo* in the Hyprland config tree, excluding GUI_CONF.

        After save + restart, the original bind is no longer in Hyprland's live
        binds (our config unbound it), or — for same-combo overrides — the live
        bind has the override action instead of the original.  This resolves
        the true original from the config document.
        """
        if self._document is None:
            return None
        excluded = frozenset({Path(config.GUI_CONF).resolve()})
        for kw in reversed(self._document.find_all("bind*", exclude_sources=excluded)):
            expanded = self._document.expand(kw.raw.strip())
            bd = parse_bind_line(expanded)
            if bd is not None and bd.combo == combo:
                bd.owned = False
                return bd
        return None

    def mark_saved(self, owned_binds: Sequence[BindData]) -> None:
        """After a save: merge session overrides into saved, then re-parse."""
        # Clear session overrides
        self._session_overrides.clear()
        # Re-parse from disk
        self.parse_saved_overrides(owned_binds)

    def clear_session_overrides(self) -> list[BindData]:
        """Clear all session overrides and return the originals.

        Used when discarding changes.
        """
        originals = list(self._session_overrides.values())
        self._session_overrides.clear()
        return originals
