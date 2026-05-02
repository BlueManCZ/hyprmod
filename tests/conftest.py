"""Shared test fixtures."""

import pytest

from hyprmod.core import config


@pytest.fixture
def gui_conf_tmp(tmp_path):
    """Redirect gui_conf() to a temporary file for the duration of a test."""
    target = tmp_path / "hyprland-gui.conf"
    with config.gui_conf_override(target):
        yield target
