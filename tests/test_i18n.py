"""Tests for internationalization (i18n) support and catalog validity."""

import string

import pytest

from hyprmod.core.schema import localize
from hyprmod.i18n import (
    _CATALOG,
    _,
    get_current_language,
    get_supported_languages,
    set_language,
)


@pytest.fixture(autouse=True)
def _reset_language():
    """Ensure tests start and finish in English."""
    set_language("en")
    yield
    set_language("en")


class TestI18nLanguageSwitching:
    def test_default_language_is_en(self):
        assert get_current_language() == "en"

    def test_set_supported_language(self):
        set_language("es")
        assert get_current_language() == "es"

    def test_unsupported_language_falls_back_to_en(self):
        set_language("fr")
        assert get_current_language() == "en"

    def test_supported_languages_dict(self):
        langs = get_supported_languages()
        assert "en" in langs
        assert "es" in langs
        assert langs["es"] == "Español"
        assert langs["en"] == "English"


class TestTranslation:
    def test_translation_in_en_returns_original(self):
        assert _("Save") == "Save"
        assert _("General") == "General"

    def test_translation_in_es_returns_spanish(self):
        set_language("es")
        assert _("Save") == "Guardar"
        assert _("Cancel") == "Cancelar"

    def test_unknown_string_returns_original(self):
        set_language("es")
        assert _("NonExistentString12345") == "NonExistentString12345"


class TestCatalogConsistency:
    def test_all_format_placeholders_match(self):
        formatter = string.Formatter()
        mismatches = []
        for en, es in _CATALOG.items():
            en_fields = {
                field_name for _, field_name, _, _ in formatter.parse(en) if field_name is not None
            }
            es_fields = {
                field_name for _, field_name, _, _ in formatter.parse(es) if field_name is not None
            }
            if en_fields != es_fields:
                mismatches.append(f"{en!r} ({en_fields}) != {es!r} ({es_fields})")
        assert not mismatches, f"Format field mismatches: {mismatches}"

    def test_no_empty_translations(self):
        empty = [en for en, es in _CATALOG.items() if not es.strip()]
        assert not empty, f"Empty translations found for: {empty}"


class TestSchemaLocalization:
    def test_localize_translates_schema_in_place(self):
        set_language("es")
        schema = {
            "groups": [
                {
                    "id": "general",
                    "label": "General",
                    "sections": [
                        {
                            "id": "layout",
                            "label": "Layout",
                            "description": "Tiling layout and direction.",
                            "options": [
                                {
                                    "key": "general:layout",
                                    "label": "Tiling layout",
                                    "description": "Which layout engine to use.",
                                    "values": [
                                        {"value": "dwindle", "label": "Dwindle"},
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        }
        localize(schema)
        assert schema["groups"][0]["label"] == _("General")
        assert schema["groups"][0]["sections"][0]["label"] == _("Layout")
