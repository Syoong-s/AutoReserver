"""Local editor operations shared by the native view; no periodic I/O or rendering."""

from dataclasses import asdict
from datetime import datetime
import uuid
import threading
from copy import deepcopy

from .desktop_reuse import ReuseEditor

from .config import config_from_dict


# ==========================================
# Function: Keep native editing and persistence independent from the window layout.
# Method: Operate a working copy and retain existing journal protections.
# ==========================================
class DesktopModel(ReuseEditor):
    # ==========================================
    # Function: Restore scheduling settings without changing the working plan.
    # Method: Map persisted configuration values onto existing native controls.
    # ==========================================
    def _load_settings(self):
        self.loading_editor = True
        c = self.data
        self.start_at.set(c["start_at"] or "")
        self.interval.set(str(c["poll_interval_seconds"]))
        self.jitter.set(str(c["poll_jitter_ratio"] * 100))
        self.backoff.set(str(c["backoff_max_seconds"]))
        self.price.set(c["max_total_price"])
        self.max_poll.set(str(c["max_poll_seconds"]))
        self.until_complete.set(c["monitor_until_complete"])
        self.show_browser.set(not c["headless"])
        self.loading_editor = False

    # ==========================================
    # Function: Track actual user edits without marking programmatic selections dirty.
    # Method: Ignore variable writes while loading an editor or processing active work.
    # ==========================================
    def mark_dirty(self, *args):
        if not self.loading_editor and not self.controller.busy and not self.saving:
            self.dirty = True

    # ==========================================
    # Function: Return the currently edited goal or no selection.
    # Method: Address goals by stable IDs independent of displayed order.
    # ==========================================
    def goal(self):
        return next((g for identity, g in self.workspace_items() if identity == self.selected_goal), None)

    # ==========================================
    # Function: Preserve goal edits before switching focus or saving.
    # Method: Update dated candidates or the selected template's independent weekly controls.
    # ==========================================
    def commit_editor(self):
        goal = self.goal()
        if goal is None or self.controller.busy or self.saving:
            return
        if self.goal_states.get(goal["id"], {}).get("status", "pending") not in {"pending", "preview"}:
            return
        goal["name"] = self.goal_name.get().strip()
        if self.is_repeat():
            goal.update(at=self.repeat_at.get().strip(), days_ahead=int(self.repeat_ahead.get()),
                        enabled=self.repeat_enabled.get(), weekdays=[i for i, v in enumerate(self.week_vars) if v.get()])
            return
        value = self.goal_date.get().strip()
        for candidate in goal["candidates"]:
            candidate["date"] = value

    # ==========================================
    # Function: Protect recorded goals and prevent edits during browser operations.
    # Method: Require an editable goal before changing dates, courts, or slots.
    # ==========================================
    def editable_goal(self):
        if self.controller.busy or self.saving:
            raise ValueError("请先停止监测，再编辑目标")
        goal = self.goal()
        if goal is None:
            raise ValueError("请先添加或选择日期目标")
        if not self.is_repeat() and self.goal_states.get(goal["id"], {}).get("status", "pending") not in {"pending", "preview"}:
            raise ValueError("此目标已有订单记录，请添加新目标；原进度会保留")
        return goal

    # ==========================================
    # Function: Add a separate date goal to the current project.
    # Method: Keep incomplete edits local until at least one valid candidate is added.
    # ==========================================
    def add_goal(self):
        if self.controller.busy or self.saving:
            return
        self.commit_editor()
        goal = {"id": "goal-" + uuid.uuid4().hex[:16], "name": f"预约目标 {len(self.data['goals'])+1}", "candidates": []}
        self.data["goals"].append(goal)
        self.dirty = True
        self.render_goals()
        self.select_goal(goal["id"])

    # ==========================================
    # Function: Remove only an editable goal.
    # Method: Keep goals with durable order records intact.
    # ==========================================
    def delete_goal(self, identity=None):
        try:
            if self.controller.busy or self.saving:
                return
            self.commit_editor()
            if identity:
                self.select_goal(identity)
            goal = self.editable_goal()
            self.data["repeat_rules" if self.is_repeat() else "goals"].remove(goal)
            self.selected_goal = None
            self.render_goals()
            self.select_goal(self.workspace_items()[0][0] if self.workspace_items() else None)
            self.dirty = True
        except Exception as exc:
            self.error(exc)

    # ==========================================
    # Function: Change goal priority while preserving its identity and progress.
    # Method: Move the selected object within the working list.
    # ==========================================
    def move_goal(self, delta):
        if self.controller.busy or self.saving or self.goal() is None:
            return
        self.commit_editor()
        values, goal = self.data["repeat_rules" if self.is_repeat() else "goals"], self.goal()
        index = values.index(goal)
        other = index + delta
        if 0 <= other < len(values):
            values[index], values[other] = values[other], values[index]
            self.dirty = True
            self.render_goals()

    # ==========================================
    # Function: Reorder the chosen alternative within one goal.
    # Method: Swap adjacent entries without changing required dates or other goals.
    # ==========================================
    def move_candidate(self, delta):
        try:
            goal = self.editable_goal()
            selected = self.candidate_tree.selection()
            if not selected:
                return
            index, values = int(selected[0]), goal["candidates"]
            other = index + delta
            if 0 <= other < len(values):
                values[index], values[other] = values[other], values[index]
                self.dirty = True
                self.render_candidates()
                self.candidate_tree.selection_set(str(other))
        except Exception as exc:
            self.error(exc)

    # ==========================================
    # Function: Remove one unsubmitted alternative.
    # Method: Keep the final validated plan subject to the shared config schema.
    # ==========================================
    def delete_candidate(self):
        try:
            goal = self.editable_goal()
            selected = self.candidate_tree.selection()
            if selected:
                goal["candidates"].pop(int(selected[0]))
                self.dirty = True
                self.render_candidates()
        except Exception as exc:
            self.error(exc)

    # ==========================================
    # Function: Validate and persist the local working plan without website reads.
    # Method: Reuse nested config validation and existing completed-goal protections.
    # ==========================================
    def save(self, quiet=False, background=False, operation=None):
        try:
            if self.saving:
                raise ValueError("设置正在保存，请稍候")
            self.commit_editor()
            self.data.update(start_at=self.start_at.get().strip() or None,
                             poll_interval_seconds=float(self.interval.get()), poll_jitter_ratio=float(self.jitter.get()) / 100,
                             backoff_max_seconds=float(self.backoff.get()), max_total_price=self.price.get().strip(),
                             max_poll_seconds=float(self.max_poll.get()), monitor_until_complete=self.until_complete.get(),
                             headless=not self.show_browser.get())
            config_from_dict(self.data)
            if background:
                payload = deepcopy(self.data)
                self.saving = True
                self.set_busy()

                # ==========================================
                # Function: Keep configuration validation and durable journal access off the UI thread.
                # Method: Send completion through the existing thread-safe native event channel.
                # ==========================================
                def persist():
                    try:
                        self.controller.save(payload)
                        self.controller.emit("saved", {"quiet": quiet, "operation": operation})
                    except Exception as exc:
                        self.controller.emit("save_failed", str(exc))

                self.save_worker = threading.Thread(target=persist, daemon=True)
                self.save_worker.start()
                return True
            self.controller.save(self.data)
            self.dirty = False
            if not quiet:
                self.status_text.set("设置已保存 · 尚未启动监测，不读取空位")
            self.render_goals()
            return True
        except Exception as exc:
            self.error(exc)
            return False

    # ==========================================
    # Function: Start a worker without blocking native event processing.
    # Method: Save monitoring parameters first; login/catalog never read slot data.
    # ==========================================
    def action(self, operation, save=True):
        try:
            self.commit_editor()
            if save:
                self.save(quiet=True, background=True, operation=operation)
                return
            self.controller.start(operation)
            self.set_busy()
        except Exception as exc:
            self.error(exc)

    # ==========================================
    # Function: Signal homepage login capture from the native window.
    # Method: Let the browser-owning worker verify and save authentication.
    # ==========================================
    def capture(self):
        try:
            self.controller.capture()
        except Exception as exc:
            self.error(exc)

    # ==========================================
    # Function: Restore durable statuses only after local saves or worker completion.
    # Method: Avoid SQLite reads on every native event-loop tick.
    # ==========================================
    def _update_record_states(self):
        if not self.controller.busy:
            self.update_goal_states(self.controller.state()["goals"])

    # ==========================================
    # Function: Append one activity message while keeping the native text buffer bounded.
    # Method: Insert new lines instead of replacing the complete log on every tick.
    # ==========================================
    def append_log(self, item):
        self.log_text.configure(state="normal")
        clock = datetime.fromisoformat(item["time"]).strftime("%H:%M:%S")
        self.log_text.insert("end", f"{clock}  {item['message']}\n")
        count = int(self.log_text.index("end-1c").split(".")[0])
        if count > 250:
            self.log_text.delete("1.0", f"{count-250}.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        self.status_text.set(item["message"])

    # ==========================================
    # Function: Show actionable validation errors in the native client.
    # Method: Keep exception details concise and preserve the editable plan.
    # ==========================================
    def error(self, exc):
        self.dialogs.showerror("请检查", str(exc), parent=self.root)

    # ==========================================
    # Function: Close safely without interrupting a final submission observation.
    # Method: Request cooperative stop and wait asynchronously for the worker to finish.
    # ==========================================
    def close(self):
        if self.controller.busy or self.saving:
            self.closing = True
            self.controller.stop()
            self.status_text.set("正在停止；若已提交，将等待本次结果后关闭")
            return
        if self.dirty and not self.dialogs.askyesno("尚未保存", "目标编辑尚未保存，仍要关闭吗？", parent=self.root):
            return
        if self.after_id:
            self.root.after_cancel(self.after_id)
        self.root.destroy()
