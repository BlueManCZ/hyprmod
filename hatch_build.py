"""Hatchling build hook.

Three jobs: compile the GSettings schema, compile gettext message catalogs
from ``po/`` into ``hyprmod/data/locale/``, and mirror repo-root data files
(``.desktop`` entry, AppStream metainfo) into ``hyprmod/data/`` so they
ship inside the wheel. The repo-root ``data/`` tree stays canonical for
distro packagers; the build hook just makes the same files available to
``hyprmod --install`` for pipx/uv users.
"""

import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version, build_data):
        root = Path(self.root)
        pkg_data = root / "hyprmod" / "data"

        subprocess.run(["glib-compile-schemas", str(pkg_data)], check=True)
        self._bundle(build_data, pkg_data / "gschemas.compiled", "hyprmod/data/gschemas.compiled")

        for src in (root / "data" / "applications").glob("*.desktop"):
            self._bundle(build_data, src, f"hyprmod/data/applications/{src.name}")
        for src in (root / "data" / "metainfo").glob("*.xml"):
            self._bundle(build_data, src, f"hyprmod/data/metainfo/{src.name}")

        po_dir = root / "po"
        if po_dir.exists():
            for po_file in po_dir.glob("*.po"):
                lang = po_file.stem
                mo_dir = pkg_data / "locale" / lang / "LC_MESSAGES"
                mo_dir.mkdir(parents=True, exist_ok=True)
                mo_file = mo_dir / "hyprmod.mo"
                subprocess.run(["msgfmt", "-o", str(mo_file), str(po_file)], check=True)
                self._bundle(
                    build_data,
                    mo_file,
                    f"hyprmod/data/locale/{lang}/LC_MESSAGES/hyprmod.mo",
                )

    @staticmethod
    def _bundle(build_data, src: Path, wheel_path: str) -> None:
        build_data["force_include"][str(src)] = wheel_path
