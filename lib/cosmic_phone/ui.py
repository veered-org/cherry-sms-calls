"""Shared GTK3 plumbing for the panel windows (cosmic-sms, cosmic-dial).

* a solid "glass" style matching the COSMIC dark theme,
* a narrow strip anchored to the right edge of the monitor under the pointer
  (via gtk-layer-shell when installed),
* a pidfile toggle, so a second click on the panel button closes the window,
* "one panel window at a time" across a family of such windows.

Layer-shell surfaces have no titlebar, no close button and no alt-tab entry,
so every window built on this module MUST ship its own exits (Esc, a Close
button, and the pidfile toggle), and must never use KeyboardMode.EXCLUSIVE -
that is a compositor-wide keyboard grab with no way out short of logging out.
"""
import atexit
import os
import signal

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GLib  # noqa: E402

ACCENT = "#63d0df"

# Opaque on purpose: without compositor-side blur (which GTK3 cannot request),
# translucency just lets whatever is behind the window wash out the text.
CSS = b"""
window.cosmicglass {
    background-image: linear-gradient(to bottom, rgb(30, 33, 40), rgb(12, 12, 14));
    color: #eef0f4;
    border-left: 1px solid rgba(255, 255, 255, 0.14);
    border-bottom: 1px solid rgba(255, 255, 255, 0.14);
}
window.cosmicglass entry,
window.cosmicglass textview,
window.cosmicglass textview text {
    background-color: rgba(255, 255, 255, 0.09);
    background-image: none;
    color: #eef0f4;
    border: 1px solid rgba(255, 255, 255, 0.16);
    border-radius: 4px;
    caret-color: #eef0f4;
}
window.cosmicglass entry:focus,
window.cosmicglass textview:focus { border-color: rgba(99, 208, 223, 0.65); }
window.cosmicglass entry placeholder { color: rgba(238, 240, 244, 0.55); }
window.cosmicglass button {
    background-image: none;
    background-color: rgba(255, 255, 255, 0.12);
    color: #eef0f4;
    border: 1px solid rgba(255, 255, 255, 0.18);
    border-radius: 4px;
    padding: 4px 8px;
}
window.cosmicglass button:hover  { background-color: rgba(255, 255, 255, 0.20); }
window.cosmicglass button:active { background-color: rgba(255, 255, 255, 0.28); }
window.cosmicglass button.suggested-action {
    background-color: rgba(99, 208, 223, 0.34);
    border-color: rgba(99, 208, 223, 0.60);
    color: #eafcff;
}
window.cosmicglass button.suggested-action:hover { background-color: rgba(99, 208, 223, 0.46); }
window.cosmicglass frame, window.cosmicglass scrolledwindow, window.cosmicglass box,
window.cosmicglass grid, window.cosmicglass separator {
    background-color: transparent;
    background-image: none;
}
window.cosmicglass frame border { border: none; }
window.cosmicglass separator { background-color: rgba(255, 255, 255, 0.14); }
window.cosmicglass treeview, window.cosmicglass treeview.view {
    background-color: rgba(255, 255, 255, 0.05);
    background-image: none;
    color: #eef0f4;
    border-radius: 4px;
}
window.cosmicglass treeview:selected, window.cosmicglass treeview.view:selected {
    background-color: rgba(99, 208, 223, 0.35);
}
window.cosmicglass label { color: #eef0f4; }
"""

_css_installed = False


def apply_style(win):
    """Call before show_all(). Does NOT set app_paintable: that is what makes
    a GTK window see-through, regardless of the CSS colours."""
    global _css_installed
    win.get_style_context().add_class("cosmicglass")
    if not _css_installed:
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            win.get_screen(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        _css_installed = True


def runtime_dir():
    return os.environ.get("XDG_RUNTIME_DIR") or "/tmp"


def toggle_instance(name):
    """Second launch closes the running window instead of opening another.
    This is the mouse-only way out when the keyboard is unavailable."""
    path = os.path.join(runtime_dir(), name + ".pid")
    try:
        old = int(open(path).read().strip())
        alive = name in open("/proc/%d/cmdline" % old).read().replace("\0", " ")
    except Exception:
        alive = False
    if alive:
        os.kill(old, signal.SIGTERM)
        raise SystemExit(0)
    with open(path, "w") as f:
        f.write(str(os.getpid()))

    def cleanup():
        try:
            os.unlink(path)
        except OSError:
            pass

    atexit.register(cleanup)

    def on_term(*_):
        # Through GLib, not signal.signal(): a plain Python handler never runs
        # while the process sits inside Gtk.main(), so the pidfile would
        # survive and the next click would think the window was still up.
        cleanup()
        Gtk.main_quit()
        return False

    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, on_term)


BASE_FAMILY = ("cosmic-sms", "cosmic-dial")


def close_others(mine, extra=()):
    """Close any other panel window in the family so only one is on screen."""
    for name in tuple(BASE_FAMILY) + tuple(extra):
        if not name or name == mine:
            continue
        try:
            pid = int(open(os.path.join(runtime_dir(), name + ".pid")).read().strip())
            # cmdline is NUL-separated: join before matching.
            cmd = open("/proc/%d/cmdline" % pid).read().replace("\0", " ")
        except Exception:
            continue
        if name not in cmd:
            continue                      # pid reused by something unrelated
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def target_geometry(width_frac=0.15, height_frac=0.5, min_width=280):
    """(width, height) of a strip on the monitor under the pointer."""
    display = Gdk.Display.get_default()
    monitor = None
    try:
        _, x, y = display.get_default_seat().get_pointer().get_position()
        monitor = display.get_monitor_at_point(x, y)
    except Exception:
        pass
    if monitor is None:
        monitor = display.get_monitor(0)
    g = monitor.get_geometry()
    return max(min_width, int(g.width * width_frac)), int(g.height * height_frac)


def anchor_right(win):
    """Anchor TOP+RIGHT with gtk-layer-shell if available (apt:
    gir1.2-gtklayershell-0.1). Without it the compositor places the window."""
    try:
        gi.require_version("GtkLayerShell", "0.1")
        from gi.repository import GtkLayerShell
    except Exception:
        return False
    GtkLayerShell.init_for_window(win)
    GtkLayerShell.set_layer(win, GtkLayerShell.Layer.TOP)
    for edge in (GtkLayerShell.Edge.RIGHT, GtkLayerShell.Edge.TOP):
        GtkLayerShell.set_anchor(win, edge, True)
    # ON_DEMAND, NEVER EXCLUSIVE (see module docstring).
    GtkLayerShell.set_keyboard_mode(win, GtkLayerShell.KeyboardMode.ON_DEMAND)
    return True


def is_close_key(ev):
    ctrl = ev.state & Gdk.ModifierType.CONTROL_MASK
    return ev.keyval == Gdk.KEY_Escape or (ctrl and ev.keyval in (Gdk.KEY_w, Gdk.KEY_W))
