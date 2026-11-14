"""Shared test fixtures."""

import pytest

from hyprmod.core import config


@pytest.fixture(autouse=True)
def reset_config_caches():
    """Keep ``config.is_lua_mode()`` and ``config.read_cached()`` deterministic.

    Both caches survive across pytest function boundaries by default, which
    breaks tests that monkeypatch Hyprland config paths or the managed path.
    Clearing them at the seam of each test isolates state without forcing
    every test to remember.
    """
    config.invalidate_lua_mode_cache()
    config.invalidate_cache()
    yield
    config.invalidate_lua_mode_cache()
    config.invalidate_cache()


@pytest.fixture
def gui_conf_tmp(tmp_path, monkeypatch):
    """Redirect managed_path() to a temporary .conf for the duration of a test.

    Forces Hyprlang mode by pointing ``default_config_dir`` at *tmp_path*
    so the absent ``hyprland.lua`` flips ``is_lua_mode()`` to False —
    legacy tests that pre-date Lua support all assume the managed file
    is the canonical ``.conf``.
    """
    monkeypatch.setattr("hyprland_config.default_config_dir", lambda: tmp_path)
    target = tmp_path / "hyprland-gui.conf"
    with config.managed_path_override(target):
        yield target
