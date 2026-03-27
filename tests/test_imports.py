"""Verify that all package modules import without errors.

Catches broken imports, circular dependencies, missing gi.require_version
calls, and typos in from-imports that only surface at runtime.
"""

import importlib

import pytest

ALL_MODULES = [
    "hyprmod",
    "hyprmod.main",
    "hyprmod.window",
    # core
    "hyprmod.core",
    "hyprmod.core.config",
    "hyprmod.core.schema",
    "hyprmod.core.setup",
    "hyprmod.core.state",
    "hyprmod.core.undo",
    "hyprmod.core.profiles",
    # ui
    "hyprmod.ui",
    "hyprmod.ui.options",
    "hyprmod.ui.banner",
    "hyprmod.ui.dna",
    "hyprmod.ui.search",
    "hyprmod.ui.timer",
    "hyprmod.ui.sources",
    "hyprmod.ui.bezier_canvas",
    "hyprmod.ui.bezier_editor",
    "hyprmod.ui.monitor_preview",
    # pages
    "hyprmod.pages",
    "hyprmod.pages.animations",
    "hyprmod.pages.binds",
    "hyprmod.pages.monitors",
    "hyprmod.pages.profiles",
    # binds
    "hyprmod.binds",
    "hyprmod.binds.dialog",
    "hyprmod.binds.dispatchers",
    "hyprmod.binds.helpers",
    "hyprmod.binds.override_state",
    # data
    "hyprmod.data",
    "hyprmod.data.bezier_data",
]


@pytest.mark.parametrize("module", ALL_MODULES)
def test_import(module):
    importlib.import_module(module)
