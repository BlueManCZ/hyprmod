"""Dynamic value sources for schema options.

Each source is a callable that returns a list of {"id": ..., "label": ...} dicts.
Register new sources by adding them to the _SOURCES dict.
"""

import functools
from collections.abc import Callable

import gi


class MissingDependencyError(Exception):
    """A system dependency required by a source provider is not available."""


@functools.cache
def _get_xkb_info():
    try:
        gi.require_version("GnomeDesktop", "4.0")
    except ValueError:
        raise MissingDependencyError("Install gnome-desktop-4 for keyboard layout data")
    from gi.repository import GnomeDesktop  # type: ignore[attr-defined]

    return GnomeDesktop.XkbInfo()


def _xkb_layouts(**_) -> list[dict]:
    """Return all base XKB layouts (no variants) sorted by display name."""
    xkb = _get_xkb_info()
    results = []
    for layout_id in xkb.get_all_layouts():
        if "+" in layout_id:
            continue  # skip variant entries
        ok, display_name, *_ = xkb.get_layout_info(layout_id)
        if ok:
            results.append({"id": layout_id, "label": f"{display_name} ({layout_id})"})
    results.sort(key=lambda v: v["label"].casefold())
    return results


def _xkb_variants(layout: str = "us", **_) -> list[dict]:
    """Return all variants for a given base layout."""
    xkb = _get_xkb_info()
    results = [{"id": "", "label": "Default"}]
    prefix = f"{layout}+"
    for layout_id in xkb.get_all_layouts():
        if not layout_id.startswith(prefix):
            continue
        ok, display_name, _, _, variant = xkb.get_layout_info(layout_id)
        if ok and variant:
            results.append({"id": variant, "label": display_name})
    results[1:] = sorted(results[1:], key=lambda v: v["label"].casefold())
    return results


def _xkb_options(**_) -> list[dict]:
    """Return all XKB options grouped by category, sorted by group then label."""
    xkb = _get_xkb_info()
    results = []
    for group_id in xkb.get_all_option_groups():
        group_desc = xkb.description_for_group(group_id)
        for option_id in xkb.get_options_for_group(group_id):
            option_desc = xkb.description_for_option(group_id, option_id)
            results.append(
                {
                    "id": option_id,
                    "label": option_desc,
                    "group": group_desc,
                }
            )
    results.sort(key=lambda v: (v["group"].casefold(), v["label"].casefold()))
    return results


_SOURCES: dict[str, Callable] = {
    "xkb_layouts": _xkb_layouts,
    "xkb_variants": _xkb_variants,
    "xkb_options": _xkb_options,
}


def get_source_values(source_name: str, **kwargs) -> list[dict]:
    """Look up a source by name and return its values."""
    provider = _SOURCES.get(source_name)
    if provider is None:
        return []
    try:
        return provider(**kwargs)
    except MissingDependencyError:
        raise
    except ValueError:
        return []
