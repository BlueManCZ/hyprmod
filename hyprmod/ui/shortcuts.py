"""Keyboard shortcuts overlay — standard GNOME ``Gtk.ShortcutsWindow``.

Built from an XML template via ``Gtk.Builder`` so translatable strings are
picked up correctly and the result matches the GTK idiom. The window is
attached to the main window with ``set_help_overlay()``, which automatically
wires the ``win.show-help-overlay`` action.
"""

from gi.repository import Gtk

# Each ``<GtkShortcutsShortcut>`` pairs an accelerator (GTK accelerator syntax)
# with a user-facing title. Keep this list in sync with ``_setup_shortcuts`` in
# ``window.py`` — the action registration is the source of truth, this overlay
# is documentation.
SHORTCUTS_UI = """<?xml version="1.0" encoding="UTF-8"?>
<interface>
  <object class="GtkShortcutsWindow" id="shortcuts_window">
    <property name="modal">true</property>
    <child>
      <object class="GtkShortcutsSection">
        <property name="section-name">shortcuts</property>
        <property name="max-height">10</property>
        <child>
          <object class="GtkShortcutsGroup">
            <property name="title" translatable="yes">General</property>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="accelerator">&lt;Control&gt;s</property>
                <property name="title" translatable="yes">Save changes</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="accelerator">&lt;Control&gt;z</property>
                <property name="title" translatable="yes">Undo last change</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="accelerator">&lt;Control&gt;&lt;Shift&gt;z</property>
                <property name="title" translatable="yes">Redo change</property>
              </object>
            </child>
          </object>
        </child>
        <child>
          <object class="GtkShortcutsGroup">
            <property name="title" translatable="yes">Search</property>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="accelerator">&lt;Control&gt;f</property>
                <property name="title" translatable="yes">Search options</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="accelerator">Escape</property>
                <property name="title" translatable="yes">Close search</property>
              </object>
            </child>
          </object>
        </child>
        <child>
          <object class="GtkShortcutsGroup">
            <property name="title" translatable="yes">Help</property>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="accelerator">&lt;Control&gt;question F1</property>
                <property name="title" translatable="yes">Show keyboard shortcuts</property>
              </object>
            </child>
          </object>
        </child>
      </object>
    </child>
  </object>
</interface>
"""


def build_shortcuts_window() -> Gtk.ShortcutsWindow:
    """Construct the keyboard shortcuts overlay from the XML template."""
    builder = Gtk.Builder.new_from_string(SHORTCUTS_UI, -1)
    window = builder.get_object("shortcuts_window")
    if not isinstance(window, Gtk.ShortcutsWindow):
        raise RuntimeError("Failed to build Gtk.ShortcutsWindow from template")
    return window
