"""Load and validate the Hyprland options schema."""

import json
import logging
from collections.abc import Mapping
from pathlib import Path

import hyprland_schema
from hyprland_schema import HyprOption

logger = logging.getLogger(__name__)


def load_schema(version: str | None = None, path: Path | None = None) -> dict:
    """Load the option schema, preferring the catalog that matches *version*.

    *version* is the running Hyprland version as reported by
    ``HyprlandState.version`` — typically ``"0.54.3"`` (no ``v`` prefix).
    When ``None`` (Hyprland offline) or when the version can't be resolved
    (unknown tag, no network, migration failure), falls back to the bundled
    latest schema.
    """
    overlay = _load_options_json(path)
    _merge(overlay, _resolve_schema_options(version))
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
        logger.warning(
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
                schema_type = src.type
                option.setdefault("type", schema_type)
                option.setdefault("default", src.default)
                option.setdefault("description", src.description)
                desc = option.get("description", "")
                if desc and desc[0].islower():
                    option["description"] = desc[0].upper() + desc[1:]
                if option["type"] in ("int", "float"):
                    if src.min is not None:
                        option.setdefault("min", src.min)
                    if src.max is not None:
                        option.setdefault("max", src.max)
                if src.enum_values and "values" not in option:
                    option["values"] = [
                        {"id": str(i), "label": v} for i, v in enumerate(src.enum_values)
                    ]


def get_groups(schema: dict) -> list[dict]:
    return schema.get("groups", [])


def get_options_flat(schema: dict) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for group in get_groups(schema):
        for section in group.get("sections", []):
            for option in section.get("options", []):
                result[option["key"]] = option
    return result
