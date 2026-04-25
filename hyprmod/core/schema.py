"""Load and validate the Hyprland options schema."""

import json
import logging
from collections.abc import Mapping
from pathlib import Path

import hyprland_schema
from hyprland_schema import HyprOption

log = logging.getLogger(__name__)


def load_schema(version: str | None = None, path: Path | None = None) -> dict:
    """Load the option schema, preferring the catalog that matches *version*.

    *version* is the running Hyprland version as reported by
    ``HyprlandState.version`` — typically ``"0.54.3"`` (no ``v`` prefix).
    When ``None`` (Hyprland offline) or when the version can't be resolved
    (unknown tag, no network, migration failure), falls back to the bundled
    latest schema.

    Groups whose options are entirely unavailable in the running Hyprland
    version (e.g. ``scrolling`` on 0.49, before the layout was added) are
    dropped so they don't appear as empty pages in the sidebar.
    """
    overlay = _load_options_json(path)
    _merge(overlay, _resolve_schema_options(version))
    _drop_unavailable(overlay)
    return overlay


def _resolve_schema_options(version: str | None) -> Mapping[str, HyprOption]:
    """Resolve the version-matched option catalog, falling back to the bundle."""
    if version is None:
        return hyprland_schema.OPTIONS_BY_KEY

    # hyprland_schema.load() keys versions by the GitHub tag (``vX.Y.Z``),
    # while HyprlandState.version drops the prefix (``X.Y.Z``). Normalise.
    tag = version if version.startswith("v") else f"v{version}"
    try:
        return hyprland_schema.load(tag).options_by_key
    except hyprland_schema.MigrationError as exc:
        log.warning(
            "Could not load schema for Hyprland %s (%s); using bundled %s",
            version,
            exc,
            hyprland_schema.HYPRLAND_VERSION,
        )
        return hyprland_schema.OPTIONS_BY_KEY


def _load_options_json(path: Path | None = None) -> dict:
    if path is None:
        path = Path(__file__).parent.parent / "data" / "schema" / "options.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _merge(overlay: dict, schema_by_key: Mapping[str, HyprOption]) -> None:
    for group in overlay.get("groups", []):
        for section in group.get("sections", []):
            for option in section.get("options", []):
                src = schema_by_key.get(option["key"])
                if src is None:
                    continue
                # HyprOption.to_dict() already omits None optionals (e.g. min/max
                # when unset and enum_values when absent), so we can use setdefault
                # directly without `is not None` guards.
                src_d = src.to_dict()
                for field in ("type", "default", "description"):
                    option.setdefault(field, src_d[field])
                desc = option["description"]
                if desc and desc[0].islower():
                    option["description"] = desc[0].upper() + desc[1:]
                if option["type"] in ("int", "float"):
                    for field in ("min", "max"):
                        if field in src_d:
                            option.setdefault(field, src_d[field])
                if src.enum_values and "values" not in option:
                    option["values"] = [
                        {"id": str(i), "label": v} for i, v in enumerate(src.enum_values)
                    ]


def _drop_unavailable(overlay: dict) -> None:
    """Drop options, sections, and groups unavailable in the running Hyprland.

    An option is considered unavailable when it has no ``type`` field after
    the merge — meaning neither the overlay nor the version-matched schema
    could supply one, so ``create_option_row`` would return ``None`` and the
    option would silently disappear. Sections with no remaining options are
    dropped; groups with no remaining sections are dropped, which hides the
    corresponding sidebar entry (e.g. ``Scrolling`` on Hyprland < 0.50).
    """
    kept_groups: list[dict] = []
    for group in overlay.get("groups", []):
        kept_sections: list[dict] = []
        for section in group.get("sections", []):
            kept_options = [opt for opt in section.get("options", []) if "type" in opt]
            if kept_options:
                section["options"] = kept_options
                kept_sections.append(section)
        if kept_sections:
            group["sections"] = kept_sections
            kept_groups.append(group)
    overlay["groups"] = kept_groups


def get_groups(schema: dict) -> list[dict]:
    return schema.get("groups", [])


def get_options_flat(schema: dict) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for group in get_groups(schema):
        for section in group.get("sections", []):
            for option in section.get("options", []):
                result[option["key"]] = option
    return result
