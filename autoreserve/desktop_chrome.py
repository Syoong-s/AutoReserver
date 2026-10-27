"""Client-drawn window controls with managed X11 taskbar/minimize behavior."""

import ctypes
import tkinter as tk


# ==========================================
# Function: Remove legacy server decorations and provide dependable window gestures.
# Method: Keep a managed X11 window, apply Motif decoration hints, and draw resize hit regions.
# ==========================================
class WindowChrome:
    # ==========================================
    # Function: Build a compact client title bar and eight resizing grips.
    # Method: Draw title text in Tk so CJK fonts and encoding match the rest of the client.
    # ==========================================
    def __init__(self, root, close, title="预约工作台"):
        self.root, self.drag, self.pending, self.job = root, None, None, None
        self.maximized, self.normal_geometry = False, None
        root.title("AutoReserve")
        self.bar = tk.Frame(root, bg="#f5f5f4", height=42)
        self.bar.pack(side="top", fill="x")
        self.bar.pack_propagate(False)
        self.title = tk.Label(self.bar, text="AutoReserve  /  " + title, font=("sans", 10), bg="#f5f5f4", fg="#656970", padx=22)
        self.title.pack(side="left", fill="y")
        for caption, command in [("×", close), ("□", self.maximize), ("−", self.minimize)]:
            control = tk.Label(self.bar, text=caption, font=("sans", 15), width=4, bg="#f5f5f4", fg="#60646c", cursor="hand2", takefocus=True)
            control.pack(side="right", fill="y")
            control.bind("<Button-1>", lambda e, fn=command: fn())
            control.bind("<Return>", lambda e, fn=command: fn())
            control.bind("<Enter>", lambda e, w=control: w.configure(bg="#e4e5e8"))
            control.bind("<Leave>", lambda e, w=control: w.configure(bg="#f5f5f4"))
        for widget in (self.bar, self.title):
            self.bind_drag(widget, "move")
            widget.bind("<Double-Button-1>", lambda e: self.maximize())
        self.grips = {}
        for edge, cursor, placement in [
            ("n", "top_side", dict(relx=0, rely=0, relwidth=1, height=7)),
            ("s", "bottom_side", dict(relx=0, rely=1, relwidth=1, height=7, anchor="sw")),
            ("w", "left_side", dict(relx=0, rely=0, width=7, relheight=1)),
            ("e", "right_side", dict(relx=1, rely=0, width=7, relheight=1, anchor="ne")),
            ("nw", "top_left_corner", dict(relx=0, rely=0, width=14, height=14)),
            ("ne", "top_right_corner", dict(relx=1, rely=0, width=14, height=14, anchor="ne")),
            ("sw", "bottom_left_corner", dict(relx=0, rely=1, width=14, height=14, anchor="sw")),
            ("se", "bottom_right_corner", dict(relx=1, rely=1, width=14, height=14, anchor="se")),
        ]:
            grip = tk.Frame(root, bg="#f5f5f4", cursor=cursor)
            grip.place(**placement)
            self.bind_drag(grip, edge)
            self.grips[edge] = grip
        root.bind("<Map>", self.mapped, add="+")
        root.bind("<Configure>", self.raise_grips, add="+")
        root.bind("<Alt-F4>", lambda e: close())
        self.remove_decorations()

    # ==========================================
    # Function: Remove the old native title bar without removing taskbar integration.
    # Method: Set managed-window Motif hints through the existing system X11 library.
    # ==========================================
    def remove_decorations(self):
        if self.root.tk.call("tk", "windowingsystem") != "x11":
            return
        self.root.update_idletasks()
        lib = ctypes.CDLL("libX11.so.6")
        lib.XOpenDisplay.argtypes, lib.XOpenDisplay.restype = [ctypes.c_char_p], ctypes.c_void_p
        lib.XInternAtom.argtypes, lib.XInternAtom.restype = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int], ctypes.c_ulong
        lib.XChangeProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        lib.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
                                  ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)),
                                  ctypes.POINTER(ctypes.c_uint)]
        lib.XFree.argtypes = [ctypes.c_void_p]
        lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
        display = lib.XOpenDisplay(None)
        if not display:
            return
        try:
            tree_root, parent = ctypes.c_ulong(), ctypes.c_ulong()
            children, count = ctypes.POINTER(ctypes.c_ulong)(), ctypes.c_uint()
            if not lib.XQueryTree(display, self.root.winfo_id(), ctypes.byref(tree_root), ctypes.byref(parent),
                                  ctypes.byref(children), ctypes.byref(count)):
                return
            if children:
                lib.XFree(children)
            # Tk's immediate parent is its client wrapper; wm frame may instead be a WM-owned decoration.
            window = self.x11_client = parent.value
            atom = lib.XInternAtom(display, b"_MOTIF_WM_HINTS", False)
            hints = (ctypes.c_ulong * 5)(2, 0, 0, 0, 0)
            lib.XChangeProperty(display, window, atom, atom, 32, 0, hints, 5)
        finally:
            lib.XCloseDisplay(display)

    # ==========================================
    # Function: Retain decoration hints after the window manager remaps the client.
    # Method: Apply hints on the root Map event only.
    # ==========================================
    def mapped(self, event):
        if event.widget is self.root:
            self.remove_decorations()
            self.raise_grips()

    # ==========================================
    # Function: Keep invisible resize regions above later-created content widgets.
    # Method: Raise the eight retained grips after layout, preserving corner priority.
    # ==========================================
    def raise_grips(self, event=None):
        if event is None or event.widget is self.root:
            for grip in self.grips.values():
                grip.lift()

    # ==========================================
    # Function: Minimize through the normal desktop window manager.
    # Method: Preserve a managed window instead of using override-redirect or hiding it permanently.
    # ==========================================
    def minimize(self):
        self.root.iconify()

    # ==========================================
    # Function: Toggle maximized workspace size from the title bar.
    # Method: Ask the window manager to respect the monitor work area and keep restore geometry.
    # ==========================================
    def maximize(self):
        was_zoomed = bool(self.root.attributes("-zoomed"))
        if not was_zoomed:
            self.normal_geometry = self.root.geometry()
        self.maximized = not was_zoomed
        self.root.attributes("-zoomed", self.maximized)
        if not self.maximized and self.normal_geometry:
            self.root.geometry(self.normal_geometry)

    # ==========================================
    # Function: Route pointer drags from the title and resize grips.
    # Method: Bind press, motion, and release to one consistent geometry controller.
    # ==========================================
    def bind_drag(self, widget, edge):
        widget.bind("<ButtonPress-1>", lambda e: self.begin(e, edge))
        widget.bind("<B1-Motion>", self.motion)
        widget.bind("<ButtonRelease-1>", self.end)

    # ==========================================
    # Function: Capture a reliable pointer baseline before dragging.
    # Method: Use root coordinates and an explicit local pointer grab until release.
    # ==========================================
    def begin(self, event, edge):
        if self.root.attributes("-zoomed"):
            return
        self.drag = (edge, event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y(), self.root.winfo_width(), self.root.winfo_height())
        event.widget.grab_set()

    # ==========================================
    # Function: Compute movement or eight-direction resizing without lost pointer events.
    # Method: Anchor opposite edges, clamp minimum size, and coalesce geometry writes per idle frame.
    # ==========================================
    def motion(self, event):
        if not self.drag:
            return
        edge, px, py, x, y, w, h = self.drag
        dx, dy = event.x_root - px, event.y_root - py
        mw, mh = self.root.minsize()
        if edge == "move":
            x, y = x + dx, y + dy
        else:
            if "e" in edge:
                w = max(mw, w + dx)
            if "s" in edge:
                h = max(mh, h + dy)
            if "w" in edge:
                new = max(mw, w - dx)
                x, w = x + w - new, new
            if "n" in edge:
                new = max(mh, h - dy)
                y, h = y + h - new, new
        self.pending = f"{w}x{h}+{x}+{y}"
        if self.job is None:
            self.job = self.root.after_idle(self.flush)

    # ==========================================
    # Function: Apply the latest drag geometry at most once per idle batch.
    # Method: Retain the newest pointer position when many motion events arrive together.
    # ==========================================
    def flush(self):
        if self.job is not None:
            self.root.after_cancel(self.job)
        self.job = None
        if self.pending:
            self.root.geometry(self.pending)
            self.pending = None

    # ==========================================
    # Function: Complete a window gesture and release pointer ownership.
    # Method: Flush the last motion before releasing the local grab.
    # ==========================================
    def end(self, event):
        if self.drag:
            self.motion(event)
            self.flush()
            event.widget.grab_release()
            self.drag = None
