"""Typed GTK library locations for Linux and the application-private Windows runtime."""

import ctypes as C
import os
from pathlib import Path
import sys

LIBRARIES = {
    'gtk': ('libgtk-3.so.0', 'libgtk-3-0.dll'), 'gdk': ('libgdk-3.so.0', 'libgdk-3-0.dll'),
    'object': ('libgobject-2.0.so.0', 'libgobject-2.0-0.dll'),
    'glib': ('libglib-2.0.so.0', 'libglib-2.0-0.dll'), 'gio': ('libgio-2.0.so.0', 'libgio-2.0-0.dll'),
    'pixbuf': ('libgdk_pixbuf-2.0.so.0', 'libgdk_pixbuf-2.0-0.dll'), 'cairo': ('libcairo.so.2', 'libcairo-2.dll'),
}
_DLL_DIRECTORIES = []


# ==========================================
# Function: Resolve native GTK artifacts without searching the caller's working directory.
# Method: Use fixed Linux SONAMEs or explicit paths under the portable Windows application.
# ==========================================
def library_path(name, windows=None):
    windows = sys.platform == 'win32' if windows is None else windows
    filename = LIBRARIES[name][int(windows)]
    return str(Path(__file__).resolve().parents[1] / '.runtime/gtk/bin' / filename) if windows else filename


# ==========================================
# Function: Make application-private Windows DLLs visible to ctypes and native GTK modules.
# Method: Retain the AddDllDirectory cookie for the process and use memory-backed settings without D-Bus autolaunch.
# ==========================================
def prepare_windows():
    if sys.platform != 'win32' or _DLL_DIRECTORIES:
        return
    root = Path(__file__).resolve().parents[1] / '.runtime/gtk'
    _DLL_DIRECTORIES.append(os.add_dll_directory(str(root / 'bin')))
    os.environ.update({'GTK_DATA_PREFIX': str(root), 'GSETTINGS_SCHEMA_DIR': str(root / 'share/glib-2.0/schemas'),
                       'GSETTINGS_BACKEND': 'memory', 'DBUS_SESSION_BUS_ADDRESS': 'disabled:', 'GDK_BACKEND': 'win32'})
    user = C.WinDLL('user32', use_last_error=True)
    if hasattr(user, 'SetProcessDpiAwarenessContext'):
        function = user.SetProcessDpiAwarenessContext
        function.argtypes, function.restype = [C.c_void_p], C.c_int
        function(C.c_void_p(-4))


# ==========================================
# Function: Load the finite GTK/Cairo C ABI without requiring PyGObject or a system-wide GTK install.
# Method: Configure Windows DLL dependencies once and load the selected absolute library path using cdecl.
# ==========================================
def load_library(name):
    prepare_windows()
    return C.CDLL(library_path(name))
