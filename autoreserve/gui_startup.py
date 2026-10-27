"""Native window presentation and read-only WSLg startup diagnostics."""

from pathlib import Path
import re


# ==========================================
# Function: Detect a broken WSLg rendering backend before silently waiting in Tk.
# Method: Inspect only the latest bounded log tail and prefer the newest gfxredir state.
# ==========================================
def wslg_render_warning(log_path=Path("/mnt/wslg/weston.log")):
    try:
        with Path(log_path).open("rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - 2 * 1024 * 1024))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    modes = list(re.finditer(r"RDP backend: use_gfxredir\s*=\s*([01])", text))
    if not modes or modes[-1].group(1) != "0":
        return None
    return ("检测到 WSLg 处于 COPY MODE（use_gfxredir=0），Windows 可能无法显示窗口或显示很慢。"
            "这是图形服务状态，程序不会自动重启 WSLg 或关闭其他图形窗口。"
            "请保存其他 Linux 图形程序的工作后，重启 WSLg 图形服务，再启动客户端。")


# ==========================================
# Function: Make a fully built native window visible and bring it forward once.
# Method: Register a real Map confirmation, deiconify, briefly raise, then release topmost state.
# ==========================================
def present_window(root, ready=None):
    from tkinter import TclError
    reported = False
    previous_topmost = root.attributes("-topmost")

    # ==========================================
    # Function: Confirm actual Tk window mapping without claiming host rendering was verified.
    # Method: Report only the root Map event and avoid duplicate notifications from its children.
    # ==========================================
    def mapped(event):
        nonlocal reported
        if event.widget is root and not reported:
            reported = True
            if ready:
                ready()

    # ==========================================
    # Function: Return normal stacking behavior after the brief startup foreground request.
    # Method: Restore the prior topmost setting, tolerating a window closed during startup.
    # ==========================================
    def release():
        try:
            root.attributes("-topmost", previous_topmost)
        except TclError:
            pass

    root.bind("<Map>", mapped, add="+")
    root.update_idletasks()
    root.deiconify()
    root.lift()
    root.attributes("-topmost", True)
    root.focus_force()
    root.after(400, release)
