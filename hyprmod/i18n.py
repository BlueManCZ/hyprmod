"""Internationalization support using Python's standard gettext module."""

import gettext
import locale
from pathlib import Path

from hyprmod.data import bundled_data_dir

TEXT_DOMAIN = "hyprmod"

_current_translation: gettext.NullTranslations = gettext.NullTranslations()


def _(message: str) -> str:
    """Translate *message* using the active catalog, or return it unchanged."""
    return _current_translation.gettext(message)


def N_(message: str) -> str:
    """No-op marker for xgettext to extract strings defined at module level."""
    return message


def ngettext(singular: str, plural: str, n: int) -> str:
    """Translate a singular/plural message based on count *n*."""
    return _current_translation.ngettext(singular, plural, n)


def pgettext(context: str, message: str) -> str:
    """Translate *message* within a disambiguating *context*."""
    return _current_translation.pgettext(context, message)


def npgettext(context: str, singular: str, plural: str, n: int) -> str:
    """Translate a singular/plural message within a disambiguating *context*."""
    return _current_translation.npgettext(context, singular, plural, n)


def get_locale_dir() -> Path:
    """Return the directory containing compiled .mo message catalogs."""
    bundled = bundled_data_dir("locale")
    if bundled.exists():
        return bundled
    system_locale = Path("/usr/share/locale")
    if system_locale.exists():
        return system_locale
    return bundled


def init_i18n(
    locale_dir: Path | str | None = None,
    languages: list[str] | None = None,
) -> gettext.NullTranslations:
    """Initialize gettext translation following system locale or explicit settings."""
    global _current_translation

    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass

    loc_dir = Path(locale_dir) if locale_dir is not None else get_locale_dir()
    loc_str = str(loc_dir)

    try:
        gettext.bindtextdomain(TEXT_DOMAIN, loc_str)
        gettext.textdomain(TEXT_DOMAIN)
    except (AttributeError, OSError):
        pass

    try:
        if hasattr(locale, "bindtextdomain"):
            locale.bindtextdomain(TEXT_DOMAIN, loc_str)
            locale.textdomain(TEXT_DOMAIN)
    except (AttributeError, OSError):
        pass

    _current_translation = gettext.translation(
        TEXT_DOMAIN,
        localedir=loc_str,
        languages=languages,
        fallback=True,
    )
    return _current_translation


init_i18n()
