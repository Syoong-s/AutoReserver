"""Per-goal repeat editing and independent next-day copies; no network operations."""

from copy import deepcopy
from dataclasses import asdict
from datetime import date, datetime, timedelta
import uuid
from zoneinfo import ZoneInfo

from .config import BookingContext, RepeatRule, config_from_dict, target_from_dict
from .repeat import copy_goals


# ==========================================
# Function: Manage ordinary goals and repeat templates in the same workspace.
# Method: Use rule-prefixed sidebar identities while keeping dated order identities immutable.
# ==========================================
class ReuseEditor:
    # ==========================================
    # Function: Expose both dated goals and repeat templates to the sidebar.
    # Method: Prefix template display identities without altering persisted rule IDs.
    # ==========================================
    def workspace_items(self):
        return [(g["id"], g) for g in self.data["goals"]] + [("rule:" + r["id"], r) for r in self.data["repeat_rules"]]

    # ==========================================
    # Function: Recognize a selected repeating template.
    # Method: Keep generated dated occurrences distinct from the rule that created them.
    # ==========================================
    def is_repeat(self):
        return bool(self.selected_goal and self.selected_goal.startswith("rule:"))

    # ==========================================
    # Function: Copy even a completed goal without inheriting its order identity.
    # Method: Allocate fresh IDs and validate the full proposed workspace before applying it.
    # ==========================================
    def copy_to_dates(self, dates):
        if self.controller.busy or self.saving:
            raise ValueError("请先停止监测，再复制目标")
        self.commit_editor()
        source = self.goal()
        if not source or not source["candidates"]:
            raise ValueError("请先为目标添加场馆和时段")
        today = datetime.now(ZoneInfo(self.data["timezone"])).date().isoformat()
        if any(day < today for day in dates):
            raise ValueError("不能复制到已过去的日期")
        clean = {"id": source["id"], "name": source["name"], "candidates": source["candidates"], "origin": None}
        additions = copy_goals(clean, dates)
        config_from_dict(self.data | {"goals": [*self.data["goals"], *additions]})
        self.data["goals"].extend(additions)
        self.dirty = True
        self.render_goals()
        self.select_goal(additions[0]["id"])
        self.status_text.set(f"已创建 {dates[0]} 的独立目标 · 请保存设置")
        return additions

    # ==========================================
    # Function: Copy the clicked goal to the day following its own booking date.
    # Method: Commit the current editor, select the source, and reuse validated independent copies.
    # ==========================================
    def copy_next_day(self, identity):
        if self.controller.busy or self.saving:
            return
        try:
            self.commit_editor()
            self.select_goal(identity)
            day = date.fromisoformat(self.goal_date.get()) + timedelta(days=1)
            return self.copy_to_dates([day.isoformat()])
        except Exception as exc:
            self.error(exc)

    # ==========================================
    # Function: Save the current alternatives as an independently scheduled template.
    # Method: Freeze inherited routes and validate weekly trigger days before applying updates.
    # ==========================================
    def set_daily_rule(self, days_ahead, at, identity=None, weekdays=None, enabled=True):
        if self.controller.busy or self.saving:
            raise ValueError("请先停止监测，再修改重复目标")
        self.commit_editor()
        source = self.goal()
        if not source or not source["candidates"]:
            raise ValueError("请先为目标添加场馆和时段，再开启重复")
        candidates = deepcopy(source["candidates"])
        for candidate in candidates:
            if not candidate.get("context"):
                candidate["context"] = asdict(BookingContext(self.data["booking_url"], self.data["venue"], self.data["sport"]))
        rule = RepeatRule(identity or "rule-" + uuid.uuid4().hex[:16], source["name"],
                          [target_from_dict(t) for t in candidates], days_ahead, at, enabled,
                          list(range(7)) if weekdays is None else weekdays)
        rules = [r for r in self.data["repeat_rules"] if r["id"] != rule.id] + [asdict(rule)]
        config_from_dict(self.data | {"repeat_rules": rules})
        self.data["repeat_rules"] = rules
        self.dirty = True
        self.render_goals()
        self.status_text.set("重复目标已设置 · 保存后点击开始监测生效")
        return rule

    # ==========================================
    # Function: Change the selected editable goal between one-time and weekly repeat modes.
    # Method: Convert rather than duplicate the source, keeping existing occurrences and orders intact.
    # ==========================================
    def toggle_repeat(self):
        try:
            self.editable_goal()
            self.commit_editor()
            source = self.goal()
            draft = list(self.builder_contexts)
            if self.is_repeat():
                day = self.goal_date.get()
                copied = copy_goals({"id": source["id"], "name": source["name"], "candidates": source["candidates"]}, [day])[0]
                proposed = self.data | {"goals": [*self.data["goals"], copied],
                                        "repeat_rules": [r for r in self.data["repeat_rules"] if r["id"] != source["id"]]}
                config_from_dict(proposed)
                self.data = proposed
                identity = copied["id"]
            else:
                rule = self.set_daily_rule(2, "12:00")
                self.data["goals"].remove(source)
                identity = "rule:" + rule.id
            self.dirty = True
            self.builder_drafts[identity] = draft
            self.render_goals()
            self.select_goal(identity)
        except Exception as exc:
            self.error(exc)

    # ==========================================
    # Function: Save per-target weekly controls immediately into the local draft.
    # Method: Retain at least one execution day and repaint only the schedule/card summary.
    # ==========================================
    def schedule_changed(self, *args):
        if self.loading_editor or not self.is_repeat() or self.controller.busy or self.saving:
            return
        if not any(v.get() for v in self.week_vars):
            self.week_vars[self._last_weekday].set(True)
            self.status_text.set("每周至少选择一个自动预约日")
            return
        self.commit_editor()
        self.dirty = True
        self.render_goals()
        self.render_schedule_state()

    # ==========================================
    # Function: Toggle a weekday without permitting an empty recurring schedule.
    # Method: Route each pill through the same local schedule update path.
    # ==========================================
    def toggle_weekday(self, day):
        if self.controller.busy or self.saving:
            return
        self._last_weekday = day
        self.week_vars[day].set(not self.week_vars[day].get())
        self.schedule_changed()

    # ==========================================
    # Function: Load controls for the selected target's own repeat schedule.
    # Method: Show normal date input or weekly options and highlight enabled repeat workspaces.
    # ==========================================
    def load_schedule(self):
        rule = self.goal() if self.is_repeat() else None
        self.repeat_at.set(rule["at"] if rule else "12:00")
        self.repeat_ahead.set(str(rule["days_ahead"] if rule else 2))
        self.repeat_enabled.set(rule["enabled"] if rule else True)
        for day, variable in enumerate(self.week_vars):
            variable.set(day in (rule.get("weekdays", list(range(7))) if rule else range(7)))
        self.repeat_button.set_text("切换为普通目标" if rule else "设为重复目标")
        self.date_caption.set("预计预约日期" if rule else "预约日期")
        if rule:
            self.schedule_panel.pack(fill="x", pady=(12, 0), after=self.meta)
        else:
            self.schedule_panel.pack_forget()
        self.render_schedule_state()

    # ==========================================
    # Function: Show execution days, pause status, and the next expected booking date.
    # Method: Derive feedback in memory from the current rule and local timezone.
    # ==========================================
    def render_schedule_state(self):
        rule = self.goal() if self.is_repeat() else None
        active = bool(rule and rule["enabled"])
        self.workspace_card.configure(highlightbackground="#2563eb" if active else "#e9e9e7", highlightthickness=2 if active else 1)
        self.mode_text.set("重复目标 · 已开启" if active else "重复目标 · 已暂停" if rule else "普通目标 · 单次预约")
        if rule:
            today = datetime.now(ZoneInfo(self.data["timezone"])).date()
            days = rule.get("weekdays", list(range(7)))
            offset = next(i for i in range(8) if (today + timedelta(days=i)).weekday() in days
                          and not (i == 0 and any(g["id"] == f"daily-{rule['id']}-{today.isoformat()}" for g in self.data["goals"])))
            self.goal_date.set((today + timedelta(days=offset + int(rule["days_ahead"]))).isoformat())
            for day, button in enumerate(self.week_buttons):
                selected = day in days
                button.configure(bg="#2563eb" if selected else "#f1f3f6", fg="white" if selected else "#737780")
            names = "、".join("一二三四五六日"[d] for d in days)
            self.schedule_hint.set(f"每周{names} {rule['at']} 自动生成，预约往后 {rule['days_ahead']} 天 · 按自动执行当天的星期判断")
