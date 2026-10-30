"""Scoped saves/monitoring, local history deletion and complete goal cleanup without real booking traffic."""

from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from autoreserve.config import Goal, RepeatRule, RushSettings, load_config, save_config
from autoreserve.desktop_controller import DesktopController
from autoreserve.goal_storage import goal_directory
from autoreserve.repeat import materialize_daily, next_daily
from autoreserve.state import Journal, private_json
from autoreserve.workspace import Workspace
from test_catalog import native_config


# ==========================================
# Function: Exercise durable lifecycle changes using actual config files, journals and isolated evidence.
# Method: Mock only browser-worker launch while keeping scoped saves, history and cleanup code in production form.
# ==========================================
class GoalActionTests(unittest.TestCase):
    # ==========================================
    # Function: Edit a completed goal and explicitly reset it without deleting its immutable order history.
    # Method: Save changed requirements, reset twice, then verify pending startup and exactly one archived original order.
    # ==========================================
    def test_completed_goal_edit_reset_preserves_order_and_allows_restart(self):
        first=self.config.goals[0]
        key=self.order(first)
        self.model.states[first.id]={'status':'submitted'}
        self.model.metadata('修改后的目标',(datetime.now(ZoneInfo(self.config.timezone)).date()+timedelta(days=3)).isoformat())
        self.controller.save_goal(self.model.payload(first.id),first.id)
        before=self.controller.order_history()[0]
        self.assertEqual(before['detail']['date'],first.candidates[0].date)
        self.assertEqual(self.controller.state()['goals'][0]['status'],'submitted')
        private_json(self.root/'auth/session.json',{})
        with patch('autoreserve.desktop_controller.threading.Thread') as thread:
            with self.assertRaisesRegex(ValueError,'重置目标状态'):
                self.controller.start('run',goal_id=first.id)
            thread.assert_not_called()
        states=self.controller.reset_goal(first.id)
        self.assertEqual(next(s for s in states if s['id']==first.id)['status'],'pending')
        history=self.controller.order_history()
        self.assertEqual(len(history),1)
        self.assertTrue(history[0]['entry_id'].startswith('attempt:'))
        self.assertEqual(history[0]['detail'],before['detail'])
        self.controller.reset_goal(first.id)
        self.assertEqual(len(self.controller.order_history()),1)
        private_json(self.root/'auth/session.json',{})
        with patch('autoreserve.desktop_controller.threading.Thread') as thread:
            self.controller.start('run',goal_id=first.id)
            self.assertEqual(thread.call_args.kwargs['args'][1].goals[0].name,'修改后的目标')

    # ==========================================
    # Function: Reset only the selected task's submission claims while preserving another task's execution.
    # Method: Mark B as running, reset A, then verify B's journal, slot guard, live state and cancellation event are untouched.
    # ==========================================
    def test_reset_is_scoped_and_running_goal_is_rejected(self):
        first,second=self.config.goals
        self.order(first)
        second_key=self.order(second)
        worker=Mock(busy=True,goal_ids={second.id})
        self.controller.runs[second.id]=worker
        self.controller.busy,self.controller.operation=True,'run'
        with self.assertRaisesRegex(ValueError,'停止'):
            self.controller.reset_goal(second.id)
        self.controller.reset_goal(first.id)
        journal=Journal(self.root/'auth/journal.sqlite3')
        try:
            self.assertIsNotNone(journal.record(second_key))
            journal.ensure_new(self.config.for_target(first.candidates[0]),'retry-first')
            with self.assertRaises(RuntimeError):
                journal.ensure_new(self.config.for_target(second.candidates[0]),'retry-second')
        finally:
            journal.close()
        worker.stop_event.set.assert_not_called()

    # ==========================================
    # Function: Keep recurring occurrences owned by their template and reject independent execution or editing.
    # Method: Exercise actual sidebar projection, durable controller guards and history deletion without a browser.
    # ==========================================
    def test_generated_goals_hidden_frozen_and_removable(self):
        rule=RepeatRule('weekly','每周预约',self.config.goals[0].candidates)
        occurrence=replace(self.config.goals[0],id='daily-weekly-2026-10-28',repeat_id=rule.id)
        self.config.repeat_rules=[rule]
        self.config.goals.insert(1,occurrence)
        save_config(self.config,self.controller.config_path)
        model=Workspace(self.config)
        self.assertEqual([key for key,_ in model.items()],[self.config.goals[0].id,'second','rule:weekly'])
        self.assertEqual(model.occurrences('rule:weekly')[0]['id'],occurrence.id)
        with self.assertRaisesRegex(ValueError,'模板'):
            model.editable(occurrence.id)
        with self.assertRaisesRegex(ValueError,'模板'):
            self.controller.start('run',goal_id=occurrence.id)
        with self.assertRaisesRegex(ValueError,'模板'):
            self.controller.save_goal(model.payload(occurrence.id),occurrence.id)
        model.move(1)
        self.assertEqual([key for key,_ in model.items()],['second',self.config.goals[0].id,'rule:weekly'])
        self.order(occurrence)
        model.selected='rule:weekly'
        self.controller.delete_goal(occurrence.id)
        model.remove(occurrence.id)
        self.assertEqual(model.selected,'rule:weekly')
        self.assertEqual(model.occurrences(),[])
        self.assertEqual(len(self.controller.order_history()),1)

    # ==========================================
    # Function: Prepare two independent pending targets in a real controller workspace.
    # Method: Use a future local booking date and exact courts so duplicate protection is deterministic.
    # ==========================================
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.config=native_config()
        first=self.config.goals[0]
        first=replace(first,candidates=[replace(first.candidates[0],court='场地1')])
        second=replace(first,id='second',name='第二目标',candidates=[replace(first.candidates[0],court='场地2')])
        self.config.goals=[first,second]
        self.config.workspace_version=2
        save_config(self.config,self.root/'config/config.json')
        self.controller=DesktopController(self.root/'config/config.json',self.root/'auth')
        self.model=Workspace(self.config)

    # ==========================================
    # Function: Release all temporary database and filesystem state after each test.
    # Method: Keep journals short lived and remove only this test's private root.
    # ==========================================
    def tearDown(self):
        self.temp.cleanup()

    # ==========================================
    # Function: Add one realistic submitted or uncertain order with durable slot claims.
    # Method: Use the exact production goal key and enrich its history detail before closing SQLite.
    # ==========================================
    def order(self, goal=None, status='submitted'):
        goal=goal or self.config.goals[0]
        journal=Journal(self.root/'auth/journal.sqlite3')
        key=self.config.goal_key(goal)
        try:
            journal.arm(self.config.for_goal(goal),goal.candidates[0],key)
            journal.update(key,status,{'order_id':'ORDER-'+goal.id,'goal_id':goal.id,'goal_name':goal.name})
        finally:
            journal.close()
        return key

    # ==========================================
    # Function: Remove a submitted target and every owned editor/file state while retaining its actual order.
    # Method: Seed isolated and legacy evidence plus shared credentials and check them after a controller restart.
    # ==========================================
    def test_completed_goal_delete_cleans_files_but_keeps_order(self):
        first=self.config.goals[0]
        key=self.order()
        folder=goal_directory(self.controller.config_path,first.id)
        folder.mkdir(parents=True)
        (folder/'shot.png').write_bytes(b'goal evidence')
        private_json(self.root/'auth/plans/old.json',asdict(self.config))
        private_json(self.root/'auth/session.json',{'credential':'keep'})
        private_json(self.root/'config/artifacts/old-run/result.json',{'goals':[{'id':first.id,'status':'submitted'}]})
        private_json(self.root/'config/artifacts/shared/result.json',{'goals':[{'id':first.id},{'id':'second'}]})
        self.model.states[first.id]={'status':'submitted'}
        self.model.drafts[first.id]=['draft']
        self.model.dates[first.id]='2030-01-01'
        removed=self.controller.delete_goal(first.id)
        self.model.remove(first.id)
        self.assertEqual(removed,{first.id})
        self.assertFalse(folder.exists())
        self.assertFalse((self.root/'config/artifacts/old-run').exists())
        self.assertNotIn(first.id,self.model.states)
        self.assertNotIn(first.id,self.model.dates)
        self.assertNotIn(first.id,self.model.drafts)
        from json import loads
        shared=loads((self.root/'config/artifacts/shared/result.json').read_text())
        self.assertEqual([g['id'] for g in shared['goals']],['second'])
        cached=load_config(self.root/'auth/plans/old.json')
        self.assertEqual([g.id for g in cached.goals],['second'])
        self.assertTrue((self.root/'auth/session.json').exists())
        again=DesktopController(self.controller.config_path,self.controller.auth_dir)
        self.assertEqual([g['id'] for g in again.state()['config']['goals']],['second'])
        self.assertEqual(again.order_history()[0]['task_key'],key)

    # ==========================================
    # Function: Keep history reachable after deleting its goal and release only explicitly deleted slot claims.
    # Method: Create another goal for the same slot, observe the actionable conflict and retry after history deletion.
    # ==========================================
    def test_same_slot_can_be_monitored_after_explicit_history_delete(self):
        key=self.order()
        self.controller.delete_goal(self.config.goals[0].id)
        retry=replace(self.config.goals[0],id='retry',name='重新预约')
        payload=asdict(replace(self.config,goals=[retry],repeat_rules=[]))
        self.controller.save_goal(payload,'retry')
        private_json(self.root/'auth/session.json',{})
        with patch('autoreserve.desktop_controller.threading.Thread') as thread:
            with self.assertRaisesRegex(ValueError,'历史订单'):
                self.controller.start('run',goal_id='retry')
            thread.assert_not_called()
            self.controller.delete_order('task:'+key)
            self.controller.start('run',goal_id='retry')
            config=thread.call_args.kwargs['args'][1]
            self.assertEqual([g.id for g in config.goals],['retry'])
            self.assertEqual(config.repeat_rules,[])
        self.assertEqual(self.controller.order_history(),[])

    # ==========================================
    # Function: Preserve unrelated history entries and all their slot aliases when deleting one record.
    # Method: Add two tasks and an archived rejection, then remove only the selected task/attempt identities.
    # ==========================================
    def test_history_delete_is_transactional_and_scoped(self):
        first_key=self.order()
        second_key=self.order(self.config.goals[1])
        journal=Journal(self.root/'auth/journal.sqlite3')
        try:
            journal.update(second_key,'rejected',{'message':'rejected'})
            journal.archive_rejection(second_key)
            archived=next(row for row in journal.history() if row['entry_id'].startswith('attempt:'))
            journal.delete_history(archived['entry_id'])
            self.assertIsNotNone(journal.record(first_key))
            with self.assertRaises(RuntimeError):journal.ensure_new(self.config.for_goal(self.config.goals[0]),'different')
            journal.delete_history('task:'+first_key)
            journal.ensure_new(self.config.for_goal(self.config.goals[0]),'different')
        finally:
            journal.close()

    # ==========================================
    # Function: Save the current valid goal without persisting another edited or incomplete goal.
    # Method: Validate a selected projection and verify both disk isolation and retained dirty drafts.
    # ==========================================
    def test_selected_save_preserves_other_dirty_and_incomplete_drafts(self):
        first=self.model.selected
        self.model.get()['name']='只保存我'
        self.model.get('second')['name']='不保存我'
        self.model.add()
        incomplete=self.model.selected
        self.model.selected=first
        self.controller.save_goal(self.model.payload(first),first)
        saved=load_config(self.controller.config_path)
        self.assertEqual(saved.goals[0].name,'只保存我')
        self.assertEqual(saved.goals[1].name,'第二目标')
        self.assertNotIn(incomplete,{g.id for g in saved.goals})
        self.model.accept_saved(asdict(saved),first)
        self.assertEqual(self.model.get('second')['name'],'不保存我')
        self.assertIsNotNone(self.model.get(incomplete))
        self.assertTrue(self.model.dirty)

    # ==========================================
    # Function: Persist a goal-to-rule conversion without leaving an extra executable source goal.
    # Method: Save only the converted selection and its explicitly tracked replacement identities.
    # ==========================================
    def test_selected_conversion_removes_only_its_source(self):
        old=self.model.selected
        self.model.toggle_repeat()
        identity=self.model.selected
        self.controller.save_goal(self.model.payload(identity),identity,self.model.replacements[identity])
        saved=load_config(self.controller.config_path)
        self.assertEqual([g.id for g in saved.goals],['second'])
        self.assertEqual(len(saved.repeat_rules),1)
        self.assertNotIn(old,{g.id for g in saved.goals})

    # ==========================================
    # Function: Restrict monitor startup to the selected dated goal or one selected repeat template.
    # Method: Capture the worker config without opening a browser and verify unrelated objects are absent.
    # ==========================================
    def test_start_only_current_goal(self):
        private_json(self.root/'auth/session.json',{})
        with patch('autoreserve.desktop_controller.threading.Thread') as thread:
            self.controller.start('run',goal_id='second')
            config=thread.call_args.kwargs['args'][1]
            self.assertEqual([g.id for g in config.goals],['second'])
            self.assertEqual(self.controller.active_identity,'second')
            self.assertEqual(config.repeat_rules,[])

    # ==========================================
    # Function: Preserve unrelated goals and rules when a selected repeat worker materializes a new occurrence.
    # Method: Merge its narrow runtime config into the full file and retain an unrelated unsaved editor draft.
    # ==========================================
    def test_scoped_repeat_persistence_cannot_overwrite_other_goals(self):
        rule=RepeatRule('weekly','每周',[self.config.goals[0].candidates[0]])
        self.config.repeat_rules=[rule]
        save_config(self.config,self.controller.config_path)
        self.model=Workspace(self.config)
        self.model.get('second')['name']='未保存草稿'
        self.controller.active_identity='rule:weekly'
        generated=replace(self.config.goals[0],id='daily-weekly-2026-10-28',repeat_id='weekly')
        projected=replace(self.config,goals=[generated],repeat_rules=[rule])
        self.controller.persist_daily(projected)
        saved=load_config(self.controller.config_path)
        self.assertEqual({g.id for g in saved.goals},{'native-one','second',generated.id})
        self.model.merge_remote(asdict(saved))
        self.assertEqual(self.model.get('second')['name'],'未保存草稿')
        self.assertIsNotNone(self.model.get(generated.id))

    # ==========================================
    # Function: Prevent deletion of a repeat occurrence from recreating it at the next scheduler tick.
    # Method: Materialize a due local execution, delete it and restart the same template's generation.
    # ==========================================
    def test_deleted_repeat_occurrence_is_not_regenerated(self):
        now=datetime.now(ZoneInfo(self.config.timezone)).replace(hour=15)
        self.config.repeat_rules=[RepeatRule('weekly','重复',[self.config.goals[0].candidates[0]],at='12:00')]
        materialize_daily(self.config,now)
        save_config(self.config,self.controller.config_path)
        generated=next(g for g in self.config.goals if g.repeat_id=='weekly')
        self.controller.delete_goal(generated.id)
        saved=load_config(self.controller.config_path)
        self.assertIn(now.date().isoformat(),saved.repeat_rules[0].skip_dates)
        materialize_daily(saved,now)
        self.assertNotIn(generated.id,{g.id for g in saved.goals})

    # ==========================================
    # Function: Delete a repeat template and its generated targets without altering independent goals.
    # Method: Give an occurrence an order and verify that its durable record survives template cleanup.
    # ==========================================
    def test_delete_template_cleans_children_and_keeps_orders(self):
        generated=replace(self.config.goals[0],id='daily-weekly-2026-10-28',repeat_id='weekly')
        self.config.goals.append(generated)
        self.config.repeat_rules=[RepeatRule('weekly','重复',generated.candidates)]
        save_config(self.config,self.controller.config_path)
        key=self.order(generated)
        removed=self.controller.delete_goal('rule:weekly')
        self.assertIn(generated.id,removed)
        saved=load_config(self.controller.config_path)
        self.assertEqual({g.id for g in saved.goals},{'native-one','second'})
        self.assertEqual(saved.repeat_rules,[])
        self.assertEqual(self.controller.order_history()[0]['task_key'],key)

    # ==========================================
    # Function: Reject local history mutations during active monitoring.
    # Method: Assert worker ownership before touching any task/slot rows.
    # ==========================================
    def test_history_cannot_mutate_active_worker(self):
        key=self.order()
        self.controller.busy=True
        with self.assertRaisesRegex(ValueError,'停止'):
            self.controller.delete_order('task:'+key)
        self.assertEqual(len(self.controller.order_history()),1)

    # ==========================================
    # Function: Allow a selected repeat template to keep scheduling after an earlier occurrence has completed.
    # Method: Capture its worker projection with a submitted child and leave unrelated goals/rules outside the run.
    # ==========================================
    def test_repeat_start_resumes_completed_occurrences_without_blocking(self):
        rule=RepeatRule('weekly','重复',[self.config.goals[0].candidates[0]])
        goal=replace(self.config.goals[0],id='daily-weekly-2026-10-28',repeat_id='weekly')
        self.config.repeat_rules=[rule]
        self.config.goals.append(goal)
        save_config(self.config,self.controller.config_path)
        self.order(goal)
        private_json(self.root/'auth/session.json',{})
        with patch('autoreserve.desktop_controller.threading.Thread') as thread:
            self.controller.start('run',goal_id='rule:weekly')
            scoped=thread.call_args.kwargs['args'][1]
            self.assertEqual([g.id for g in scoped.goals],[goal.id])
            self.assertEqual([r.id for r in scoped.repeat_rules],['weekly'])

    # ==========================================
    # Function: Refuse deletion through a redirected evidence parent without removing the configured target.
    # Method: Place a target-like folder outside the configuration tree and keep both the sentinel and saved goal.
    # ==========================================
    @unittest.skipIf(os.name=='nt','Creating test symlinks requires optional Windows developer privileges')
    def test_cleanup_does_not_follow_redirected_parent(self):
        outside=self.root/'unrelated'
        outside.mkdir()
        parent=self.root/'config/artifacts/goals'
        parent.parent.mkdir()
        parent.symlink_to(outside,target_is_directory=True)
        folder=goal_directory(self.controller.config_path,self.config.goals[0].id)
        folder.mkdir()
        sentinel=folder/'keep.txt'
        sentinel.write_text('unrelated')
        with self.assertRaisesRegex(ValueError,'之外'):
            self.controller.delete_goal(self.config.goals[0].id)
        self.assertTrue(sentinel.exists())
        self.assertEqual(len(load_config(self.controller.config_path).goals),2)

    # ==========================================
    # Function: Preserve legacy plans that reuse a goal ID in a different venue scope.
    # Method: Delete the current target while retaining another venue's independently keyed cached plan.
    # ==========================================
    def test_cleanup_uses_legacy_scope_not_just_reused_id(self):
        from autoreserve.catalog import booking_url
        foreign=replace(self.config,booking_url=booking_url('stadium','羽毛球'),venue='体育馆',sport='羽毛球')
        save_config(foreign,self.root/'auth/plans/foreign.json')
        self.controller.delete_goal(self.config.goals[0].id)
        cached=load_config(self.root/'auth/plans/foreign.json')
        self.assertEqual(len(cached.goals),2)

    # ==========================================
    # Function: Keep weekly scheduling valid after a midnight prewarmed occurrence is explicitly deleted.
    # Method: Skip the coming Monday from Sunday night and find the following Monday beyond the initial week.
    # ==========================================
    def test_deleted_upcoming_weekday_keeps_next_week_schedulable(self):
        now=datetime(2026,11,1,23,59,30,tzinfo=ZoneInfo(self.config.timezone))
        rule=RepeatRule('weekly','午夜周一',[self.config.goals[0].candidates[0]],weekdays=[0],
                        rush=RushSettings(enabled=True,at='00:00:10'))
        self.config.repeat_rules=[rule]
        materialize_daily(self.config,now)
        save_config(self.config,self.controller.config_path)
        occurrence=next(g for g in self.config.goals if g.repeat_id=='weekly')
        self.controller.delete_goal(occurrence.id)
        saved=load_config(self.controller.config_path)
        following=next_daily(saved,now)
        self.assertIsNotNone(following)
        self.assertEqual(following.date().isoformat(),'2026-11-08')
