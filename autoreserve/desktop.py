"""Modern, incremental native reservation workspace; no web rendering or UI polling I/O."""

from dataclasses import asdict
from datetime import datetime, timedelta
import queue
import time
from zoneinfo import ZoneInfo

from .catalog import ANY_COURT, booking_url
from .config import BookingContext, Target, load_config
from .desktop_controller import DesktopController
from .desktop_model import DesktopModel
from .desktop_events import EventWakeup

STATUS = {"pending": "等待空位", "submitted": "已下单", "preview": "已预览", "expired": "日期已过",
          "unknown": "需核对订单", "needs_verification": "需人工验证", "conflict": "需核对候选", "rejected": "继续监测"}
HOURS = [f"{h:02d}:00-{h+1:02d}:00" for h in range(7, 22)]
BG, SIDE, INK, MUTED, LINE = "#ffffff", "#f5f5f4", "#202020", "#777777", "#ececeb"


# ==========================================
# Function: Draw compact rounded native buttons without an additional GUI framework.
# Method: Create one small Canvas and repaint only its own hover/focus/disabled state.
# ==========================================
def rounded_button(parent, text, command, primary=False, width=124):
    import tkinter as tk

    # ==========================================
    # Function: Implement keyboard-accessible, flat native action controls.
    # Method: Keep canvas item count constant and expose ttk-compatible state/invoke methods.
    # ==========================================
    class Button(tk.Canvas):
        # ==========================================
        # Function: Bind local pointer and keyboard interaction.
        # Method: Reuse a single background polygon and text item for all state changes.
        # ==========================================
        def __init__(self):
            super().__init__(parent, width=width, height=36, highlightthickness=0,
                             bg=parent.cget("background"), bd=0, takefocus=1, cursor="hand2")
            self.disabled, self.hover, self.focused = False, False, False
            self._painted = None
            self.shape = self.create_polygon(0, 0, 1, 1, smooth=True, splinesteps=16, outline="")
            self.caption = self.create_text(0, 0, text=text, font=("sans", 10, "bold" if primary else "normal"))
            self.bind("<Configure>", self.draw)
            self.bind("<Enter>", lambda event: self.highlight(True))
            self.bind("<Leave>", lambda event: self.highlight(False))
            self.bind("<ButtonRelease-1>", lambda event: self.invoke())
            self.bind("<Return>", lambda event: self.invoke())
            self.bind("<space>", lambda event: self.invoke())
            self.bind("<FocusIn>", lambda event: self.focus_changed(True))
            self.bind("<FocusOut>", lambda event: self.focus_changed(False))
            self.draw()

        # ==========================================
        # Function: Update only this button's rounded geometry and colors.
        # Method: Reposition existing items instead of allocating new view widgets.
        # ==========================================
        def draw(self, event=None):
            w, h, r = max(self.winfo_width(), width), 36, 9
            signature = (w, self.disabled, self.hover, self.focused)
            if signature == self._painted:
                return
            self._painted = signature
            self.coords(self.shape, r, 1, w-r, 1, w-1, 1, w-1, r, w-1, h-r,
                        w-1, h-1, w-r, h-1, r, h-1, 1, h-1, 1, h-r, 1, r, 1, 1)
            fill = "#dededd" if self.disabled else ("#3b3b3b" if self.hover else INK) if primary else ("#e5e5e3" if self.hover else "#f1f1ef")
            self.itemconfigure(self.shape, fill=fill, outline="#b3b3b0" if self.focused else "")
            self.coords(self.caption, w/2, h/2)
            self.itemconfigure(self.caption, fill="#a1a1a1" if self.disabled else "white" if primary else INK)

        # ==========================================
        # Function: Track focus locally without a synchronous X11 focus query.
        # Method: Use delivered focus events and repaint only changed visual state.
        # ==========================================
        def focus_changed(self, active):
            self.focused = active
            self.draw()

        # ==========================================
        # Function: Render pointer feedback without touching the rest of the interface.
        # Method: Change a local color flag and redraw two canvas items.
        # ==========================================
        def highlight(self, active):
            self.hover = active
            self.draw()

        # ==========================================
        # Function: Execute an enabled action from pointer or keyboard input.
        # Method: Preserve the same callback path used by native widget tests.
        # ==========================================
        def invoke(self):
            if not self.disabled:
                return command()

        # ==========================================
        # Function: Support the shared busy-state controller.
        # Method: Translate ttk state flags into one disabled property.
        # ==========================================
        def state(self, flags=None):
            if flags and ("disabled" in flags) != self.disabled:
                self.disabled = "disabled" in flags
                self.configure(cursor="arrow" if self.disabled else "hand2")
                self.draw()
            return ("disabled",) if self.disabled else ()

        # ==========================================
        # Function: Update a retained button caption after a mode switch.
        # Method: Reuse the existing canvas text item without rebuilding the control.
        # ==========================================
        def set_text(self, value):
            self.itemconfigure(self.caption, text=value)

    return Button()


# ==========================================
# Function: Present cross-venue goals in a minimal sidebar-and-cards desktop workspace.
# Method: Reuse editor validation and draw only changed status rows and visible content.
# ==========================================
class DesktopApp(DesktopModel):
    # ==========================================
    # Function: Restore the local workspace without browsing or querying inventory.
    # Method: Build persistent native widgets once and process a bounded event queue.
    # ==========================================
    def __init__(self, root, controller, auto_catalog=True):
        import tkinter as tk
        from tkinter import ttk, messagebox
        from .desktop_widgets import ChoiceGrid, GoalList, ScrollPanel
        from .desktop_chrome import WindowChrome
        self.choice_type, self.goal_type, self.scroll_type = ChoiceGrid, GoalList, ScrollPanel
        self.chrome_type = WindowChrome
        self.tk, self.ttk, self.dialogs = tk, ttk, messagebox
        self.root, self.controller = root, controller
        self.data = asdict(load_config(controller.config_path))
        self.selected_goal, self.goal_states, self.current_snapshot = None, {}, None
        self.dirty = self.closing = self.loading_editor = self.saving = False
        self._busy_signature, self._editor_signature = None, None
        self._goal_rows = {}
        self.next_read_at, self.after_id = None, None
        self.builder_contexts, self.completions = [], []
        self.builder_drafts = {}
        self.completion_window, self.settings_window = None, None
        self.activity_visible, self.pending_snapshot = False, None
        self._last_countdown, self._shown_notice_ids = None, set()
        self.action_widgets = []
        root.title("AutoReserve · 预约助手")
        root.geometry("1360x930")
        root.minsize(1120, 780)
        root.configure(bg=BG)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.style()
        self.chrome = self.chrome_type(root, self.close)
        self.build()
        self.chrome.raise_grips()
        root.bind("<Configure>", self.resize, add="+")
        self._load_settings()
        self.render_catalog()
        self.render_goals()
        self.auth_text.set(controller.auth_status)
        self.select_goal(self.workspace_items()[0][0] if self.workspace_items() else None)
        self._update_record_states()
        for variable in (self.goal_name, self.goal_date, self.start_at, self.interval, self.jitter,
                         self.backoff, self.price, self.max_poll, self.until_complete, self.show_browser):
            variable.trace_add("write", self.mark_dirty)
        self.event_pump = EventWakeup(root, self.drain_events)
        self.controller.event_wakeup = self.event_pump.notify
        self.after_id = root.after(1000, self.drain_events)
        self.event_pump.notify()
        if auto_catalog and controller.catalog is None and (controller.auth_dir / "session.json").exists():
            root.after(250, lambda: self.action("catalog", save=False))

    # ==========================================
    # Function: Apply a coherent flat palette and modern typography to native controls.
    # Method: Remove default 3D borders, table grids, and dark selection blocks.
    # ==========================================
    def style(self):
        s = self.ttk.Style(self.root)
        s.theme_use("clam")
        s.configure("TFrame", background=BG)
        s.configure("TLabel", background=BG, foreground=INK, font=("sans", 10))
        s.configure("TCombobox", fieldbackground="#f7f7f6", background="#f7f7f6", bordercolor="#f7f7f6",
                    lightcolor="#f7f7f6", darkcolor="#f7f7f6", arrowcolor=MUTED, padding=(10, 7), relief="flat", borderwidth=0)
        s.map("TCombobox", fieldbackground=[("readonly", "#f7f7f6")], selectbackground=[("readonly", "#f7f7f6")], selectforeground=[("readonly", INK)])
        s.layout("TCombobox", [("Combobox.field", {"sticky": "nswe", "children": [
            ("Combobox.downarrow", {"side": "right", "sticky": "ns"}),
            ("Combobox.padding", {"sticky": "nswe", "children": [("Combobox.textarea", {"sticky": "nswe"})]})]})])
        s.configure("Vertical.TScrollbar", background="#d5d5d2", troughcolor=BG, borderwidth=0,
                    relief="flat", arrowsize=10, width=10, lightcolor=BG, darkcolor=BG, bordercolor=BG, gripcount=0)
        s.layout("Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
            ("Vertical.Scrollbar.thumb", {"sticky": "nswe", "expand": "1"})]})])
        s.map("Vertical.TScrollbar", background=[("active", "#bcbcb8"), ("disabled", BG)])
        s.configure("TCheckbutton", background=BG, foreground=MUTED, font=("sans", 10))
        s.configure("Treeview", background=BG, fieldbackground=BG, foreground=INK, borderwidth=0,
                    relief="flat", rowheight=42, font=("sans", 10))
        s.map("Treeview", background=[("selected", "#dbeafe")], foreground=[("selected", "#1d4ed8")])
        s.configure("Treeview.Heading", background=BG, foreground=MUTED, font=("sans", 9), relief="flat", padding=(8, 9))
        s.configure("Sidebar.Treeview", background=SIDE, fieldbackground=SIDE, rowheight=64, font=("sans", 10))
        s.configure("TNotebook", background=BG, borderwidth=0, bordercolor=BG, lightcolor=BG, darkcolor=BG, relief="flat")
        s.configure("TNotebook.Tab", background=BG, padding=(14, 7), font=("sans", 9), borderwidth=0, bordercolor=BG, lightcolor=BG, darkcolor=BG)
        s.map("TNotebook.Tab", background=[("selected", "#f1f1ef")])
        s.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        self.root.option_add("*TCombobox*Listbox.font", ("sans", 10))

    # ==========================================
    # Function: Create consistent borderless text labels.
    # Method: Use explicit parent colors so sidebar and content remain visually distinct.
    # ==========================================
    def label(self, parent, text=None, variable=None, size=10, color=INK, bold=False):
        return self.tk.Label(parent, text=text, textvariable=variable, bg=parent.cget("background"),
                             fg=color, font=("sans", size, "bold" if bold else "normal"), anchor="w", bd=0)

    # ==========================================
    # Function: Register a compact native action for busy-state updates.
    # Method: Share rounded controls across toolbar, sidebar, and dialogs.
    # ==========================================
    def button(self, parent, text, command, primary=False, width=124):
        widget = rounded_button(parent, text, command, primary, width)
        self.action_widgets.append(widget)
        return widget

    # ==========================================
    # Function: Create a flat keyboard-editable field.
    # Method: Use subtle focus borders and keep all settings in shared Tk variables.
    # ==========================================
    def entry(self, parent, variable, width=20):
        widget = self.tk.Entry(parent, textvariable=variable, width=width, font=("sans", 11),
                                bg="#f7f7f6", fg=INK, relief="flat", bd=6, insertbackground=INK,
                                highlightthickness=0, disabledbackground="#f7f7f6", disabledforeground=MUTED)
        self.action_widgets.append(widget)
        return widget

    # ==========================================
    # Function: Build one sidebar and an uncluttered editing canvas.
    # Method: Move secondary settings/activity into on-demand panels and keep primary actions visible.
    # ==========================================
    def build(self):
        tk, ttk = self.tk, self.ttk
        side = tk.Frame(self.root, bg=SIDE, width=286)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        self.label(side, "◈  AutoReserve", size=16, bold=True).pack(fill="x", padx=22, pady=(27, 5))
        self.label(side, "你的场馆预约助手", color=MUTED).pack(fill="x", padx=25, pady=(0, 24))
        self.button(side, "＋  新建预约目标", self.add_goal, width=210).pack(fill="x", padx=20, pady=(0, 5))
        self.label(side, "预约目标", size=9, color=MUTED).pack(fill="x", padx=24, pady=(0, 8))
        self.goal_tree = self.goal_type(side, self.copy_next_day, self.delete_goal)
        self.goal_tree.pack(fill="both", expand=True, padx=10)
        self.goal_tree.bind("<<TreeviewSelect>>", self.goal_selected)
        reorder = tk.Frame(side, bg=SIDE)
        reorder.pack(fill="x", padx=20, pady=8)
        self.button(reorder, "↑", lambda: self.move_goal(-1), width=46).pack(side="left", padx=(0, 5))
        self.button(reorder, "↓", lambda: self.move_goal(1), width=46).pack(side="left")
        self.label(reorder, "+ 下一天   × 删除", size=9, color=MUTED).pack(side="right")
        tk.Frame(side, bg="#e6e6e2", height=1).pack(fill="x", padx=20, pady=15)
        self.button(side, "⚙  执行设置", self.open_settings, width=210).pack(fill="x", padx=20, pady=3)
        self.button(side, "执行记录与空位", self.toggle_activity, width=210).pack(fill="x", padx=20, pady=3)
        self.auth_text = tk.StringVar()
        self.label(side, variable=self.auth_text, color=MUTED, size=9).pack(fill="x", padx=23, pady=(19, 7))
        self.login_button = self.button(side, "登录 / 重新登录", lambda: self.action("login", save=False), width=210)
        self.login_button.pack(fill="x", padx=20, pady=(0, 20))
        outer = tk.Frame(self.root, bg=BG, padx=26, pady=18)
        outer.pack(side="left", fill="both", expand=True)
        main = outer
        toolbar = tk.Frame(main, bg=BG)
        toolbar.pack(fill="x", pady=(0, 5))
        self.label(toolbar, "预约工作台", size=21, bold=True).pack(side="left")
        self.button(toolbar, "保存设置", lambda: self.save(background=True), width=100).pack(side="right", padx=(8, 0))
        self.start_button = self.button(toolbar, "开始监测  →", lambda: self.action("run"), primary=True, width=150)
        self.start_button.pack(side="right")
        self.capture_button = rounded_button(toolbar, "保存登录", self.capture, True, 120)
        self.stop_button = rounded_button(toolbar, "停止监测", self.controller.stop, False, 110)
        self.summary_text = tk.StringVar()
        self.label(main, variable=self.summary_text, color=MUTED, size=9).pack(fill="x", pady=(7, 22))
        self.editor_scroll = self.scroll_type(outer)
        self.editor_scroll.pack(fill="both", expand=True)
        self.workspace_card = tk.Frame(self.editor_scroll.body, bg=BG, padx=18, pady=15,
                                       highlightbackground="#e9e9e7", highlightthickness=1)
        self.workspace_card.pack(fill="both", expand=True, padx=(0, 6), pady=(0, 12))
        main = self.workspace_card
        modebar = tk.Frame(main, bg=BG)
        modebar.pack(fill="x", pady=(0, 14))
        self.mode_text = tk.StringVar(value="普通目标 · 单次预约")
        self.label(modebar, variable=self.mode_text, color="#2563eb", size=10, bold=True).pack(side="left")
        self.repeat_button = self.button(modebar, "设为重复目标", self.toggle_repeat, width=152)
        self.repeat_button.pack(side="right")
        self.meta = meta = tk.Frame(main, bg=BG)
        meta.pack(fill="x")
        namebox, datebox = tk.Frame(meta, bg=BG), tk.Frame(meta, bg=BG)
        namebox.pack(side="left", fill="x", expand=True, padx=(0, 14))
        datebox.pack(side="left")
        self.goal_name, self.goal_date = tk.StringVar(), tk.StringVar()
        self.label(namebox, "目标名称", size=9, color=MUTED).pack(anchor="w", pady=(0, 7))
        self.name_entry = self.entry(namebox, self.goal_name)
        self.name_entry.pack(fill="x")
        self.date_caption = tk.StringVar(value="预约日期")
        self.label(datebox, variable=self.date_caption, size=9, color=MUTED).pack(anchor="w", pady=(0, 7))
        self.date_entry = self.entry(datebox, self.goal_date, 18)
        self.date_entry.pack()
        self.schedule_panel = tk.Frame(main, bg="#f5f9ff", padx=14, pady=12)
        schedule = self.schedule_panel
        self.repeat_at, self.repeat_ahead = tk.StringVar(value="12:00"), tk.StringVar(value="2")
        self.repeat_enabled = tk.BooleanVar(value=True)
        self.week_vars = [tk.BooleanVar(value=True) for _ in range(7)]
        self.week_buttons, self._last_weekday = [], 0
        schedule_top = tk.Frame(schedule, bg="#f5f9ff")
        schedule_top.pack(fill="x")
        self.label(schedule_top, "自动预约日", color="#2563eb", bold=True).pack(side="left")
        check = ttk.Checkbutton(schedule_top, text="开启重复预约", variable=self.repeat_enabled, command=self.schedule_changed)
        check.pack(side="right")
        self.action_widgets.append(check)
        daysbar = tk.Frame(schedule, bg="#f5f9ff")
        daysbar.pack(fill="x", pady=(10, 12))
        for day, caption in enumerate(["周一", "周二", "周三", "周四", "周五", "周六", "周日"]):
            button = tk.Label(daysbar, text=caption, font=("sans", 10), padx=12, pady=7,
                              bg="#2563eb", fg="white", cursor="hand2", takefocus=True)
            button.pack(side="left", padx=(0, 6))
            button.bind("<Button-1>", lambda e, d=day: self.toggle_weekday(d))
            button.bind("<space>", lambda e, d=day: self.toggle_weekday(d))
            button.bind("<Return>", lambda e, d=day: self.toggle_weekday(d))
            self.week_buttons.append(button)
        timing = tk.Frame(schedule, bg="#f5f9ff")
        timing.pack(fill="x")
        self.label(timing, "执行时间", color=MUTED, size=9).pack(side="left", padx=(0, 8))
        self.entry(timing, self.repeat_at, 7).pack(side="left")
        self.label(timing, "预约往后", color=MUTED, size=9).pack(side="left", padx=(20, 8))
        self.ahead_combo = ttk.Combobox(timing, textvariable=self.repeat_ahead, values=list(range(8)), state="readonly", width=3)
        self.ahead_combo.pack(side="left")
        self.ahead_combo.bind("<<ComboboxSelected>>", self.schedule_changed)
        self.label(timing, "天 · 北京时间", color=MUTED, size=9).pack(side="left", padx=8)
        self.repeat_at.trace_add("write", self.schedule_changed)
        self.schedule_hint = tk.StringVar()
        self.label(schedule, variable=self.schedule_hint, size=9, color=MUTED).pack(fill="x", pady=(10, 0))
        candidate_header = tk.Frame(main, bg=BG)
        candidate_header.pack(fill="x", pady=(23, 8))
        self.label(candidate_header, "场馆备选", size=13, bold=True).pack(side="left")
        self.label(candidate_header, "按顺序匹配 · 任意一个下单即完成目标", size=9, color=MUTED).pack(side="left", padx=15)
        candidate_list = tk.Frame(main, bg=BG)
        candidate_list.pack(fill="x")
        self.candidate_tree = ttk.Treeview(candidate_list, columns=("venue", "court", "slots"), show="tree headings", height=4, selectmode="browse")
        for column, title, width in [("#0", "优先级", 58), ("venue", "场馆 / 项目", 240), ("court", "具体场地", 155), ("slots", "时段", 225)]:
            self.candidate_tree.heading(column, text=title, anchor="w")
            self.candidate_tree.column(column, width=width, minwidth=45, stretch=column != "#0")
        self.candidate_tree.pack(side="left", fill="x", expand=True)
        candidate_scroll = ttk.Scrollbar(candidate_list, command=self.candidate_tree.yview)
        candidate_scroll.pack(side="right", fill="y")
        self.candidate_tree.configure(yscrollcommand=candidate_scroll.set)
        commands = tk.Frame(main, bg=BG)
        commands.pack(fill="x", pady=(7, 14))
        self.button(commands, "↑ 提高优先级", lambda: self.move_candidate(-1), width=120).pack(side="left", padx=(0, 7))
        self.button(commands, "↓", lambda: self.move_candidate(1), width=44).pack(side="left")
        self.button(commands, "移除备选", self.delete_candidate, width=95).pack(side="right")
        tk.Frame(main, bg=LINE, height=1).pack(fill="x", pady=(0, 17))
        self.label(main, "添加场馆与时段", size=13, bold=True).pack(anchor="w")
        self.catalog_text = tk.StringVar(value="登录并保存后，一次性读取场馆目录；编辑目标不查询空位。")
        self.label(main, variable=self.catalog_text, size=9, color=MUTED).pack(fill="x", pady=(5, 10))
        context = tk.Frame(main, bg=BG)
        context.pack(fill="x")
        self.venue_var, self.sport_var = tk.StringVar(), tk.StringVar()
        self.venue_combo = ttk.Combobox(context, textvariable=self.venue_var, state="readonly", width=34)
        self.venue_combo.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.venue_combo.bind("<<ComboboxSelected>>", self.venue_changed)
        self.sport_combo = ttk.Combobox(context, textvariable=self.sport_var, state="readonly", width=17)
        self.sport_combo.pack(side="left", padx=(0, 8))
        self.sport_combo.bind("<<ComboboxSelected>>", self.sport_changed)
        self.button(context, "＋ 加入场馆", self.add_venue, width=115).pack(side="left")
        self.venue_tags = tk.Frame(main, bg=BG)
        self.venue_tags.bind("<Configure>", self.wrap_venue_tags)
        self.venue_tags.pack(fill="x", pady=(6, 4))
        choices = tk.Frame(main, bg=BG)
        choices.pack(fill="x")
        courtbox, slotbox = tk.Frame(choices, bg=BG), tk.Frame(choices, bg=BG)
        courtbox.pack(side="left", fill="both", expand=True, padx=(0, 14))
        slotbox.pack(side="left", fill="both", expand=True)
        for box, title in [(courtbox, "具体场地 · 点击多选"), (slotbox, "时段 · 点击多选")]:
            self.label(box, title, color=MUTED, size=9).pack(anchor="w", pady=(4, 5))
        self.court_list = self.choice_type(courtbox, columns=2, height=4)
        self.court_list.pack(fill="both", expand=True)
        self.hour_list = self.choice_type(slotbox, columns=2, height=4)
        self.hour_list.pack(fill="both", expand=True)
        self.selection_text = tk.StringVar()
        for widget in (self.court_list, self.hour_list):
            widget.bind("<<ListboxSelect>>", self.selection_changed)
        for slot in HOURS:
            self.hour_list.insert("end", slot)
        self.hour_list.selection_set(7)
        self.hour_list.see(7)
        addbar = tk.Frame(main, bg=BG)
        addbar.pack(fill="x", pady=(12, 8))
        self.bundle = tk.BooleanVar(value=False)
        self.bundle_check = ttk.Checkbutton(addbar, text="连续时段合并下单", variable=self.bundle)
        self.bundle_check.pack(side="left")
        self.action_widgets.append(self.bundle_check)
        self.button(addbar, "添加到当前目标  ＋", self.add_candidates, primary=True, width=190).pack(side="right")
        self.editor_hint = tk.StringVar(value="同一目标可加入多个场馆；未监测过的项目可选“任意可用场地”。")
        self.label(main, variable=self.selection_text, color="#2563eb", size=9).pack(fill="x", pady=(0, 7))
        self.label(main, variable=self.editor_hint, color=MUTED, size=9).pack(fill="x", pady=(0, 7))
        self.status_text = tk.StringVar(value="就绪 · 编辑和选择场馆不读取空位")
        self.label(outer, variable=self.status_text, color="#526a5b", size=9).pack(fill="x", side="bottom", pady=(7, 4))
        self.activity = tk.Frame(self.editor_scroll.body, bg=BG)
        notebook = ttk.Notebook(self.activity, height=90)
        notebook.pack(fill="both", expand=True)
        logs, inventory = tk.Frame(notebook, bg=BG), tk.Frame(notebook, bg=BG)
        notebook.add(logs, text="执行记录")
        notebook.add(inventory, text="监测空位")
        self.log_text = tk.Text(logs, height=4, state="disabled", wrap="word", font=("sans", 9), bg=BG, fg=MUTED, relief="flat", bd=4)
        self.log_text.pack(fill="both", expand=True)
        self.inventory_tree = ttk.Treeview(inventory, show="headings", height=3)
        self.inventory_tree.pack(fill="both", expand=True)
        self.inventory_text = tk.StringVar(value="尚未启动监测，未读取空位")
        self.label(inventory, variable=self.inventory_text, size=9, color=MUTED).pack(anchor="w")
        self.start_at, self.interval, self.jitter, self.backoff, self.price, self.max_poll = (tk.StringVar() for _ in range(6))
        self.until_complete, self.show_browser = tk.BooleanVar(), tk.BooleanVar()

    # ==========================================
    # Function: Keep primary controls visible on smaller laptop displays.
    # Method: Adjust only list heights when crossing a height threshold, not on each resize tick.
    # ==========================================
    def resize(self, event):
        if event.widget != self.root:
            return
        compact = event.height < 850
        if compact != getattr(self, "_compact", None):
            self._compact = compact
            self.candidate_tree.configure(height=min(3 if compact else 4, max(1, len(getattr(self, "_candidate_view", [])))))
            for widget in (self.court_list, self.hour_list):
                widget.configure(height=3 if compact else 4)

    # ==========================================
    # Function: Populate cached catalog selectors without changing any target or default scope.
    # Method: Keep directory selection as a candidate builder, not a workspace switch.
    # ==========================================
    def render_catalog(self):
        venues = (self.controller.catalog or {}).get("venues", [])
        self.venues_by_label = {f"{v['name']} · {v['campus']}": v for v in venues}
        self.venue_combo["values"] = list(self.venues_by_label)
        if self.venue_var.get() not in self.venues_by_label and venues:
            selected = next((label for label, v in self.venues_by_label.items() if v["name"] == self.data["venue"]), next(iter(self.venues_by_label)))
            self.venue_var.set(selected)
        venue = self.venues_by_label.get(self.venue_var.get())
        if venue:
            names = [s["name"] for s in venue["sports"]]
            self.sport_combo["values"] = names
            if self.sport_var.get() not in names:
                self.sport_var.set(self.data["sport"] if self.data["sport"] in names else names[0] if names else "")
        if venues:
            self.catalog_text.set(f"目录已缓存 · {len(venues)} 个场馆 / {sum(len(v['sports']) for v in venues)} 个项目 · 不查询实时空位")
        self.update_summary()

    # ==========================================
    # Function: Resolve the builder's currently selected cached project in memory.
    # Method: Generate an exact route without reading config files or fetching the website.
    # ==========================================
    def builder_context(self):
        venue = self.venues_by_label.get(self.venue_var.get())
        if venue is None or self.sport_var.get() not in {s["name"] for s in venue["sports"]}:
            raise ValueError("请先登录并从缓存目录中选择场馆和项目")
        return BookingContext(booking_url(venue["id"], self.sport_var.get()), venue["name"], self.sport_var.get())

    # ==========================================
    # Function: Follow a venue selection without touching the current goal's alternatives.
    # Method: Choose its first cached project and refresh only the scoped court list.
    # ==========================================
    def venue_changed(self, event=None):
        venue = self.venues_by_label.get(self.venue_var.get())
        names = [s["name"] for s in (venue or {}).get("sports", [])]
        self.sport_combo["values"] = names
        self.sport_var.set(names[0] if names else "")
        self.sport_changed()

    # ==========================================
    # Function: Change only the candidate builder's project selection.
    # Method: Keep all goals and existing venue candidates intact.
    # ==========================================
    def sport_changed(self, event=None):
        self.refresh_courts()

    # ==========================================
    # Function: Show names learned for the current builder context without disk I/O.
    # Method: Scope choices to its venue/project and include only matching saved candidates.
    # ==========================================
    def refresh_courts(self):
        venue = self.venues_by_label.get(self.venue_var.get())
        venue_name, sport = (venue or {}).get("name", self.data["venue"]), self.sport_var.get() or self.data["sport"]
        from .desktop_controller import context_key
        names = [ANY_COURT, *self.controller.courts.get(context_key(venue_name, sport), [])]
        for candidate in (self.goal() or {}).get("candidates", []):
            context = candidate.get("context") or {"venue": self.data["venue"], "sport": self.data["sport"]}
            if (context["venue"], context["sport"]) == (venue_name, sport):
                names.append(candidate["court"])
        names = tuple(dict.fromkeys(names))
        scope = venue_name, sport, names
        if scope == getattr(self, "_court_scope", None):
            return
        self._court_names, self._court_scope = names, scope
        self.court_list.configure(state="normal")
        self.court_list.delete(0, "end")
        self.court_list.insert("end", *names)
        self.court_list.selection_set(0)
        if self.controller.busy or self.saving:
            self.court_list.configure(state="disabled")

    # ==========================================
    # Function: Add one chosen venue/project and its court choices to a multi-select draft.
    # Method: Preserve the selection order as candidate priority, allowing different project types.
    # ==========================================
    def add_venue(self):
        try:
            self.editable_goal()
            context = self.builder_context()
            courts = [self.court_list.get(i) for i in self.court_list.curselection()]
            if not courts:
                raise ValueError("请选择具体场地或任意可用场地")
            value = {"context": context, "courts": courts}
            existing = next((item for item in self.builder_contexts if item["context"] == context), None)
            if existing:
                existing["courts"] = courts
            else:
                self.builder_contexts.append(value)
            self.render_venue_tags()
        except Exception as exc:
            self.error(exc)

    # ==========================================
    # Function: Keep every added venue visible as an explicit draft selection card.
    # Method: Wrap cards across rows, show court names, and reserve a separate remove action.
    # ==========================================
    def render_venue_tags(self):
        for child in self.venue_tags.winfo_children():
            child.destroy()
        for index, item in enumerate(self.builder_contexts):
            context = item["context"]
            card = self.tk.Frame(self.venue_tags, bg="#eff6ff", padx=10, pady=8,
                                  highlightbackground="#bfdbfe", highlightthickness=1)
            remove = self.tk.Label(card, text="×", bg="#eff6ff", fg="#2563eb", font=("sans", 13), cursor="hand2", takefocus=True)
            remove.pack(side="right", padx=(6, 0))
            remove.bind("<Button-1>", lambda event, i=index: self.remove_venue(i))
            remove.bind("<Return>", lambda event, i=index: self.remove_venue(i))
            text = f"✓ {context.venue} · {context.sport}\n{'、'.join(item['courts'])}"
            label = self.tk.Label(card, text=text, bg="#eff6ff", fg="#1d4ed8", font=("sans", 9),
                                  justify="left", anchor="w", wraplength=235)
            label.pack(side="left", fill="x", expand=True)
        self.wrap_venue_tags()
        if hasattr(self, "selection_text"):
            self.selection_changed()

    # ==========================================
    # Function: Reflow venue cards when the workspace width changes.
    # Method: Use a responsive grid so added venues cannot disappear beyond the right edge.
    # ==========================================
    def wrap_venue_tags(self, event=None):
        columns = max(1, self.venue_tags.winfo_width() // 280)
        for index, card in enumerate(self.venue_tags.winfo_children()):
            card.grid(row=index // columns, column=index % columns, sticky="nsew", padx=(0, 6), pady=4)
        for column in range(10):
            self.venue_tags.columnconfigure(column, weight=1 if column < columns else 0)

    # ==========================================
    # Function: Make staged selections and the next add action explicit.
    # Method: Count selected courts, times, and all staged venue cards without querying inventory.
    # ==========================================
    def selection_changed(self, event=None):
        if not hasattr(self, "selection_text"):
            return
        courts, slots = len(self.court_list.curselection()), len(self.hour_list.curselection())
        count = len(self.builder_contexts)
        self.selection_text.set(f"已加入 {count} 个场馆 / 项目 · 当前勾选 {courts} 个场地、{slots} 个时段" if count else f"当前勾选 {courts} 个场地、{slots} 个时段 · 可直接添加，或加入多个场馆")

    # ==========================================
    # Function: Remove a pending venue chip without changing persisted alternatives.
    # Method: Mutate only the local builder draft.
    # ==========================================
    def remove_venue(self, index):
        if not self.controller.busy and not self.saving:
            self.builder_contexts.pop(index)
            self.render_venue_tags()

    # ==========================================
    # Function: Add alternatives for every selected venue/court and hour group.
    # Method: Validate all additions first, then append without duplicates in draft priority order.
    # ==========================================
    def add_candidates(self):
        try:
            goal = self.editable_goal()
            self.commit_editor()
            day = self.goal_date.get().strip()
            contexts = self.builder_contexts or [{"context": self.builder_context(), "courts": [self.court_list.get(i) for i in self.court_list.curselection()]}]
            slots = [self.hour_list.get(i) for i in self.hour_list.curselection()]
            if not slots or any(not item["courts"] for item in contexts):
                raise ValueError("请至少选择一个场地和一个时段")
            groups = [slots] if self.bundle.get() else [[slot] for slot in slots]
            additions = [asdict(Target(day, court, group, item["context"])) for item in contexts for court in item["courts"] for group in groups]
            for candidate in additions:
                if candidate not in goal["candidates"]:
                    goal["candidates"].append(candidate)
            self.builder_contexts.clear()
            self.render_venue_tags()
            self.dirty = True
            self.render_candidates()
            self.render_goals()
            self.selection_changed()
        except Exception as exc:
            self.error(exc)

    # ==========================================
    # Function: Update sidebar goal rows only when their content or ordering changes.
    # Method: Retain widget identities and selection rather than deleting the entire list.
    # ==========================================
    def render_goals(self):
        ids = {identity for identity, g in self.workspace_items()}
        for identity in self.goal_tree.get_children():
            if identity not in ids:
                self.goal_tree.delete(identity)
                self._goal_rows.pop(identity, None)
        for index, (identity, goal) in enumerate(self.workspace_items()):
            day = goal["candidates"][0]["date"] if goal["candidates"] else "待添加备选"
            status = self.goal_states.get(identity, {}).get("status", "pending")
            label = STATUS.get(status, status)
            if identity.startswith("rule:"):
                days = goal.get("weekdays", list(range(7)))
                day = "每天" if len(days) == 7 else "周" + " / ".join("一二三四五六日"[d] for d in days)
                label = ("重复 · " if goal["enabled"] else "已暂停 · ") + goal["at"]
            text = f"{goal['name'] or '未命名目标'}\n{day}  ·  {label}"
            if self.goal_tree.exists(identity):
                if self._goal_rows.get(identity) != (text, day, label):
                    self.goal_tree.item(identity, text=text, values=(day, label))
                if self.goal_tree.index(identity) != index:
                    self.goal_tree.move(identity, "", index)
            else:
                self.goal_tree.insert("", "end", iid=identity, text=text, values=(day, label))
            self._goal_rows[identity] = (text, day, label)
        if self.selected_goal and self.goal_tree.exists(self.selected_goal) and self.goal_tree.selection() != (self.selected_goal,):
            self.goal_tree.selection_set(self.selected_goal)
        self.update_summary()

    # ==========================================
    # Function: Commit the previous goal before displaying another existing goal.
    # Method: Use stable IDs and avoid any website or configuration-file reads.
    # ==========================================
    def goal_selected(self, event=None):
        selected = self.goal_tree.selection()
        if selected and selected[0] != self.selected_goal:
            self.commit_editor()
            self.select_goal(selected[0])

    # ==========================================
    # Function: Populate one goal's editor and restore its own pending venue selections.
    # Method: Resolve saved candidate context from memory and preserve all other goals.
    # ==========================================
    def select_goal(self, identity):
        self.loading_editor = True
        if self.selected_goal:
            self.builder_drafts[self.selected_goal] = list(self.builder_contexts)
        self.selected_goal = identity
        goal = self.goal()
        today = datetime.now(ZoneInfo(self.data["timezone"])).date()
        name = goal["name"] if goal else ""
        day = goal["candidates"][0]["date"] if goal and goal["candidates"] else (today+timedelta(days=1)).isoformat()
        if self.goal_name.get() != name:
            self.goal_name.set(name)
        if self.goal_date.get() != day:
            self.goal_date.set(day)
        if goal and goal["candidates"]:
            context = goal["candidates"][0].get("context")
            if context:
                match = next((label for label, venue in self.venues_by_label.items() if venue["name"] == context["venue"]), None)
                if match:
                    if self.venue_var.get() != match:
                        self.venue_var.set(match)
                        self.venue_changed()
                    if self.sport_var.get() != context["sport"]:
                        self.sport_var.set(context["sport"])
        if identity and self.goal_tree.exists(identity) and self.goal_tree.selection() != (identity,):
            self.goal_tree.selection_set(identity)
        self.builder_contexts = list(self.builder_drafts.get(identity, []))
        self.render_venue_tags()
        self.refresh_courts()
        self.render_candidates()
        self.load_schedule()
        self.loading_editor = False
        self.set_busy()

    # ==========================================
    # Function: Render the selected goal's cross-venue alternative list.
    # Method: Reuse rows on explicit edits and show venue/project next to exact court and hours.
    # ==========================================
    def render_candidates(self):
        rows = []
        for index, candidate in enumerate((self.goal() or {}).get("candidates", [])):
            context = candidate.get("context") or {"venue": self.data["venue"], "sport": self.data["sport"]}
            rows.append((f"  {index+1:02d}", (f"{context['venue']} · {context['sport']}", candidate["court"], ", ".join(candidate["slots"]))))
        previous = getattr(self, "_candidate_view", [])
        if rows == previous:
            return
        for index in range(len(rows), len(previous)):
            self.candidate_tree.delete(str(index))
        for index, (text, values) in enumerate(rows):
            if index >= len(previous):
                self.candidate_tree.insert("", "end", iid=str(index), text=text, values=values)
            elif rows[index] != previous[index]:
                self.candidate_tree.item(str(index), text=text, values=values)
        self._candidate_view = rows
        self.candidate_tree.configure(height=min(3 if getattr(self, "_compact", False) else 4, max(1, len(rows))))

    # ==========================================
    # Function: Keep a compact workspace summary current without database reads.
    # Method: Derive counts from local data and progress already received.
    # ==========================================
    def update_summary(self):
        done = sum(self.goal_states.get(g["id"], {}).get("status") == "submitted" for g in self.data["goals"])
        rules = sum(r["enabled"] for r in self.data["repeat_rules"])
        value = f"{len(self.workspace_items())} 个目标  ·  {done} 个已完成  ·  {rules} 个重复预约已开启  ·  所有场馆共用监测间隔"
        if value != getattr(self, "_summary_value", None):
            self._summary_value = value
            self.summary_text.set(value)

    # ==========================================
    # Function: Show secondary execution controls only when requested.
    # Method: Reuse existing Tk variables and avoid loading a settings pane at each status update.
    # ==========================================
    def open_settings(self):
        if self.settings_window and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return
        window = self.tk.Toplevel(self.root, bg=BG)
        self.settings_window = window
        window.title("执行设置")
        window.geometry("550x620")
        window.minsize(550, 620)
        window.transient(self.root)
        window.chrome = self.chrome_type(window, window.destroy, "执行设置")
        panel = self.tk.Frame(window, bg=BG, padx=28, pady=24)
        panel.pack(fill="both", expand=True)
        self.label(panel, "执行设置", size=19, bold=True).pack(anchor="w", pady=(0, 8))
        self.label(panel, "所有目标与场馆共用；提交订单后通知，不付款。", color=MUTED, size=9).pack(anchor="w", pady=(0, 17))
        for label, variable in [("启动时间 · 北京时间，留空立即", self.start_at), ("全局读取间隔 / 秒", self.interval),
                                ("额外随机延迟 / %", self.jitter), ("退避上限 / 秒", self.backoff),
                                ("单个订单金额上限 / 元", self.price), ("非持续模式监测时长 / 秒", self.max_poll)]:
            row = self.tk.Frame(panel, bg=BG)
            row.pack(fill="x", pady=6)
            self.label(row, label, size=10).pack(side="left")
            self.entry(row, variable, 21).pack(side="right")
        for label, variable in [("所有目标完成后停止", self.until_complete), ("显示预约浏览器（用于人工验证）", self.show_browser)]:
            check = self.ttk.Checkbutton(panel, text=label, variable=variable)
            check.pack(anchor="w", pady=10)
            self.action_widgets.append(check)
        self.button(panel, "保存设置", lambda: self.save(background=True) and window.destroy(), primary=True, width=170).pack(anchor="e", pady=20)
        window.chrome.raise_grips()

    # ==========================================
    # Function: Reveal optional activity details without fetching additional availability.
    # Method: Render the latest in-memory snapshot only when the user opens the panel.
    # ==========================================
    def toggle_activity(self):
        self.activity_visible = not self.activity_visible
        if self.activity_visible:
            self.activity.pack(fill="both", expand=True, pady=(5, 0))
            if self.pending_snapshot:
                self.current_snapshot = None
                self.render_inventory(self.pending_snapshot)
        else:
            self.activity.pack_forget()

    # ==========================================
    # Function: Disable edits and protect recorded goal fields during monitoring.
    # Method: Toggle existing controls, remove destroyed dialog controls, and keep stop available.
    # ==========================================
    def set_busy(self):
        busy = self.controller.busy or self.saving
        recorded = self.goal_states.get(self.selected_goal, {}).get("status", "pending") not in {"pending", "preview"}
        signature = busy, self.controller.operation, len(self.action_widgets)
        if signature != self._busy_signature:
            self._busy_signature = signature
            self.action_widgets = [w for w in self.action_widgets if w.winfo_exists()]
            for widget in self.action_widgets:
                if hasattr(widget, "state"):
                    widget.state(["disabled"] if busy else ["!disabled"])
                else:
                    widget.configure(state="disabled" if busy else "normal")
            for widget in (self.venue_combo, self.sport_combo):
                widget.configure(state="disabled" if busy else "readonly")
            for widget in (self.court_list, self.hour_list):
                widget.configure(state="disabled" if busy else "normal")
            self.capture_button.pack_forget()
            self.stop_button.pack_forget()
            if busy and self.controller.operation == "login":
                self.capture_button.pack(side="right", padx=8)
            if busy and not self.saving:
                self.stop_button.pack(side="right", padx=8)
            self._editor_signature = None
        if (busy, recorded, self.is_repeat(), self.selected_goal) != self._editor_signature:
            self._editor_signature = busy, recorded, self.is_repeat(), self.selected_goal
            self.name_entry.configure(state="disabled" if busy or recorded else "normal")
            self.date_entry.configure(state="disabled" if busy or recorded or self.is_repeat() else "normal")
            self.repeat_button.state(["disabled"] if busy or recorded or self.goal() is None else ["!disabled"])
            self.ahead_combo.configure(state="disabled" if busy else "readonly")

    # ==========================================
    # Function: Update only changed progress rows without rebuilding the selected editor.
    # Method: Compare existing values before sending native widget update commands.
    # ==========================================
    def update_goal_states(self, goals):
        for state in goals:
            self.goal_states[state["id"]] = state
            if not self.goal_tree.exists(state["id"]):
                continue
            label = "退避等待" if state.get("watch_state") == "backoff" else "检查中" if state.get("watch_state") == "checking" else STATUS.get(state["status"], state["status"])
            values = list(self.goal_tree.item(state["id"], "values"))
            if values[1] != label:
                values[1] = label
                goal = next((g for g in self.data["goals"] if g["id"] == state["id"]), None)
                self.goal_tree.item(state["id"], text=f"{(goal or {}).get('name','预约目标')}\n{values[0]}  ·  {label}", values=values)
                self._goal_rows.pop(state["id"], None)
        self.update_summary()

    # ==========================================
    # Function: Render a visible monitoring snapshot without issuing any network request.
    # Method: Skip hidden or unchanged grids and create rows only for the selected details tab.
    # ==========================================
    def render_inventory(self, shot):
        self.pending_snapshot = shot
        if not self.activity_visible or shot == self.current_snapshot:
            return
        self.current_snapshot = shot
        self.inventory_tree.delete(*self.inventory_tree.get_children())
        if not shot:
            self.inventory_text.set("尚未启动监测，未读取空位")
            return
        columns = ["hour", *[f"court-{i}" for i in range(len(shot["columns"]))]]
        self.inventory_tree.configure(columns=columns)
        for column, text in zip(columns, ["时段", *shot["columns"]]):
            self.inventory_tree.heading(column, text=text)
            self.inventory_tree.column(column, width=95, minwidth=55)
        for index, row in enumerate(shot["rows"]):
            self.inventory_tree.insert("", "end", values=[shot["times"][index], *[{"available": "可预约", "selected": "已选"}.get(s, "—") for s in row]])
        self.inventory_text.set(f"{shot['venue']} / {shot['sport']} / {shot['active_date']} · 最近一次监测结果")

    # ==========================================
    # Function: Display a confirmed order immediately without blocking other goals or Tk.
    # Method: Use one modeless topmost notification window and aggregate rapid completions.
    # ==========================================
    def show_completion(self, goal):
        detail = goal.get("detail", {})
        identity = (goal["id"], str(detail.get("order_id")))
        if not detail.get("order_id") or identity in self._shown_notice_ids:
            return
        self._shown_notice_ids.add(identity)
        if not self.completion_window or not self.completion_window.winfo_exists():
            self.completions = []
            window = self.tk.Toplevel(self.root, bg=BG)
            self.completion_window = window
            window.title("预约成功 · AutoReserve")
            window.geometry("520x390")
            window.minsize(520, 390)
            window.attributes("-topmost", True)
            window.chrome = self.chrome_type(window, window.destroy, "预约成功")
            panel = self.tk.Frame(window, bg=BG, padx=28, pady=25)
            panel.pack(fill="both", expand=True)
            self.label(panel, "✓  预约已下单", size=20, color="#286d4d", bold=True).pack(anchor="w", pady=(0, 10))
            self.notice_text = self.tk.Text(panel, height=9, bg=BG, fg=INK, font=("sans", 11), wrap="word", relief="flat", bd=0, highlightthickness=0, state="disabled")
            self.notice_text.pack(fill="both", expand=True)
            self.label(panel, "订单已提交，未执行付款。其他目标会继续监测。", color=MUTED, size=9).pack(anchor="w", pady=10)
            rounded_button(panel, "知道了", window.destroy, True, 120).pack(anchor="e")
            window.chrome.raise_grips()
        self.completions.append(goal)
        self.notice_text.configure(state="normal")
        self.notice_text.insert("end", f"{goal.get('name','预约目标')}\n{detail.get('venue','')} · {detail.get('sport','')} · {detail.get('court','')}\n{detail.get('date','')}  {', '.join(detail.get('slots',[]))}\n订单号：{detail['order_id']}\n\n")
        self.notice_text.see("end")
        self.notice_text.configure(state="disabled")
        self.completion_window.chrome.title.configure(text=f"AutoReserve  /  {len(self.completions)} 个目标已下单")
        self.completion_window.deiconify()
        self.completion_window.lift()
        self.root.bell()

    # ==========================================
    # Function: Process worker events without I/O or a full UI refresh on each tick.
    # Method: Bound each batch by time, coalesce progress, and preserve every completion event.
    # ==========================================
    def drain_events(self):
        if self.after_id:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        latest, until = None, time.monotonic() + 0.008
        for _ in range(160):
            try:
                kind, value = self.controller.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.append_log(value)
            elif kind == "auth":
                self.auth_text.set(value)
            elif kind == "catalog":
                self.render_catalog()
                self.refresh_courts()
            elif kind == "progress":
                latest = value
            elif kind == "goal_completed":
                self.show_completion(value)
            elif kind == "busy":
                self.set_busy()
            elif kind == "workspace":
                self.data = value
                self.render_goals()
                if self.selected_goal not in {identity for identity, g in self.workspace_items()}:
                    self.select_goal(self.workspace_items()[0][0] if self.workspace_items() else None)
            elif kind == "states":
                self.update_goal_states(value)
            elif kind in {"saved", "save_failed"}:
                self.saving = False
                self.set_busy()
                if kind == "save_failed":
                    self.error(value)
                else:
                    self.dirty = False
                    self.render_goals()
                    if not value["quiet"]:
                        self.status_text.set("设置已保存 · 点击开始监测后每日规则生效")
                    if value["operation"] and not self.closing:
                        self.action(value["operation"], save=False)
                if self.closing:
                    self.close()
                    return
            elif kind == "finished":
                self.next_read_at = None
                self.set_busy()
                self.refresh_courts()
                if self.closing:
                    self.root.destroy()
                    return
            if time.monotonic() >= until:
                break
        if latest:
            self.update_goal_states(latest.get("goals", []))
            if latest.get("snapshot") is not None:
                self.render_inventory(latest["snapshot"])
            self.next_read_at = latest.get("monitor", {}).get("next_read_at")
        if self.next_read_at and self.controller.busy and self.controller.operation == "run":
            remaining = max(0, int(self.next_read_at - time.time()))
            text = f"{self.controller.message} · 下次读取约 {remaining} 秒后"
            if text != self._last_countdown:
                self._last_countdown = text
                self.status_text.set(text)
        delay = 1 if not self.controller.events.empty() else 1000
        self.after_id = self.root.after(delay, self.drain_events)


# ==========================================
# Function: Launch the production GTK client while preserving the public CLI entry point.
# Method: Delegate to the TeleMail-style native shell; the Tk class above is legacy regression code.
# ==========================================
def serve_desktop(config_path, auth_dir):
    from .gtk_desktop import serve_gtk
    return serve_gtk(config_path, auth_dir)
