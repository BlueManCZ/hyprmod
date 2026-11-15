"""Keyboard shortcuts overlay — standard GNOME ``Gtk.ShortcutsWindow``.

Built from an XML template via ``Gtk.Builder`` so translatable strings are
picked up correctly and the result matches the GTK idiom. The window is
attached to the main window with ``set_help_overlay()``, which automatically
wires the ``win.show-help-overlay`` action.
"""

from gi.repository import Gtk

from hyprmod.i18n import _

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
            <property name="title" translatable="yes">{general}</property>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="accelerator">&lt;Control&gt;s</property>
                <property name="title" translatable="yes">{save_changes}</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="accelerator">&lt;Control&gt;z</property>
                <property name="title" translatable="yes">{undo}</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="accelerator">&lt;Control&gt;&lt;Shift&gt;z</property>
                <property name="title" translatable="yes">{redo}</property>
              </object>
            </child>
          </object>
        </child>
        <child>
          <object class="GtkShortcutsGroup">
            <property name="title" translatable="yes">{search}</property>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="accelerator">&lt;Control&gt;f</property>
                <property name="title" translatable="yes">{search_options}</property>
              </object>
            </child>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="accelerator">Escape</property>
                <property name="title" translatable="yes">{close_search}</property>
              </object>
            </child>
          </object>
        </child>
        <child>
          <object class="GtkShortcutsGroup">
            <property name="title" translatable="yes">{help}</property>
            <child>
              <object class="GtkShortcutsShortcut">
                <property name="accelerator">&lt;Control&gt;question F1</property>
                <property name="title" translatable="yes">{show_shortcuts}</property>
              </object>
            </child>
          </object>
        </child>
      </object>
    </child>
  </object>
</interface>
""".format(
    general=_("General"),
    save_changes=_("Save changes"),
    undo=_("Undo last change"),
    redo=_("Redo change"),
    search=_("Search"),
    search_options=_("Search options"),
    close_search=_("Close search"),
    help=_("Help"),
    show_shortcuts=_("Show keyboard shortcuts"),
)


def build_shortcuts_window() -> Gtk.ShortcutsWindow:
    """Construct the keyboard shortcuts overlay from the XML template."""
    builder = Gtk.Builder.new_from_string(SHORTCUTS_UI, -1)
    window = builder.get_object("shortcuts_window")
    if not isinstance(window, Gtk.ShortcutsWindow):
        raise RuntimeError("Failed to build Gtk.ShortcutsWindow from template")
    return window
