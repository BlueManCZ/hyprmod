"""Verify that all package modules import without errors.

Catches broken imports, circular dependencies, missing gi.require_version
calls, and typos in from-imports that only surface at runtime.

The module list is discovered at collection time so newly-added modules
are exercised automatically.
"""

import importlib
import pkgutil

import pytest

import hyprmod


def _walk_package(package) -> list[str]:
    return [
        name
        for _, name, _ in pkgutil.walk_packages(package.__path__, prefix=package.__name__ + ".")
    ]


ALL_MODULES = [hyprmod.__name__, *_walk_package(hyprmod)]


@pytest.mark.parametrize("module", ALL_MODULES)
def test_import(module):
    importlib.import_module(module)
