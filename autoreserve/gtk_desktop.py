"""GTK desktop workspace: native layout/scrolling, pure editor state, queued browser work."""

import ctypes as C
from dataclasses import asdict
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import queue
import signal
import sys
import threading
import time

from .catalog import ANY_COURT, booking_url
from .config import load_config, config_from_dict
from .desktop_controller import DesktopController, context_key
from .gtk_native import GtkNative, P, I
from .workspace import Workspace
from .state import private_json
from .paths import prepare_config
from .config import RushSettings
from zoneinfo import ZoneInfo

ASSETS = Path(__file__).with_name('gtk_ui')
HOURS = [f'{h:02d}:00-{h+1:02d}:00' for h in range(7,22)]
STATUS = {'pending':'等待空位','submitted':'已下单','preview':'已预览','expired':'日期已过',
          'unknown':'需核对订单','needs_verification':'需人工验证','conflict':'需核对候选','rejected':'继续监测'}


# ==========================================
# Function: Coordinate GTK views without placing business state inside native widgets.
# Method: Reuse DesktopController workers and drain their event queue on the GTK main thread.
# ==========================================
class GtkDesktop:
    # ==========================================
    # Function: Build an offline workspace with real GTK scroll containers and CSD windows.
    # Method: Load GtkBuilder/CSS before mapping and keep browser execution behind explicit actions.
    # ==========================================
    def __init__(self, controller, native=None, application=None, auto_catalog=True):
        self.g = native or GtkNative()
        g = self.g
        if not g.gtk_init_check(None,None):
            raise RuntimeError('无法初始化 GTK，请在图形桌面中启动并保留完整程序运行库')
        self.controller, self.application = controller, application
        self.model = Workspace(load_config(controller.config_path))
        self.loading = self.saving = self.closing = self.destroyed = False
        self.settings_window = self.notice_window = self.confirm_window = None
        self.settings_fields, self.notices, self.errors = {}, {}, []
        self.edit_controls, self.sidebar_rows = [], {}
        self.court_buttons, self.hour_buttons = {}, {}
        self.logs, self.snapshot = [], None
        self.monitor_started, self._inventory_signature = False, None
        self.font_provider = None
        self.font_spin = None
        self.layout_save_timer = None
        self.layout_restore_timer = None
        self._layout_ready = self._restoring_layout = False
        self._layout_phase = 0
        self._sidebar_order, self._court_scope, self._busy = None, None, None
        self._sidebar_captions, self._row_tones = {}, {}
        self._folds, self._rush_status_text = {}, None
        self._log_pending, self._shown_orders, self.next_read_at = False, set(), None
        self.builder = g.builder(ASSETS/'main.ui')
        ids = ['window','header','sidebar','main','new_goal','goals','goal_scroll','goal_count','goal_up','goal_down',
               'settings','auth','login','summary','save','start','stop','capture','editor','editor_scroll',
               'error_bar','error','dismiss_error','monitoring','log_card','inventory_card','logs','log_scroll',
               'run_badge','inventory','inventory_scope','inventory_scroll','status',
               'layout','workspace_split','log_split','inventory_split']
        self.w = {name:g.object(self.builder,name) for name in ids}
        self.window = self.w['window']
        g.configure_desktop_window(self.window)
        if application:
            g.gtk_window_set_application(self.window,application)
        g.css(ASSETS/'style.css')
        self.build_editor()
        self.load_display_preferences()
        self.bind_layout()
        self.bind_actions()
        self.model.states = {s['id']:s for s in controller.state()['goals']}
        legend=self.box(spacing=10)
        for caption,css in [('● 监测中','legend-running'),('● 重复','legend-recurring'),('● 待启动','legend-planned')]:
            self.pack(legend,self.label(caption,css))
        g.gtk_box_pack_start(self.w['sidebar'],legend,False,True,0)
        reorder=g.libs['gtk'].gtk_box_reorder_child
        reorder.argtypes,reorder.restype=[P,P,I],None
        reorder(self.w['sidebar'],legend,3)
        self.render_catalog()
        self.render_sidebar()
        self.load_editor()
        self.render_inventory(None)
        g.label(self.w['auth'],controller.auth_status)
        g.gtk_widget_show_all(self.window)
        self.refresh_visibility()
        self.timer = g.later(33,self.tick,repeat=True)
        self.start_layout_restore()
        g.later(40,self.reveal_selected_hours)
        if auto_catalog and controller.catalog is None and (controller.auth_dir/'session.json').exists():
            g.later(250,lambda:self.action('catalog'))

    # ==========================================
    # Function: Create a native box with consistent orientation and spacing.
    # Method: Let GTK measure and allocate children rather than maintaining pixel offsets.
    # ==========================================
    def box(self, vertical=False, spacing=8, css=None):
        widget = self.g.gtk_box_new(int(vertical),spacing)
        if css:
            self.g.style(widget,css)
        return widget

    # ==========================================
    # Function: Pack a child with explicit expansion semantics.
    # Method: Keep row expansion explicit while native paned widgets own workspace region sizes.
    # ==========================================
    def pack(self, parent, child, expand=False):
        self.g.gtk_box_pack_start(parent,child,expand,True,0)
        return child

    # ==========================================
    # Function: Construct readable plain-text labels with optional wrapping.
    # Method: Keep user data out of markup and constrain long names through native ellipsizing.
    # ==========================================
    def label(self, text='', css=None, wrap=False, ellipsis=False):
        g=self.g
        widget=g.gtk_label_new(str(text).encode())
        g.gtk_label_set_xalign(widget,0)
        if css:
            g.style(widget,css)
        if wrap:
            g.gtk_label_set_line_wrap(widget,True)
            g.gtk_label_set_max_width_chars(widget,60)
        if ellipsis:
            g.gtk_label_set_ellipsize(widget,3)
            g.gtk_label_set_max_width_chars(widget,28)
        return widget

    # ==========================================
    # Function: Construct accessible native actions with guarded callbacks.
    # Method: Route pointer and keyboard activation through GTK's clicked signal.
    # ==========================================
    def button(self, text, callback, css='secondary', editable=False):
        g=self.g
        widget=g.gtk_button_new_with_label(text.encode())
        g.style(widget,css)
        g.connect(widget,'clicked',lambda *_:self.guard(callback))
        if editable:
            self.edit_controls.append(widget)
        return widget

    # ==========================================
    # Function: Construct native text fields with optional placeholders.
    # Method: Rely on GTK editing and focus behavior.
    # ==========================================
    def entry(self, value='', width=None, placeholder=None):
        g=self.g
        widget=g.gtk_entry_new()
        g.text(widget,value)
        if width:
            g.gtk_entry_set_width_chars(widget,width)
        if placeholder:
            g.gtk_entry_set_placeholder_text(widget,placeholder.encode())
        return widget

    # ==========================================
    # Function: Create a labeled input group.
    # Method: Keep field captions aligned using a measured vertical GTK box.
    # ==========================================
    def field(self, caption, control):
        group=self.box(True,6)
        self.pack(group,self.label(caption,'caption'))
        self.pack(group,control)
        return group

    # ==========================================
    # Function: Create a wrapping native selection/chip container.
    # Method: Use GtkFlowBox for responsive columns and scrolling event propagation.
    # ==========================================
    def flow(self, minimum=1, maximum=3):
        g=self.g
        widget=g.gtk_flow_box_new()
        g.gtk_flow_box_set_selection_mode(widget,0)
        g.gtk_widget_set_valign(widget,1)
        g.gtk_flow_box_set_min_children_per_line(widget,minimum)
        g.gtk_flow_box_set_max_children_per_line(widget,maximum)
        g.gtk_flow_box_set_column_spacing(widget,7)
        g.gtk_flow_box_set_row_spacing(widget,7)
        return widget

    # ==========================================
    # Function: Wrap native content in a reliable scrolling viewport.
    # Method: Delegate wheel bubbling, thumb dragging, and adjustment bounds to GTK.
    # ==========================================
    def scroller(self, child, height):
        g=self.g
        widget=g.gtk_scrolled_window_new(None,None)
        g.gtk_scrolled_window_set_policy(widget,2,1)
        g.gtk_scrolled_window_set_overlay_scrolling(widget,False)
        g.gtk_widget_set_size_request(widget,-1,height)
        g.gtk_container_add(widget,child)
        return widget

    # ==========================================
    # Function: Build persistent editing controls independent of goal selection.
    # Method: Update their values on navigation and rebuild only explicitly changed candidate/choice lists.
    # ==========================================
    def build_editor(self):
        g,card=self.g,self.w['editor']
        top=self.box()
        self.pack(card,top)
        self.mode=self.pack(top,self.label('普通目标 · 单次预约','mode'),True)
        self.pack(top,self.label('抢购模式','caption'))
        self.rush_switch=g.gtk_switch_new()
        self.pack(top,self.rush_switch)
        g.gtk_widget_set_valign(self.rush_switch,3)
        self.edit_controls.append(self.rush_switch)
        self.automation_expander,self.automation_body=self.section(card,'自动执行 · 重复与抢购','automation',False)
        self.repeat_expander,repeat_body=self.section(self.automation_body,'重复功能设置','repeat',False)
        self.repeat_button=self.pack(repeat_body,self.button('设为每周重复',self.toggle_repeat,editable=True))
        self.pack(repeat_body,self.label('选定每周执行日；开启抢购后将提前生成并准备。','caption',wrap=True))
        self.rush_expander,rush_body=self.section(self.automation_body,'抢购功能设置','rush',False)
        self.build_rush(rush_body)
        self.target_expander,card=self.section(card,'目标设定','target',True)
        meta=self.box(spacing=14)
        self.pack(card,meta)
        self.name_entry,self.date_entry=self.entry(),self.entry(width=12,placeholder='YYYY-MM-DD')
        self.date_caption=self.label('预约日期','caption')
        self.pack(meta,self.field('目标名称',self.name_entry),True)
        date_group=self.box(True,6)
        self.pack(date_group,self.date_caption)
        self.pack(date_group,self.date_entry)
        self.pack(meta,date_group)
        self.repeat_revealer=g.gtk_revealer_new()
        g.gtk_revealer_set_transition_duration(self.repeat_revealer,0)
        self.pack(repeat_body,self.repeat_revealer)
        self.repeat_panel=self.box(True,10,'repeat-panel')
        g.gtk_container_add(self.repeat_revealer,self.repeat_panel)
        row=self.box()
        self.pack(self.repeat_panel,row)
        self.pack(row,self.label('自动预约日','section-title'),True)
        self.repeat_switch=g.gtk_switch_new()
        g.gtk_widget_set_valign(self.repeat_switch,3)
        self.pack(row,self.label('开启重复','caption'))
        self.pack(row,self.repeat_switch)
        self.week_buttons=[]
        days=self.pack(self.repeat_panel,self.box())
        for index,caption in enumerate(['周一','周二','周三','周四','周五','周六','周日']):
            button=g.gtk_toggle_button_new_with_label(caption.encode())
            g.style(button,'day')
            g.connect(button,'toggled',lambda *_, day=index:self.week_changed(day))
            self.pack(days,button,True)
            self.week_buttons.append(button)
        timing=self.pack(self.repeat_panel,self.box(spacing=12))
        self.repeat_at=self.entry('12:00',width=6)
        self.repeat_ahead=g.gtk_spin_button_new_with_range(0,7,1)
        self.pack(timing,self.label('执行时间','caption'))
        self.pack(timing,self.repeat_at)
        self.pack(timing,self.label('预约往后','caption'))
        self.pack(timing,self.repeat_ahead)
        self.pack(timing,self.label('天 · 北京时间','caption'))
        self.schedule_hint=self.pack(self.repeat_panel,self.label('', 'caption',wrap=True))
        g.connect(self.repeat_at,'activate',lambda *_:self.guard(self.commit_schedule))
        g.connect(self.repeat_at,'focus-out-event',lambda *_:self.schedule_focus_out(),count=3,returns=True)
        g.connect(self.repeat_ahead,'value-changed',lambda *_:self.guard(self.commit_schedule))
        g.connect(self.repeat_switch,'notify::active',lambda *_:self.guard(self.commit_schedule),count=3)
        heading=self.pack(card,self.box())
        self.pack(heading,self.label('场馆备选','section-title'),True)
        self.add_button=self.pack(heading,self.button('＋ 添加场馆与时段',self.open_builder,'secondary',True))
        self.pack(heading,self.button('↑',lambda:self.move_candidate(-1),'icon',True))
        self.pack(heading,self.button('↓',lambda:self.move_candidate(1),'icon',True))
        self.pack(heading,self.button('移除',self.delete_candidate,'icon',True))
        self.candidate_list=g.gtk_list_box_new()
        g.gtk_list_box_set_selection_mode(self.candidate_list,1)
        g.style(self.candidate_list,'candidate-list')
        self.pack(card,self.label('从上到下依次尝试，任意一个下单即完成当前目标。','caption'))
        self.candidate_scroll=self.pack(card,self.scroller(self.candidate_list,248))
        self.builder_popover=g.gtk_popover_new(self.add_button)
        g.gtk_popover_set_position(self.builder_popover,3)
        g.gtk_popover_set_transitions_enabled(self.builder_popover,False)
        g.style(self.builder_popover,'candidate-composer')
        self.builder_shell=self.box(True,14,'composer-shell')
        g.gtk_widget_set_size_request(self.builder_shell,600,-1)
        g.gtk_container_add(self.builder_popover,self.builder_shell)
        builder_heading=self.pack(self.builder_shell,self.box())
        self.pack(builder_heading,self.label('添加场馆与时段','section-title'),True)
        self.pack(builder_heading,self.button('×',lambda:g.gtk_popover_popdown(self.builder_popover),'icon'))
        self.catalog_hint=self.pack(self.builder_shell,self.label('场馆目录保存在本机','caption',wrap=True))
        self.builder_body=self.box(True,14)
        self.builder_scroll=self.pack(self.builder_shell,self.scroller(self.builder_body,280),True)
        g.gtk_scrolled_window_set_min_content_height(self.builder_scroll,240)
        g.gtk_scrolled_window_set_max_content_height(self.builder_scroll,420)
        g.gtk_scrolled_window_set_propagate_natural_height(self.builder_scroll,True)
        self.venue_expander,card=self.section(self.builder_body,'场馆与项目','builder-venue',True)
        context=self.pack(card,self.box())
        self.venue_combo,self.sport_combo=g.gtk_combo_box_text_new(),g.gtk_combo_box_text_new()
        self.pack(context,self.venue_combo,True)
        self.pack(context,self.sport_combo,True)
        self.pack(context,self.button('＋ 加入场馆',self.add_venue,editable=True))
        self.venue_flow=self.pack(card,self.flow(1,3))
        self.choices_expander,card=self.section(self.builder_body,'场地与时段','builder-choices',True)
        choices=self.pack(card,self.box(spacing=16))
        courts,slots=self.box(True,8),self.box(True,8)
        self.pack(choices,courts,True)
        self.pack(choices,slots,True)
        self.pack(courts,self.label('具体场地 · 可多选','caption'))
        self.pack(slots,self.label('预约时段 · 可多选','caption'))
        self.court_flow,self.hour_flow=self.flow(1,2),self.flow(2,2)
        self.court_scroll=self.pack(courts,self.scroller(self.court_flow,180))
        self.hour_scroll=self.pack(slots,self.scroller(self.hour_flow,180))
        self.build_choices(self.hour_flow,HOURS,self.hour_buttons,{'14:00-15:00'},False)
        self.selection_hint=self.pack(self.builder_shell,self.label('', 'caption',wrap=True))
        addbar=self.pack(self.builder_shell,self.box())
        self.bundle=g.gtk_check_button_new_with_label('连续时段合并下单'.encode())
        self.pack(addbar,self.bundle,True)
        self.pack(addbar,self.button('添加备选  →',self.add_candidates,'primary',True))
        for widget in [self.venue_combo,self.sport_combo,self.repeat_switch,self.repeat_at,self.repeat_ahead,self.bundle,*self.week_buttons]:
            self.edit_controls.append(widget)
        g.connect(self.name_entry,'changed',lambda *_:self.metadata_changed())
        g.connect(self.date_entry,'changed',lambda *_:self.metadata_changed())
        g.connect(self.venue_combo,'changed',lambda *_:self.venue_changed())
        g.connect(self.sport_combo,'changed',lambda *_:self.refresh_courts())

    # ==========================================
    # Function: Open a compact anchored candidate composer without discarding unfinished selections.
    # Method: Keep its native controls alive between dismissals and constrain scrolling to the visible window height.
    # ==========================================
    def open_builder(self):
        self.editing()
        self.model.editable()
        g=self.g
        height=max(200,min(420,g.gtk_widget_get_allocated_height(self.window)-300))
        g.gtk_widget_set_size_request(self.builder_scroll,-1,min(280,height))
        g.gtk_scrolled_window_set_min_content_height(self.builder_scroll,0)
        g.gtk_scrolled_window_set_max_content_height(self.builder_scroll,height)
        g.gtk_scrolled_window_set_min_content_height(self.builder_scroll,min(240,height))
        g.gtk_widget_show_all(self.builder_popover)
        g.gtk_popover_popup(self.builder_popover)
        g.later(25,self.reveal_selected_hours)

    # ==========================================
    # Function: Restore application typography and pane positions independently of booking settings.
    # Method: Read the existing presentation file once and scope CSS to this window and its floating composer.
    # ==========================================
    def load_display_preferences(self):
        g=self.g
        self.ui_path=self.controller.config_path.parent/'desktop-ui.json'
        try:
            self.ui_preferences=json.loads(self.ui_path.read_text(encoding='utf-8'))
            if not isinstance(self.ui_preferences,dict):
                self.ui_preferences={}
        except (OSError,ValueError,AttributeError):
            self.ui_preferences={}
        value=self.ui_preferences.get('font_size',13)
        if not isinstance(self.ui_preferences.get('panes'),dict):
            self.ui_preferences['panes']={}
        self.font_size=value if type(value) is int and 11<=value<=20 else 13
        self.font_scope='workbench-'+format(self.window,'x')
        for widget in (self.window,self.builder_popover):
            g.style(widget,self.font_scope)
        self.set_font_size(self.font_size,persist=False)

    # ==========================================
    # Function: Resize all application text without altering goals or interrupting monitoring.
    # Method: Apply the same scoped provider to the sidebar, workbench, popover and every owned dialog.
    # ==========================================
    def set_font_size(self, value, persist=True):
        g=self.g
        self.font_size=max(11,min(20,int(value)))
        scope='.'+self.font_scope
        rules=[f'{scope}, {scope} * {{ font-size: {self.font_size}px; }}']
        for name,offset in [('caption',-2),('muted',-1),('page-title',10),('section-title',1),('brand',8),
                            ('mode',-2),('badge',-2),('choice',-1),('inventory-cell',-2),('log-text',-2),
                            ('goal-name',-1),('legend-running',-3),('legend-recurring',-3),('legend-planned',-3)]:
            rules.append(f'{scope} .{name}, {scope} .{name} * {{ font-size: {max(10,self.font_size+offset)}px; }}')
        self.font_provider=g.css_text('\n'.join(rules),self.font_provider)
        if self.font_spin and g.gtk_spin_button_get_value_as_int(self.font_spin)!=self.font_size:
            g.gtk_spin_button_set_value(self.font_spin,self.font_size)
        self.ui_preferences['font_size']=self.font_size
        if persist:
            self.persist_display_preferences()

    # ==========================================
    # Function: Register native draggable dividers for column widths and independent card heights.
    # Method: Observe GtkPaned positions without intercepting pointer events or touching reservation data.
    # ==========================================
    def bind_layout(self):
        self.panes={name:self.w[name] for name in ('layout','workspace_split','monitoring','log_split','inventory_split')}
        captions={'layout':'拖动调整左栏宽度','workspace_split':'上下拖动调整目标区与底部面板高度',
                  'monitoring':'左右拖动调整执行记录与空位监测宽度',
                  'log_split':'拖动底边调整执行记录高度','inventory_split':'拖动底边调整空位监测高度'}
        for name,pane in self.panes.items():
            self.g.gtk_widget_set_tooltip_text(pane,captions[name].encode())
            self.g.connect(pane,'notify::position',lambda *_,key=name:self.remember_pane(key),count=3)

    # ==========================================
    # Function: Begin a staged restore after GTK has allocated the window's real size.
    # Method: Cancel earlier restoration work and let parent dividers settle before their nested panes.
    # ==========================================
    def start_layout_restore(self, use_saved=True):
        if self.layout_restore_timer:
            self.g.cancel(self.layout_restore_timer)
        if self.layout_save_timer:
            self.g.cancel(self.layout_save_timer)
            self.layout_save_timer=None
        self._layout_ready=False
        self._layout_phase=0
        self._layout_use_saved=use_saved
        self.layout_restore_timer=self.g.later(40,self.restore_layout,repeat=True)

    # ==========================================
    # Function: Restore native geometry from outer columns down to individual card heights.
    # Method: Wait a layout turn between hierarchy levels so parent resizing cannot redistribute restored child positions.
    # ==========================================
    def restore_layout(self):
        if self.destroyed or self._layout_ready:
            return False
        g=self.g
        height=g.gtk_widget_get_allocated_height(self.w['workspace_split'])
        width=g.gtk_widget_get_allocated_width(self.w['monitoring'])
        if height<100 or width<100:
            return True
        target=max(160,height-250)
        defaults={'layout':278,'workspace_split':target,'monitoring':width//2,
                  'log_split':max(120,g.gtk_widget_get_allocated_height(self.w['log_split'])-8),
                  'inventory_split':max(140,g.gtk_widget_get_allocated_height(self.w['inventory_split'])-8)}
        levels=[('layout','workspace_split'),('monitoring',),('log_split','inventory_split')]
        self._restoring_layout=True
        try:
            for name in levels[self._layout_phase]:
                pane=self.panes[name]
                value=self.ui_preferences['panes'].get(name) if self._layout_use_saved else None
                position=value if type(value) is int and 0<=value<=100000 else defaults[name]
                g.gtk_paned_set_position(pane,position)
        finally:
            self._restoring_layout=False
        self._layout_phase+=1
        if self._layout_phase<len(levels):
            return True
        self._layout_ready=True
        self.layout_restore_timer=None
        if not self._layout_use_saved:
            self.ui_preferences['panes']={name:g.gtk_paned_get_position(pane) for name,pane in self.panes.items()}
            self.guard(self.persist_display_preferences)
        return False

    # ==========================================
    # Function: Remember divider changes without writing a file for every mouse-motion event.
    # Method: Cache each native position and debounce preference writes until the pointer settles.
    # ==========================================
    def remember_pane(self, name):
        if not self._layout_ready or self._restoring_layout or self.destroyed:
            return
        self.ui_preferences['panes'][name]=self.g.gtk_paned_get_position(self.panes[name])
        if self.layout_save_timer:
            self.g.cancel(self.layout_save_timer)
        self.layout_save_timer=self.g.later(250,self.flush_display_preferences)

    # ==========================================
    # Function: Commit one debounced batch of presentation preferences on the GTK thread.
    # Method: Clear the timer before reporting any filesystem error through the ordinary inline error path.
    # ==========================================
    def flush_display_preferences(self):
        self.layout_save_timer=None
        self.guard(self.persist_display_preferences)

    # ==========================================
    # Function: Keep global font size and pane geometry in one presentation-only file.
    # Method: Atomically write cached values without reading configuration or querying destroyed widgets.
    # ==========================================
    def persist_display_preferences(self):
        private_json(self.ui_path,self.ui_preferences)

    # ==========================================
    # Function: Recover a readable default layout while keeping the selected global font.
    # Method: Reapply native default positions, then persist all resulting pane values together.
    # ==========================================
    def reset_layout(self):
        self.start_layout_restore(use_saved=False)

    # ==========================================
    # Function: Build one independently collapsible native workbench module.
    # Method: Remember per-target expanded state without changing booking configuration or dirty flags.
    # ==========================================
    def section(self, parent, title, key, expanded):
        g=self.g
        expander=g.gtk_expander_new(title.encode())
        g.style(expander,'workbench-section')
        g.gtk_expander_set_expanded(expander,expanded)
        body=self.box(True,12)
        g.gtk_widget_set_margin_top(body,12)
        g.gtk_widget_set_margin_bottom(body,8)
        g.gtk_container_add(expander,body)
        self.pack(parent,expander)
        g.connect(expander,'notify::expanded',lambda *_:self.remember_fold(key,expander),count=3)
        return expander,body

    # ==========================================
    # Function: Keep folding preferences independent for each selected goal.
    # Method: Ignore programmatic loads and store only the live native expander state.
    # ==========================================
    def remember_fold(self, key, expander):
        if not self.loading and self.model.selected:
            self._folds.setdefault(self.model.selected,{})[key]=bool(self.g.gtk_expander_get_expanded(expander))

    # ==========================================
    # Function: Expose a goal's release, preparation lead, bounded cadence and clock-estimate options.
    # Method: Use persistent native controls and commit only validated local RushSettings values.
    # ==========================================
    def build_rush(self, parent):
        g=self.g
        self.rush_time_caption=self.pack(parent,self.label('抢购时刻 · YYYY-MM-DD HH:MM:SS','caption'))
        self.rush_at=self.pack(parent,self.entry(placeholder='2026-10-28 12:00:00'))
        row=self.pack(parent,self.box(spacing=12))
        self.rush_spins={}
        for key,caption,low,high,step in [('prewarm_seconds','提前准备 / 秒',5,600,5),
                ('first_second_interval_ms','首秒刷新间隔 / ms',200,1000,50),
                ('followup_interval_ms','随后刷新间隔 / ms',1000,15000,250),
                ('followup_seconds','随后加速时长 / 秒',0,30,1)]:
            widget=g.gtk_spin_button_new_with_range(low,high,step)
            self.rush_spins[key]=widget
            self.pack(row,self.field(caption,widget),True)
            self.edit_controls.append(widget)
            g.connect(widget,'value-changed',lambda *_:self.guard(self.commit_rush))
        clock_row=self.pack(parent,self.box(spacing=12))
        self.rush_sync=g.gtk_check_button_new_with_label('自动估算网站时间'.encode())
        self.pack(clock_row,self.rush_sync,True)
        self.rush_offset=g.gtk_spin_button_new_with_range(-5000,5000,10)
        self.pack(clock_row,self.field('手动补偿 / ms（正数表示服务器快）',self.rush_offset))
        self.rush_status=self.pack(parent,self.label('开启后保存并开始监测，将按此目标时间准备。','caption',wrap=True))
        self.pack(parent,self.label('首秒最多 4 次串行读取，账号内所有目标共享；随后恢复常规频率。校时有秒级误差，遇到限流仍会退避。','caption',wrap=True))
        self.edit_controls.extend([self.rush_at,self.rush_sync,self.rush_offset])
        g.connect(self.rush_switch,'notify::active',lambda *_:self.guard(self.commit_rush),count=3)
        g.connect(self.rush_sync,'toggled',lambda *_:self.guard(self.commit_rush))
        g.connect(self.rush_offset,'value-changed',lambda *_:self.guard(self.commit_rush))
        g.connect(self.rush_at,'activate',lambda *_:self.guard(self.commit_rush))
        g.connect(self.rush_at,'focus-out-event',lambda *_:self.rush_focus_out(),count=3,returns=True)

    # ==========================================
    # Function: Save per-target rush controls without discarding drafts or changing other goals.
    # Method: Validate the full setting atomically and reveal its module when enabling the top switch.
    # ==========================================
    def commit_rush(self):
        if self.loading or self.controller.busy or self.saving or not self.model.get():
            return
        self.editing()
        enabled=bool(self.g.gtk_switch_get_active(self.rush_switch))
        was_enabled=bool(self.model.get().get('rush',{}).get('enabled'))
        stamp=self.g.text(self.rush_at).strip() or None
        if not enabled and not self._rush_loaded.get('at') and stamp==self._rush_default_at:
            stamp=None
        self.model.rush({'enabled':enabled,'at':stamp,
                         **{key:self.g.gtk_spin_button_get_value_as_int(widget) for key,widget in self.rush_spins.items()},
                         'sync_clock':bool(self.g.gtk_toggle_button_get_active(self.rush_sync)),
                         'manual_offset_ms':self.g.gtk_spin_button_get_value_as_int(self.rush_offset)})
        if enabled and not was_enabled:
            self.g.gtk_expander_set_expanded(self.automation_expander,True)
            self.g.gtk_expander_set_expanded(self.rush_expander,True)
        self.render_schedule()
        self.render_rush()
        self.render_sidebar()

    # ==========================================
    # Function: Preserve native focus navigation after editing a release timestamp.
    # Method: Use the same guarded commit path as the repeat-time field.
    # ==========================================
    def rush_focus_out(self):
        self.guard(self.commit_rush)
        return False

    # ==========================================
    # Function: Show the selected goal's release phase and honest clock-estimate precision.
    # Method: Update only changed captions and derive the live countdown from a monotonic deadline.
    # ==========================================
    def render_rush(self):
        g=self.g
        enabled=bool((self.model.get() or {}).get('rush',{}).get('enabled'))
        g.style(self.rush_expander,'rush-enabled',enabled)
        caption='抢购功能设置 · 已开启' if enabled else '抢购功能设置'
        if caption!=getattr(self,'_rush_caption',None):
            self._rush_caption=caption
            g.gtk_expander_set_label(self.rush_expander,caption.encode())
        state=self.model.states.get(self.model.selected,{}).get('rush') if self.controller.busy else None
        if not state and self.controller.busy and self.model.repeating() and self.model.get():
            instances=[self.model.states[g['id']] for g in self.model.data['goals']
                       if g.get('repeat_id')==self.model.get()['id'] and g['id'] in self.model.states
                       and self.model.states[g['id']].get('status') in {'pending','rejected','preview'}
                       and self.model.states[g['id']].get('rush')]
            if instances:
                state=min(instances,key=lambda item:item['rush']['deadline_monotonic'])['rush']
        if state:
            remaining=max(0,state.get('deadline_monotonic',time.monotonic())-time.monotonic())
            text=f"{state['stage']} · 距开抢 {remaining:.1f} 秒\n{state['clock']}"
        else:
            status=self.model.states.get(self.model.selected,{}).get('status')
            text=('开启后保存并开始监测，将按此目标时间准备。' if not enabled else
                  '抢购已开启 · 等待下一次执行日或准备阶段。' if self.controller.busy else
                  f'本次执行：{STATUS.get(status,status)}' if status and status!='pending' else
                  '已开启 · 准备和开抢按此目标单独执行，尚未启动。')
        if text!=self._rush_status_text:
            self._rush_status_text=text
            g.label(self.rush_status,text)

    # ==========================================
    # Function: Attach retained top-level actions once.
    # Method: Route every operation through shared guards and native GTK lifecycle signals.
    # ==========================================
    def bind_actions(self):
        g=self.g
        actions={'new_goal':self.add_goal,'goal_up':lambda:self.move_goal(-1),'goal_down':lambda:self.move_goal(1),
                 'settings':self.open_settings,'save':self.save,'start':lambda:self.save(operation='run'),
                 'stop':self.controller.stop,'login':lambda:self.action('login'),'capture':self.controller.capture,
                 'dismiss_error':lambda:g.gtk_widget_hide(self.w['error_bar'])}
        for name,callback in actions.items():
            g.connect(self.w[name],'clicked',lambda *_,fn=callback:self.guard(fn))
        g.connect(self.w['goals'],'row-selected',self.row_selected,count=3)
        g.connect(self.window,'delete-event',lambda *_:self.request_close(),count=3,returns=True)
        g.connect(self.window,'destroy',lambda *_:self.on_destroy())

    # ==========================================
    # Function: Keep validation errors visible without opening another system-decorated dialog.
    # Method: Catch expected editor/account errors and preserve all local drafts.
    # ==========================================
    def guard(self, callback):
        try:
            return callback()
        except Exception as exc:
            self.error(exc)

    # ==========================================
    # Function: Display a concise inline error while retaining editable content.
    # Method: Use a plain-text error banner owned by the main window.
    # ==========================================
    def error(self, exc):
        self.errors.append(str(exc))
        self.g.label(self.w['error'],str(exc))
        self.g.gtk_widget_set_no_show_all(self.w['error_bar'],False)
        self.g.gtk_widget_show_all(self.w['error_bar'])
        self.g.gtk_widget_set_no_show_all(self.w['error_bar'],True)

    # ==========================================
    # Function: Reject editor mutations while browser work or durable saves are active.
    # Method: Use the same guard behind keyboard and pointer activation.
    # ==========================================
    def editing(self):
        if self.controller.busy or self.saving:
            raise ValueError('请先停止监测，再编辑预约目标')

    # ==========================================
    # Function: Commit valid local entry changes without rereading disk.
    # Method: Keep recorded goals immutable and update only changed sidebar captions.
    # ==========================================
    def metadata_changed(self):
        if self.loading or self.controller.busy or self.saving or not self.model.get():
            return
        self.guard(lambda:self.model.metadata(self.g.text(self.name_entry),self.g.text(self.date_entry)))
        self.render_sidebar()

    # ==========================================
    # Function: Commit the selected template's complete repeat controls.
    # Method: Validate time on activation/focus loss and retain at least one weekday.
    # ==========================================
    def commit_schedule(self):
        if self.loading or not self.model.repeating() or self.controller.busy or self.saving:
            return
        self.editing()
        days=[i for i,b in enumerate(self.week_buttons) if self.g.gtk_toggle_button_get_active(b)]
        self.model.schedule(days,self.g.text(self.repeat_at).strip(),self.g.gtk_spin_button_get_value_as_int(self.repeat_ahead),bool(self.g.gtk_switch_get_active(self.repeat_switch)))
        self.render_sidebar()
        self.render_schedule()

    # ==========================================
    # Function: Validate schedule time when leaving its entry without consuming focus navigation.
    # Method: Report errors inline and allow GTK's native focus event to continue.
    # ==========================================
    def schedule_focus_out(self):
        self.guard(self.commit_schedule)
        return False

    # ==========================================
    # Function: Prevent an empty weekly schedule when toggling the last weekday.
    # Method: Restore that native toggle under a load guard and keep prior configuration intact.
    # ==========================================
    def week_changed(self, day):
        if self.loading:
            return
        if not any(self.g.gtk_toggle_button_get_active(b) for b in self.week_buttons):
            self.loading=True
            self.g.gtk_toggle_button_set_active(self.week_buttons[day],True)
            self.loading=False
            self.g.label(self.w['status'],'每周至少选择一个自动预约日')
            return
        self.guard(self.commit_schedule)

    # ==========================================
    # Function: Populate the cached venue selector without reading availability.
    # Method: Keep directory IDs separate from display labels and existing targets.
    # ==========================================
    def render_catalog(self):
        g=self.g
        current=(g.gtk_combo_box_get_active_id(self.venue_combo) or b'').decode()
        self.loading=True
        self.venues={v['id']:v for v in (self.controller.catalog or {}).get('venues',[])}
        g.gtk_combo_box_text_remove_all(self.venue_combo)
        for identity,venue in self.venues.items():
            g.gtk_combo_box_text_append(self.venue_combo,identity.encode(),f"{venue['name']} · {venue.get('campus','')}".encode())
        preferred=next((key for key,v in self.venues.items() if v['name']==self.model.data['venue']),next(iter(self.venues),''))
        g.gtk_combo_box_set_active_id(self.venue_combo,(current if current in self.venues else preferred).encode())
        self.loading=False
        self.venue_changed()
        g.label(self.catalog_hint,f'已缓存 {len(self.venues)} 个场馆 · 选择场馆不查询实时空位')

    # ==========================================
    # Function: Load only projects belonging to the currently selected venue.
    # Method: Reuse cached metadata and preserve candidate lists and staged selections.
    # ==========================================
    def venue_changed(self):
        if self.loading:
            return
        g=self.g
        venue=self.venues.get((g.gtk_combo_box_get_active_id(self.venue_combo) or b'').decode())
        old=(g.gtk_combo_box_get_active_id(self.sport_combo) or b'').decode()
        self.loading=True
        g.gtk_combo_box_text_remove_all(self.sport_combo)
        names=[s['name'] for s in (venue or {}).get('sports',[])]
        for name in names:
            g.gtk_combo_box_text_append(self.sport_combo,name.encode(),name.encode())
        selected=old if old in names else self.model.data['sport'] if self.model.data['sport'] in names else names[0] if names else ''
        g.gtk_combo_box_set_active_id(self.sport_combo,selected.encode())
        self.loading=False
        self.refresh_courts()

    # ==========================================
    # Function: Resolve the selected builder route in memory.
    # Method: Require an exact cached venue/project pair and derive the existing safe booking URL.
    # ==========================================
    def context(self):
        identity=(self.g.gtk_combo_box_get_active_id(self.venue_combo) or b'').decode()
        sport=(self.g.gtk_combo_box_get_active_id(self.sport_combo) or b'').decode()
        venue=self.venues.get(identity)
        if not venue or sport not in {s['name'] for s in venue['sports']}:
            raise ValueError('请先登录并从缓存目录选择场馆和项目')
        return {'booking_url':booking_url(identity,sport),'venue':venue['name'],'sport':sport}

    # ==========================================
    # Function: Render selectable native court/time tiles with normal event bubbling.
    # Method: Keep GtkToggleButton state and let the surrounding GtkScrolledWindow receive wheel events.
    # ==========================================
    def build_choices(self, flow, values, buttons, selected, courts):
        g=self.g
        g.clear(flow)
        buttons.clear()
        for value in values:
            button=g.gtk_toggle_button_new_with_label(value.encode())
            g.style(button,'choice')
            g.gtk_widget_set_hexpand(button,True)
            g.gtk_toggle_button_set_active(button,value in selected)
            g.connect(button,'toggled',lambda *_,v=value,cs=courts:self.choice_changed(v,cs))
            g.gtk_container_add(flow,button)
            buttons[value]=button
        g.gtk_widget_show_all(flow)

    # ==========================================
    # Function: Keep wildcard and exact courts mutually exclusive.
    # Method: Adjust other native toggles under a load guard and update the selection caption.
    # ==========================================
    def choice_changed(self, value, courts):
        if self.loading:
            return
        if courts and self.g.gtk_toggle_button_get_active(self.court_buttons[value]):
            self.loading=True
            for name,button in self.court_buttons.items():
                if name!=value and (value==ANY_COURT or name==ANY_COURT):
                    self.g.gtk_toggle_button_set_active(button,False)
            self.loading=False
        self.selection_summary()

    # ==========================================
    # Function: Scope known court names to the selected venue/project.
    # Method: Read only in-memory caches and rebuild tiles only when their scope changes.
    # ==========================================
    def refresh_courts(self):
        if self.loading:
            return
        try:
            context=self.context()
        except ValueError:
            context={'venue':self.model.data['venue'],'sport':self.model.data['sport']}
        names=[ANY_COURT,*self.controller.courts.get(context_key(context['venue'],context['sport']),[])]
        for candidate in (self.model.get() or {}).get('candidates',[]):
            c=candidate.get('context') or self.model.data
            if (c['venue'],c['sport'])==(context['venue'],context['sport']):
                names.append(candidate['court'])
        names=list(dict.fromkeys(names))
        scope=(context['venue'],context['sport'],tuple(names))
        if scope!=self._court_scope:
            self._court_scope=scope
            self.build_choices(self.court_flow,names,self.court_buttons,{ANY_COURT},True)
        self.selection_summary()
        self.set_busy()

    # ==========================================
    # Function: Reveal the initially selected time without scrolling the whole workbench.
    # Method: Position the native slot adjustment after GTK has allocated its wrapping rows.
    # ==========================================
    def reveal_selected_hours(self):
        if self.destroyed:
            return
        selected=self.selected_values(self.hour_buttons)
        if selected:
            adjustment=self.g.gtk_scrolled_window_get_vadjustment(self.hour_scroll)
            rows=(len(self.hour_buttons)+1)//2
            row=list(self.hour_buttons).index(selected[0])//2
            height=self.g.gtk_adjustment_get_upper(adjustment)/max(1,rows)
            self.g.gtk_adjustment_set_value(adjustment,max(0,row-1)*height)

    # ==========================================
    # Function: Read selected values in displayed priority order.
    # Method: Query native toggle state rather than maintaining a second selection cache.
    # ==========================================
    def selected_values(self, buttons):
        return [value for value,button in buttons.items() if self.g.gtk_toggle_button_get_active(button)]

    # ==========================================
    # Function: Keep staged venue/court/time counts explicit.
    # Method: Derive the caption solely from local selected controls and target drafts.
    # ==========================================
    def selection_summary(self):
        count=len(self.model.drafts.get(self.model.selected,[]))
        self.g.label(self.selection_hint,f"已加入 {count} 个场馆 / 项目 · 已选 {len(self.selected_values(self.court_buttons))} 个场地、{len(self.selected_values(self.hour_buttons))} 个时段")

    # ==========================================
    # Function: Render all staged venues in a native wrapping flow layout.
    # Method: Show court labels and an explicit remove button without clipping long selections.
    # ==========================================
    def render_drafts(self):
        g=self.g
        g.clear(self.venue_flow)
        for index,item in enumerate(self.model.drafts.get(self.model.selected,[])):
            chip=self.box(spacing=5,css='venue-chip')
            c=item['context']
            text=f"{c['venue']} · {c['sport']}\n{'、'.join(item['courts'])}"
            self.pack(chip,self.label(text,wrap=True),True)
            self.pack(chip,self.button('×',lambda i=index:self.remove_draft(i),'icon'))
            g.gtk_container_add(self.venue_flow,chip)
        g.gtk_widget_show_all(self.venue_flow)
        self.selection_summary()

    # ==========================================
    # Function: Stage the currently selected venue and court choices.
    # Method: Mutate only the selected goal's local draft.
    # ==========================================
    def add_venue(self):
        self.editing()
        self.model.stage(self.context(),self.selected_values(self.court_buttons))
        self.render_drafts()

    # ==========================================
    # Function: Remove one staged context without altering saved candidates.
    # Method: Respect busy-state protection on both pointer and keyboard paths.
    # ==========================================
    def remove_draft(self, index):
        self.editing()
        self.model.drafts[self.model.selected].pop(index)
        self.render_drafts()

    # ==========================================
    # Function: Add all staged alternatives atomically.
    # Method: Use the pure workspace validator before rebuilding candidate rows.
    # ==========================================
    def add_candidates(self):
        self.editing()
        self.commit_current()
        contexts=self.model.drafts.get(self.model.selected) or [{'context':self.context(),'courts':self.selected_values(self.court_buttons)}]
        self.model.add_candidates(contexts,self.selected_values(self.hour_buttons),bool(self.g.gtk_toggle_button_get_active(self.bundle)))
        self.render_candidates()
        self.render_drafts()
        self.render_sidebar()
        self.g.gtk_popover_popdown(self.builder_popover)

    # ==========================================
    # Function: Update sidebar rows without resetting scroll position on status events.
    # Method: Reuse existing GTK rows; rebuild only after an explicit membership/order change.
    # ==========================================
    def render_sidebar(self):
        g=self.g
        items=self.model.items()
        order=[key for key,item in items]
        if order!=self._sidebar_order:
            self.loading=True
            adjustment=g.gtk_scrolled_window_get_vadjustment(self.w['goal_scroll'])
            position=g.gtk_adjustment_get_value(adjustment)
            g.clear(self.w['goals'])
            self.sidebar_rows={}
            self._sidebar_captions,self._row_tones={},{}
            for key,item in items:
                row=g.gtk_list_box_row_new()
                box=self.box(spacing=2)
                caption=self.box(True,5)
                name=self.pack(caption,self.label('', 'goal-name',ellipsis=True))
                detail=self.pack(caption,self.label('', 'caption',ellipsis=True))
                self.pack(box,caption,True)
                plus=self.pack(box,self.button('+',lambda identity=key:self.copy_goal(identity),'icon'))
                delete=self.pack(box,self.button('×',lambda identity=key:self.delete_goal(identity),'icon'))
                g.gtk_widget_set_tooltip_text(plus,'复制为该目标下一天的独立目标'.encode())
                g.gtk_widget_set_tooltip_text(delete,'删除可编辑目标，保留订单记录'.encode())
                g.gtk_container_add(row,box)
                g.gtk_container_add(self.w['goals'],row)
                self.sidebar_rows[key]=(row,name,detail,plus,delete)
            self._sidebar_order=order
            g.gtk_widget_show_all(self.w['goals'])
            if self.model.selected in self.sidebar_rows:
                g.gtk_list_box_select_row(self.w['goals'],self.sidebar_rows[self.model.selected][0])
            g.gtk_adjustment_set_value(adjustment,position)
            self.loading=False
        for key,item in items:
            row,name,detail,plus,delete=self.sidebar_rows[key]
            state=self.model.states.get(key,{})
            if key.startswith('rule:'):
                days=item['weekdays']
                schedule='每天' if len(days)==7 else '周'+'、'.join('一二三四五六日'[d] for d in days)
                at=item.get('rush',{}).get('at') if item.get('rush',{}).get('enabled') else item['at']
                text=f"{schedule} {at} · {'重复' if item['enabled'] else '已暂停'}"
            else:
                status='检查中' if state.get('watch_state')=='checking' else '退避等待' if state.get('watch_state')=='backoff' else STATUS.get(state.get('status','pending'),'需处理')
                if status=='等待空位' and not (self.controller.busy and self.controller.operation=='run'):
                    status='待启动'
                text=f"{self.model.day(key)} · {status}"
            if item.get('rush',{}).get('enabled'):
                text+=' · 抢购'
            caption=(item['name'] or '未命名目标',text)
            if self._sidebar_captions.get(key)!=caption:
                g.label(name,caption[0])
                g.label(detail,caption[1])
                self._sidebar_captions[key]=caption
        done=sum(s.get('status')=='submitted' for s in self.model.states.values())
        rules=sum(r['enabled'] for r in self.model.data['repeat_rules'])
        g.label(self.w['summary'],f'{len(items)} 个目标 · {done} 个已完成 · {rules} 个重复预约已开启')
        g.label(self.w['goal_count'],f'预约目标  {len(items)}')
        self.paint_status_rows()
        self.set_busy()

    # ==========================================
    # Function: Distinguish running, recurring, planned, completed and attention-needed goals visibly.
    # Method: Apply cached native CSS classes with tinted surfaces and persistent colored left accents.
    # ==========================================
    def paint_status_rows(self):
        running=self.controller.busy and self.controller.operation=='run'
        for key,item in self.model.items():
            status=self.model.states.get(key,{}).get('status','pending')
            if self.model.repeating(key):
                tone='recurring' if item['enabled'] else 'paused'
            elif status=='submitted':
                tone='completed'
            elif status in {'unknown','needs_verification','conflict','expired'}:
                tone='attention'
            elif running and status in {'pending','preview','rejected'}:
                tone='running'
            elif item.get('repeat_id'):
                tone='recurring'
            else:
                tone='planned'
            if self._row_tones.get(key)!=tone and key in self.sidebar_rows:
                row=self.sidebar_rows[key][0]
                old=self._row_tones.get(key)
                if old:
                    self.g.style(row,old,False)
                self.g.style(row,tone)
                self._row_tones[key]=tone

    # ==========================================
    # Function: Switch targets without disturbing unfinished venue drafts.
    # Method: Commit current fields and resolve the native row by stable sidebar identity.
    # ==========================================
    def row_selected(self, box, row, unused):
        if self.loading or not row:
            return
        identity=next((key for key,values in self.sidebar_rows.items() if values[0]==row),None)
        if identity and identity!=self.model.selected:
            try:
                self.commit_current()
            except Exception as exc:
                self.loading=True
                if self.model.selected in self.sidebar_rows:
                    self.g.gtk_list_box_select_row(box,self.sidebar_rows[self.model.selected][0])
                self.loading=False
                self.error(exc)
                return
            self.model.selected=identity
            self.load_editor()

    # ==========================================
    # Function: Persist current entry values into the pure local editor model.
    # Method: Protect recorded targets and apply weekly controls only for templates.
    # ==========================================
    def commit_current(self):
        if self.loading or self.controller.busy or self.saving or not self.model.get():
            return
        if not self.model.repeating() and self.model.states.get(self.model.selected,{}).get('status','pending') not in {'pending','preview'}:
            return
        self.model.metadata(self.g.text(self.name_entry),self.g.text(self.date_entry))
        if self.model.repeating():
            self.commit_schedule()
        self.commit_rush()

    # ==========================================
    # Function: Load a selected goal into retained editor controls.
    # Method: Change values under a signal guard and preserve all other targets/drafts.
    # ==========================================
    def load_editor(self):
        g=self.g
        self.loading=True
        item=self.model.get()
        g.text(self.name_entry,item['name'] if item else '')
        g.text(self.date_entry,self.model.day())
        repeating=self.model.repeating()
        g.gtk_button_set_label(self.repeat_button,('切换为普通目标' if repeating else '设为每周重复').encode())
        g.label(self.date_caption,'预计预约日期' if repeating else '预约日期')
        if repeating:
            g.text(self.repeat_at,item['at'])
            g.gtk_spin_button_set_value(self.repeat_ahead,item['days_ahead'])
            g.gtk_switch_set_active(self.repeat_switch,item['enabled'])
            for day,button in enumerate(self.week_buttons):
                g.gtk_toggle_button_set_active(button,day in item['weekdays'])
        rush=(item or {}).get('rush',asdict(RushSettings()))
        self._rush_loaded=dict(rush)
        default_at=(item['at']+':00' if repeating else
                    (datetime.now(ZoneInfo(self.model.data['timezone']))+timedelta(hours=1)).replace(minute=0,second=0,microsecond=0).isoformat())
        self._rush_default_at=default_at
        g.text(self.rush_at,rush.get('at') or default_at)
        g.gtk_switch_set_active(self.rush_switch,bool(rush['enabled']))
        g.gtk_toggle_button_set_active(self.rush_sync,bool(rush['sync_clock']))
        g.gtk_spin_button_set_value(self.rush_offset,rush['manual_offset_ms'])
        for key,widget in self.rush_spins.items():
            g.gtk_spin_button_set_value(widget,rush[key])
        g.label(self.rush_time_caption,'执行日抢购时刻 · HH:MM:SS' if repeating else '抢购时刻 · YYYY-MM-DD HH:MM:SS（北京时间）')
        folds=self._folds.setdefault(self.model.selected,{})
        for key,widget,default in [('repeat',self.repeat_expander,repeating),('rush',self.rush_expander,rush['enabled']),
                                   ('automation',self.automation_expander,repeating or rush['enabled']),
                                   ('target',self.target_expander,True),('builder-venue',self.venue_expander,True),
                                   ('builder-choices',self.choices_expander,True)]:
            g.gtk_expander_set_expanded(widget,folds.get(key,default))
        self.loading=False
        self.render_schedule()
        self.render_rush()
        self.render_candidates()
        self.render_drafts()
        self.refresh_courts()
        g.gtk_adjustment_set_value(g.gtk_scrolled_window_get_vadjustment(self.w['editor_scroll']),0)
        self.set_busy()
        if self.model.selected in self.sidebar_rows:
            self.loading=True
            g.gtk_list_box_select_row(self.w['goals'],self.sidebar_rows[self.model.selected][0])
            self.loading=False

    # ==========================================
    # Function: Show repeat mode and the enabled blue workspace outline.
    # Method: Use a native revealer and CSS class rather than changing window decorations.
    # ==========================================
    def render_schedule(self):
        g=self.g
        item=self.model.get()
        repeating=self.model.repeating()
        g.gtk_revealer_set_reveal_child(self.repeat_revealer,repeating)
        active=bool(repeating and item['enabled'])
        g.style(self.w['editor'],'recurring',active)
        g.label(self.mode,'每周重复 · 已开启' if active else '每周重复 · 已暂停' if repeating else '普通目标 · 单次预约')
        if repeating:
            g.text(self.date_entry,self.model.day())
            rush=item.get('rush',{})
            message=(f"{rush['at']} 开抢 · 提前 {rush['prewarm_seconds']:g} 秒生成和准备" if rush.get('enabled') else f"{item['at']} 生成预约")
            g.label(self.schedule_hint,f"按执行日的星期判断 · {message} · 预约往后 {item['days_ahead']} 天")

    # ==========================================
    # Function: Render ranked candidate rows only after a goal or candidates change.
    # Method: Keep venue, exact court, and hours together in one readable native row.
    # ==========================================
    def render_candidates(self):
        g=self.g
        g.clear(self.candidate_list)
        candidates=(self.model.get() or {}).get('candidates',[])
        for index,candidate in enumerate(candidates):
            row=g.gtk_list_box_row_new()
            box=self.box(spacing=12)
            self.pack(box,self.label(f'{index+1:02d}','caption'))
            info=self.box(True,4)
            context=candidate.get('context') or self.model.data
            self.pack(info,self.label(f"{context['venue']} · {context['sport']}",ellipsis=True))
            self.pack(info,self.label(f"{candidate['court']}  ·  {' / '.join(candidate['slots'])}",'caption',ellipsis=True))
            self.pack(box,info,True)
            g.gtk_container_add(row,box)
            g.gtk_container_add(self.candidate_list,row)
        if not candidates:
            row=g.gtk_list_box_row_new()
            g.gtk_container_add(row,self.label('点击“添加场馆与时段”，创建第一个备选','muted'))
            g.gtk_container_add(self.candidate_list,row)
        g.gtk_widget_show_all(self.candidate_list)
        g.gtk_widget_set_size_request(self.candidate_scroll,-1,248)

    # ==========================================
    # Function: Create another independent one-time target.
    # Method: Preserve the current editor before switching selection.
    # ==========================================
    def add_goal(self):
        self.editing()
        self.commit_current()
        self.model.add()
        self.render_sidebar()
        self.load_editor()

    # ==========================================
    # Function: Copy the clicked row's own next day.
    # Method: Commit the active editor without selecting or rewriting the source first.
    # ==========================================
    def copy_goal(self, identity):
        self.editing()
        self.commit_current()
        self.model.copy_next(identity)
        self.render_sidebar()
        self.load_editor()
        self.g.label(self.w['status'],'已创建下一天的独立目标 · 请保存设置')

    # ==========================================
    # Function: Delete the clicked editable row while retaining durable records.
    # Method: Use the pure model's identity-based order protection.
    # ==========================================
    def delete_goal(self, identity):
        self.editing()
        self.commit_current()
        self.model.remove(identity)
        self.render_sidebar()
        self.load_editor()

    # ==========================================
    # Function: Reorder the selected target without changing its identity.
    # Method: Persist local fields and swap adjacent items in their own target category.
    # ==========================================
    def move_goal(self, delta):
        self.editing()
        self.commit_current()
        self.model.move(delta)
        self.render_sidebar()

    # ==========================================
    # Function: Convert the current target between one-time and weekly modes.
    # Method: Replace the source rather than leave an extra executable booking behind.
    # ==========================================
    def toggle_repeat(self):
        self.editing()
        self.commit_current()
        self.model.toggle_repeat()
        self.render_sidebar()
        self.load_editor()

    # ==========================================
    # Function: Change a selected alternative's priority.
    # Method: Use native list selection and preserve the row after swapping candidates.
    # ==========================================
    def move_candidate(self, delta):
        self.editing()
        row=self.g.gtk_list_box_get_selected_row(self.candidate_list)
        if row and (self.model.get() or {}).get('candidates'):
            index=self.model.move(delta,self.g.gtk_list_box_row_get_index(row))
            self.render_candidates()
            self.g.gtk_list_box_select_row(self.candidate_list,self.g.gtk_list_box_get_row_at_index(self.candidate_list,index))

    # ==========================================
    # Function: Remove a selected unsubmitted alternative.
    # Method: Apply order protection before altering the candidate list.
    # ==========================================
    def delete_candidate(self):
        self.editing()
        self.model.editable()
        row=self.g.gtk_list_box_get_selected_row(self.candidate_list)
        values=self.model.get()['candidates']
        if row and values:
            values.pop(self.g.gtk_list_box_row_get_index(row))
            self.model.dirty=True
            self.render_candidates()
            self.render_sidebar()

    # ==========================================
    # Function: Update interaction locks only when the effective busy/editor state changes.
    # Method: Keep stop, capture, scrolling, and goal inspection responsive during browser work.
    # ==========================================
    def set_busy(self):
        g=self.g
        busy=self.controller.busy or self.saving
        recorded=not self.model.repeating() and self.model.states.get(self.model.selected,{}).get('status','pending') not in {'pending','preview'}
        signature=(busy,self.controller.operation,self.model.selected,recorded,self._court_scope,len(self.sidebar_rows))
        if signature==self._busy:
            return
        self._busy=signature
        self.render_inventory(self.snapshot)
        editable=not busy and not recorded and self.model.get() is not None
        for widget in self.edit_controls:
            g.gtk_widget_set_sensitive(widget,editable)
        for button in [*self.court_buttons.values(),*self.hour_buttons.values()]:
            g.gtk_widget_set_sensitive(button,editable)
        g.gtk_widget_set_sensitive(self.name_entry,editable)
        g.gtk_widget_set_sensitive(self.date_entry,editable and not self.model.repeating())
        for name in ['save','start','new_goal','login','goal_up','goal_down']:
            g.gtk_widget_set_sensitive(self.w[name],not busy)
        g.gtk_widget_set_sensitive(self.w['settings'],True)
        self.set_settings_busy()
        for key,(row,name,detail,plus,delete) in self.sidebar_rows.items():
            g.gtk_widget_set_sensitive(plus,not busy)
            g.gtk_widget_set_sensitive(delete,not busy)
        g.gtk_widget_set_visible(self.w['capture'],self.controller.busy and self.controller.operation=='login')
        g.gtk_widget_set_visible(self.w['stop'],self.controller.busy)
        self.paint_status_rows()
        g.label(self.w['run_badge'],'保存中' if self.saving else '监测中' if self.controller.operation=='run' else '登录中' if self.controller.busy else '待命')

    # ==========================================
    # Function: Restore optional native controls after the initial show-all traversal.
    # Method: Keep login capture and repeat settings hidden unless relevant.
    # ==========================================
    def refresh_visibility(self):
        self._busy=None
        self.set_busy()
        self.render_schedule()
        self.g.gtk_widget_hide(self.w['error_bar'])

    # ==========================================
    # Function: Launch an explicitly requested browser action.
    # Method: Delegate to the existing serialized worker without blocking GTK.
    # ==========================================
    def action(self, operation):
        self.controller.start(operation)
        if operation=='run':
            self.monitor_started=True
            self.render_inventory(None)
        self.set_busy()

    # ==========================================
    # Function: Persist a validated working copy on a background thread.
    # Method: Disable edits until the durable save completes, then dispatch the requested operation.
    # ==========================================
    def save(self, operation=None):
        self.editing()
        self.commit_current()
        payload=self.model.payload()
        self.saving=True
        self.set_busy()

        # ==========================================
        # Function: Keep config and journal I/O outside GTK signal handlers.
        # Method: Send plain completion/error values through the existing controller queue.
        # ==========================================
        def persist():
            try:
                self.controller.save(payload)
                states=self.controller.state()['goals']
                self.controller.emit('gtk_saved',{'operation':operation,'states':states})
            except Exception as exc:
                self.controller.emit('gtk_save_failed',str(exc))

        self.save_worker=threading.Thread(target=persist,daemon=True,name='autoreserve-save')
        self.save_worker.start()

    # ==========================================
    # Function: Create a GTK child window with only a close action before first mapping.
    # Method: Attach a client-side GtkHeaderBar and transient relationship before showing any widget.
    # ==========================================
    def child_window(self, title, width, height):
        g=self.g
        window=g.gtk_window_new(0)
        g.style(window,self.font_scope)
        g.gtk_window_set_title(window,b'AutoReserve')
        g.gtk_window_set_transient_for(window,self.window)
        g.gtk_window_set_default_size(window,width,height)
        g.gtk_window_set_resizable(window,False)
        header=g.gtk_header_bar_new()
        g.gtk_header_bar_set_title(header,title.encode())
        g.gtk_header_bar_set_decoration_layout(header,b':close')
        g.gtk_header_bar_set_show_close_button(header,True)
        g.style(header,'reserve-header')
        g.gtk_window_set_titlebar(window,header)
        body=self.box(True,14,'settings-body')
        g.gtk_container_add(window,body)
        return window,body,header

    # ==========================================
    # Function: Present global typography/layout controls alongside protected execution settings.
    # Method: Apply display choices immediately and keep booking edits disabled during active work.
    # ==========================================
    def open_settings(self):
        if self.settings_window:
            self.g.gtk_window_present(self.settings_window)
            return
        if not self.controller.busy and not self.saving:
            self.commit_current()
        g=self.g
        window,body,header=self.child_window('设置',650,620)
        self.settings_window,self.settings_header=window,header
        self.settings_fields={}
        content=self.box(True,14)
        self.pack(body,self.scroller(content,160),True)
        self.pack(content,self.label('界面设置','section-title'))
        self.pack(content,self.label('字号作用于整个程序。字号与拖动后的布局自动保存，不影响预约参数。','caption',wrap=True))
        display=self.pack(content,self.box(spacing=16))
        self.pack(display,self.label('全局字号 / px'),True)
        self.font_spin=g.gtk_spin_button_new_with_range(11,20,1)
        g.gtk_spin_button_set_value(self.font_spin,self.font_size)
        self.pack(display,self.font_spin)
        g.connect(self.font_spin,'value-changed',lambda widget,*_:self.guard(
            lambda:self.set_font_size(g.gtk_spin_button_get_value_as_int(widget))))
        self.pack(display,self.button('恢复默认布局',self.reset_layout,'secondary'))
        self.pack(content,self.label('执行设置','section-title'))
        self.pack(content,self.label('运行中可调整界面；修改预约参数前请先停止监测。','muted',wrap=True))
        specs=[('普通目标启动时间 · 北京时间，留空立即','start_at'),('全局读取间隔 / 秒','poll_interval_seconds'),
               ('额外随机延迟 / %','poll_jitter_ratio'),('异常退避上限 / 秒','backoff_max_seconds'),
               ('单个订单金额上限 / 元 · 0 为无上限','max_total_price'),('普通模式监测时长 / 秒 · 0 为无上限','max_poll_seconds')]
        for caption,key in specs:
            value=self.model.data[key]
            value=value*100 if key=='poll_jitter_ratio' else value if value is not None else ''
            control=self.entry(value,width=12)
            self.settings_fields[key]=control
            row=self.pack(content,self.box(spacing=20))
            self.pack(row,self.label(caption),True)
            self.pack(row,control)
        for caption,key,invert in [('全部目标完成后停止','monitor_until_complete',False),('显示预约浏览器','headless',True)]:
            control=g.gtk_check_button_new_with_label(caption.encode())
            g.gtk_toggle_button_set_active(control,not self.model.data[key] if invert else self.model.data[key])
            self.pack(content,control)
            self.settings_fields[key]=control
        self.settings_save=self.pack(body,self.button('保存执行设置',self.apply_settings,'primary'))
        self.set_settings_busy()
        g.connect(window,'destroy',lambda *_:self.settings_destroyed())
        g.gtk_widget_show_all(window)
        g.gtk_window_present(window)

    # ==========================================
    # Function: Clear borrowed settings pointers when its native window closes.
    # Method: Avoid late save completions touching destroyed child widgets.
    # ==========================================
    def settings_destroyed(self):
        self.settings_window=None
        self.settings_fields={}
        self.font_spin=None

    # ==========================================
    # Function: Keep display settings accessible while reservation parameters are locked.
    # Method: Toggle only booking fields and their save action, leaving global font and layout controls interactive.
    # ==========================================
    def set_settings_busy(self):
        if self.settings_window:
            editable=not self.controller.busy and not self.saving
            for widget in self.settings_fields.values():
                self.g.gtk_widget_set_sensitive(widget,editable)
            self.g.gtk_widget_set_sensitive(self.settings_save,editable)

    # ==========================================
    # Function: Validate child-window settings before applying them to the shared workspace.
    # Method: Parse typed values into a proposed config and save asynchronously.
    # ==========================================
    def apply_settings(self):
        self.editing()
        g=self.g
        data=dict(self.model.data)
        for key,widget in self.settings_fields.items():
            if key in {'headless','monitor_until_complete'}:
                value=bool(g.gtk_toggle_button_get_active(widget))
                data[key]=not value if key=='headless' else value
            else:
                value=g.text(widget).strip()
                data[key]=(value or None) if key=='start_at' else value if key=='max_total_price' else float(value)/(100 if key=='poll_jitter_ratio' else 1)
        self.model.data=asdict(config_from_dict(data))
        self.model.dirty=True
        if self.settings_window:
            g.gtk_widget_set_sensitive(self.settings_save,False)
        self.save()

    # ==========================================
    # Function: Display recent logs continuously in the resizable execution-record card.
    # Method: Keep exactly the latest fifty logical lines and always follow the newest line after native layout.
    # ==========================================
    def render_logs(self):
        g=self.g
        adjustment=g.gtk_scrolled_window_get_vadjustment(self.w['log_scroll'])
        self.logs='\n'.join(self.logs).splitlines()[-50:]
        buffer=g.gtk_text_view_get_buffer(self.w['logs'])
        g.gtk_text_buffer_set_text(buffer,'\n'.join(self.logs).encode(),-1)
        end=(C.c_void_p*16)()
        g.gtk_text_buffer_get_end_iter(buffer,C.byref(end))
        g.gtk_text_buffer_place_cursor(buffer,C.byref(end))
        mark=g.gtk_text_buffer_get_insert(buffer)
        g.gtk_text_view_scroll_to_mark(self.w['logs'],mark,0,True,0,1)

        # ==========================================
        # Function: Settle the newest line at the bottom after GTK finishes measuring the text.
        # Method: Use a persistent text mark and clamp the native adjustment after layout.
        # ==========================================
        def settle():
            if not self.destroyed:
                maximum=g.gtk_adjustment_get_upper(adjustment)-g.gtk_adjustment_get_page_size(adjustment)
                g.gtk_text_view_scroll_to_mark(self.w['logs'],mark,0,True,0,1)
                g.gtk_adjustment_set_value(adjustment,max(0,maximum))

        g.later(25,settle)

    # ==========================================
    # Function: Keep live availability visible in its independently resizable native card.
    # Method: Repaint only changed in-memory snapshots; never request availability from UI rendering.
    # ==========================================
    def render_inventory(self, shot):
        running=self.controller.busy and self.controller.operation=='run'
        signature=(shot,running,self.monitor_started)
        if signature==self._inventory_signature and self.g.children(self.w['inventory']):
            return
        self._inventory_signature=signature
        self.snapshot=shot
        g=self.g
        g.clear(self.w['inventory'])
        if not shot:
            g.label(self.w['inventory_scope'],'监测已启动 · 等待首次结果' if running else
                    '本次尚未取得空位结果' if self.monitor_started else '尚未开始监测')
            caption=('正在连接场馆并等待允许的读取时间，取得空位后会自动更新。' if running else
                     '开始监测后显示最近一次空位结果\n编辑场馆和时段不会读取空位')
            self.pack(self.w['inventory'],self.label(caption,'muted',wrap=True))
        else:
            g.label(self.w['inventory_scope'],f"{shot.get('venue','')} / {shot.get('sport','')} / {shot.get('active_date','')}")
            grid=g.gtk_grid_new()
            g.gtk_grid_set_column_spacing(grid,5)
            g.gtk_grid_set_row_spacing(grid,4)
            for column,caption in enumerate(['时段',*shot.get('columns',[])]):
                g.gtk_grid_attach(grid,self.label(caption,'inventory-cell'),column,0,1,1)
            for row,(hour,cells) in enumerate(zip(shot.get('times',[]),shot.get('rows',[])),1):
                g.gtk_grid_attach(grid,self.label(hour,'inventory-cell'),0,row,1,1)
                for column,state in enumerate(cells,1):
                    label=self.label({'available':'可预约','selected':'已选择'}.get(state,'—'),'inventory-cell')
                    if state=='available':
                        g.style(label,'available')
                    g.gtk_grid_attach(grid,label,column,row,1,1)
            self.pack(self.w['inventory'],grid)
        g.gtk_widget_show_all(self.w['inventory'])

    # ==========================================
    # Function: Notify confirmed orders without stopping other targets.
    # Method: Use one nonmodal close-only GTK window and deduplicate by goal/order identity.
    # ==========================================
    def show_completion(self, item):
        detail=item.get('detail',{})
        identity=(item['id'],str(detail.get('order_id','')))
        if not detail.get('order_id') or identity in self._shown_orders:
            return
        self._shown_orders.add(identity)
        g=self.g
        if not self.notice_window:
            window,body,header=self.child_window('预约成功',500,330)
            self.notice_window=window
            self.notice_label=self.pack(body,self.label('',wrap=True))
            self.pack(body,self.label('订单已提交，未付款。其他未完成目标继续监测。','caption',wrap=True))
            self.pack(body,self.button('知道了',lambda:g.gtk_widget_destroy(window),'primary'))
            g.connect(window,'destroy',lambda *_:self.notice_destroyed())
        self.notices[identity]=f"{item.get('name','预约目标')}\n{detail.get('venue','')} · {detail.get('sport','')} · {detail.get('court','')}\n{detail.get('date','')}  {' / '.join(detail.get('slots',[]))}\n订单号：{detail['order_id']}"
        g.label(self.notice_label,'\n\n'.join(list(self.notices.values())[-6:]))
        g.gtk_widget_show_all(self.notice_window)
        g.gtk_window_present(self.notice_window)

    # ==========================================
    # Function: Release notification pointers after closing its native window.
    # Method: Preserve global deduplication while clearing the visible notification group.
    # ==========================================
    def notice_destroyed(self):
        self.notice_window=None
        self.notices={}

    # ==========================================
    # Function: Drain backend events without I/O or full workspace reconstruction per tick.
    # Method: Bound each batch by time, coalesce progress, and process all completion events.
    # ==========================================
    def tick(self):
        if self.destroyed:
            return False
        latest,latest_shot,logs,until=None,None,False,time.monotonic()+0.006
        for _ in range(160):
            try:
                kind,value=self.controller.events.get_nowait()
            except queue.Empty:
                break
            if kind=='log':
                stamp=datetime.fromisoformat(value['time']).strftime('%H:%M:%S')
                self.logs.append(f"{stamp}  {value['message']}")
                self.g.label(self.w['status'],value['message'])
                logs=True
            elif kind=='progress':
                latest=value
                if value.get('snapshot') is not None:
                    latest_shot=value['snapshot']
            elif kind=='busy' and value=='run':
                latest,latest_shot=None,None
                self.monitor_started=True
                self.render_inventory(None)
            elif kind=='goal_completed':
                self.show_completion(value)
            elif kind=='states':
                self.model.states.update({s['id']:s for s in value})
                self.render_sidebar()
            elif kind=='workspace':
                self.model.data=value
                if not self.model.get():
                    self.model.selected=self.model.items()[0][0] if self.model.items() else None
                    self.load_editor()
                self.render_sidebar()
            elif kind=='catalog':
                self.render_catalog()
            elif kind=='courts':
                self.refresh_courts()
            elif kind=='auth':
                self.g.label(self.w['auth'],value)
            elif kind=='gtk_saved':
                self.saving=False
                self.model.dirty=False
                self.model.states={s['id']:s for s in value['states']}
                self.render_sidebar()
                self.g.label(self.w['status'],'设置已保存 · 仅点击开始监测后执行预约')
                if self.settings_window:
                    self.g.gtk_widget_destroy(self.settings_window)
                if value['operation']=='close':
                    self.destroy()
                    return False
                if value['operation']:
                    self.guard(lambda:self.action(value['operation']))
            elif kind=='gtk_save_failed':
                self.saving=False
                self.error(value)
                if self.settings_window:
                    self.g.gtk_widget_set_sensitive(self.settings_save,True)
            elif kind=='finished':
                self.next_read_at=None
            if time.monotonic()>=until:
                break
        if latest:
            self.model.states.update({s['id']:s for s in latest.get('goals',[])})
            if latest_shot is not None:
                self.render_inventory(latest_shot)
            self.next_read_at=latest.get('monitor',{}).get('next_read_at')
            self.render_sidebar()
        if logs:
            self.render_logs()
        self.set_busy()
        self.render_rush()
        if self.next_read_at and self.controller.busy:
            text=f'{self.controller.message} · 下次读取约 {max(0,int(self.next_read_at-time.time()))} 秒后'
            if text!=getattr(self,'_countdown',None):
                self._countdown=text
                self.g.label(self.w['status'],text)
        if self.closing and not self.controller.busy and not self.saving:
            self.destroy()
            return False
        return not self.destroyed

    # ==========================================
    # Function: Close cooperatively during a submission or save.
    # Method: Wait for the worker result and ask about unsaved idle edits in a close-only native window.
    # ==========================================
    def request_close(self):
        if self.controller.busy or self.saving:
            self.closing=True
            self.controller.stop()
            self.g.label(self.w['status'],'正在停止，等待本次执行结果后关闭')
        elif self.model.dirty:
            self.confirm_close()
        else:
            self.destroy()
        return True

    # ==========================================
    # Function: Preserve unsaved drafts when closing the idle client.
    # Method: Offer save, discard, or cancel in a native transient without minimize/maximize actions.
    # ==========================================
    def confirm_close(self):
        if self.confirm_window:
            self.g.gtk_window_present(self.confirm_window)
            return
        g=self.g
        window,body,header=self.child_window('尚未保存',420,180)
        self.confirm_window=window
        self.pack(body,self.label('预约目标有未保存的修改。',wrap=True))
        row=self.pack(body,self.box())
        self.pack(row,self.button('取消',lambda:g.gtk_widget_destroy(window)))
        self.pack(row,self.button('直接退出',self.destroy))
        self.pack(row,self.button('保存并退出',lambda:self.save(operation='close'),'primary'))
        g.connect(window,'destroy',lambda *_:setattr(self,'confirm_window',None))
        g.gtk_widget_show_all(window)

    # ==========================================
    # Function: Destroy the native workspace only after account work has stopped.
    # Method: Close owned transient windows and let GTK release native widgets and signals.
    # ==========================================
    def destroy(self):
        if self.destroyed:
            return
        for window in [self.settings_window,self.notice_window,self.confirm_window]:
            if window:
                self.g.gtk_widget_destroy(window)
        self.g.gtk_widget_destroy(self.window)

    # ==========================================
    # Function: Retire UI timers and exit the registered application safely.
    # Method: Mark destruction before a pending GLib tick can touch borrowed widget pointers.
    # ==========================================
    def on_destroy(self):
        self.destroyed=True
        if self.layout_restore_timer:
            self.g.cancel(self.layout_restore_timer)
            self.layout_restore_timer=None
        if self.layout_save_timer:
            self.g.cancel(self.layout_save_timer)
            self.layout_save_timer=None
            try:
                self.persist_display_preferences()
            except OSError as exc:
                print('界面布局保存失败：'+str(exc),file=sys.stderr)
        if self.font_provider:
            self.g.gtk_style_context_remove_provider_for_screen(self.g.gdk_screen_get_default(),self.font_provider)
            self.g.g_object_unref(self.font_provider)
            self.font_provider=None
        if self.application:
            self.g.g_application_quit(self.application)


# ==========================================
# Function: Launch the GTK client through the same Linux native architecture as TeleMail.
# Method: Use GtkApplication activation, platform decorations before mapping, and the existing browser controller.
# ==========================================
def serve_gtk(config_path, auth_dir):
    from . import __version__
    from .browser import require_playwright
    require_playwright()
    config_path=prepare_config(config_path)
    g=GtkNative()
    g.g_set_prgname(b'AutoReserve')
    g.g_set_application_name(b'AutoReserve')
    if not g.gtk_init_check(None,None):
        raise RuntimeError('无法初始化 GTK 3，请检查图形桌面会话和程序目录内的运行库')
    suffix=hashlib.sha256(str(Path(config_path).resolve()).encode()).hexdigest()[:16]
    application=g.gtk_application_new(('org.autoreserve.Desktop.w'+suffix).encode(),0)
    holder={}

    # ==========================================
    # Function: Activate one workspace window or present the already-open application.
    # Method: Register with the desktop before constructing controller state or mapping a window.
    # ==========================================
    def activate(*unused):
        if holder.get('app') and not holder['app'].destroyed:
            g.gtk_window_present(holder['app'].window)
            return
        print(f'正在启动 AutoReserve {__version__} · GTK 3 原生客户端…',flush=True)
        try:
            app=GtkDesktop(DesktopController(config_path,auth_dir),g,application)
            holder['app']=app
            g.gtk_window_present(app.window)
            if sys.platform == 'win32':
                print('AUTORESERVE_READY',flush=True)
            print('客户端窗口已创建并映射：AutoReserve · GTK 3',flush=True)
        except Exception as exc:
            holder['error']=str(exc)
            print('客户端启动失败：'+str(exc),flush=True)
            g.g_application_quit(application)

    # ==========================================
    # Function: Route terminal interruption through the GTK main context.
    # Method: Defer the normal cooperative-close path instead of unwinding across a C callback.
    # ==========================================
    def interrupt(signum, frame):
        if holder.get('app'):
            g.later(1,holder['app'].request_close)
        else:
            g.g_application_quit(application)

    previous_interrupt=signal.signal(signal.SIGINT,interrupt)
    # GtkApplication is a GObject, not a widget; its callback is retained for the whole run.
    callback=C.CFUNCTYPE(None,P,P)(activate)
    g.g_signal_connect_data(application,b'activate',C.cast(callback,P),None,None,0)
    try:
        result=g.g_application_run(application,0,None)
        if holder.get('error'):
            raise RuntimeError(holder['error'])
        return result
    finally:
        signal.signal(signal.SIGINT,previous_interrupt)
        if holder.get('app') and not holder['app'].destroyed:
            holder['app'].destroy()
        for source in list(g.sources):
            g.cancel(source)
        g.g_object_unref(application)
