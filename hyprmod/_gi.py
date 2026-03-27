"""Centralized GI typelib version requirements.

Import this module before any ``from gi.repository import ...`` statements.
All version pins live here, so individual modules don't need to repeat them.
"""

import gi

gi.require_version("Adw", "1")
gi.require_version("Gdk", "4.0")
gi.require_version("GnomeDesktop", "4.0")
gi.require_version("Gtk", "4.0")
