"""Tests for gettext internationalization infrastructure."""

import subprocess
from pathlib import Path

import pytest

from hyprmod.i18n import (
    N_,
    _,
    get_locale_dir,
    init_i18n,
    ngettext,
    npgettext,
    pgettext,
)


@pytest.fixture(autouse=True)
def _reset_i18n():
    """Reset the translation catalog to fallback before and after each test."""
    init_i18n(locale_dir="/nonexistent", languages=["en"])
    yield
    init_i18n()


class TestFallbackTranslations:
    def test_gettext_fallback_returns_original(self):
        assert _("Hello world") == "Hello world"

    def test_noop_marker_returns_string(self):
        assert N_("Constant string") == "Constant string"

    def test_ngettext_fallback(self):
        assert ngettext("{n} file", "{n} files", 1) == "{n} file"
        assert ngettext("{n} file", "{n} files", 2) == "{n} files"
        assert ngettext("{n} file", "{n} files", 0) == "{n} files"

    def test_pgettext_fallback(self):
        assert pgettext("dialog action", "Cancel") == "Cancel"

    def test_npgettext_fallback(self):
        assert npgettext("context", "{n} match", "{n} matches", 1) == "{n} match"
        assert npgettext("context", "{n} match", "{n} matches", 3) == "{n} matches"


class TestCatalogResolution:
    def test_get_locale_dir_returns_path(self):
        locale_dir = get_locale_dir()
        assert isinstance(locale_dir, Path)

    def test_init_i18n_loads_compiled_mo(self, tmp_path: Path):
        po_content = """\
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"
"Plural-Forms: nplurals=2; plural=(n != 1);\\n"

msgid "Save"
msgstr "Guardar"

msgctxt "navigation"
msgid "Back"
msgstr "Volver"

msgid "{count} result"
msgid_plural "{count} results"
msgstr[0] "{count} resultado"
msgstr[1] "{count} resultados"
"""
        po_file = tmp_path / "test.po"
        po_file.write_text(po_content, encoding="utf-8")

        mo_dir = tmp_path / "es" / "LC_MESSAGES"
        mo_dir.mkdir(parents=True)
        mo_file = mo_dir / "hyprmod.mo"

        subprocess.run(["msgfmt", "-o", str(mo_file), str(po_file)], check=True)

        init_i18n(locale_dir=tmp_path, languages=["es"])

        assert _("Save") == "Guardar"
        assert _("Unknown string") == "Unknown string"
        assert pgettext("navigation", "Back") == "Volver"
        assert pgettext("other context", "Back") == "Back"
        assert ngettext("{count} result", "{count} results", 1) == "{count} resultado"
        assert ngettext("{count} result", "{count} results", 2) == "{count} resultados"
