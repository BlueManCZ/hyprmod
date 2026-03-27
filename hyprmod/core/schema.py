"""Load and validate the Hyprland options schema."""

import json
from collections.abc import Mapping
from pathlib import Path

from hyprland_schema import OPTIONS_BY_KEY, HyprOption


def load_schema(path: Path | None = None) -> dict:
    overlay = _load_options_json(path)
    _merge(overlay, OPTIONS_BY_KEY)
    return overlay


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
