"""Tests for the XDG install/registration helpers."""

from pathlib import Path

import pytest
from gi.repository import Gio, GLib

from hyprmod import install

DESKTOP_TEXT = "[Desktop Entry]\nType=Application\nExec=hyprmod\nTerminal=false\n"


def _desktop_file(tmp_path: Path) -> Path:
    path = tmp_path / install.DESKTOP_FILE
    path.write_text(DESKTOP_TEXT)
    return path


class TestIsRegistered:
    def test_returns_true_when_entry_found(self, monkeypatch):
        monkeypatch.setattr(Gio.DesktopAppInfo, "new", staticmethod(lambda _id: object()))
        assert install.is_registered() is True

    def test_returns_false_on_constructor_returned_null(self, monkeypatch):
        # PyGObject turns a NULL return from Gio.DesktopAppInfo.new into a
        # TypeError rather than None; is_registered must treat that as "absent"
        # instead of crashing first launch. https://github.com/BlueManCZ/hyprmod/issues/52
        def _raise(_id):
            raise TypeError("constructor returned NULL")

        monkeypatch.setattr(Gio.DesktopAppInfo, "new", staticmethod(_raise))
        assert install.is_registered() is False


class TestPinExecToBinary:
    @pytest.mark.parametrize(
        "binary",
        [
            "/home/user/.local/bin/hyprmod",
            "/home/my user/bin/hyprmod",
            "/home/my$dir/hyprmod",
            "/home/back\\slash/hyprmod",
            '/home/qu"ote/hyprmod',
            "/home/tick`s/hyprmod",
        ],
    )
    def test_written_exec_line_parses_back_to_the_binary(self, tmp_path, monkeypatch, binary):
        # Reserved characters need escaping twice over: once for Exec argument
        # quoting, once for the key file value the desktop reads it out of.
        monkeypatch.setattr(install.shutil, "which", lambda _: binary)
        path = _desktop_file(tmp_path)

        install._pin_exec_to_binary(path)

        key_file = GLib.KeyFile()
        key_file.load_from_file(str(path), GLib.KeyFileFlags.NONE)
        exec_value = key_file.get_string("Desktop Entry", "Exec")
        assert GLib.shell_parse_argv(exec_value)[1] == [binary]

    def test_leaves_file_alone_when_binary_is_not_on_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr(install.shutil, "which", lambda _: None)
        path = _desktop_file(tmp_path)

        install._pin_exec_to_binary(path)

        assert path.read_text() == DESKTOP_TEXT


class TestInstallUserFiles:
    def test_installed_entry_points_at_the_binary(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        monkeypatch.setattr(install.shutil, "which", lambda _: "/opt/hyprmod/bin/hyprmod")

        placed = install.install_user_files(quiet=True)

        desktop = tmp_path / "applications" / install.DESKTOP_FILE
        assert desktop in placed
        assert "Exec=/opt/hyprmod/bin/hyprmod\n" in desktop.read_text()
