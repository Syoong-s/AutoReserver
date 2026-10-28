"""Independent native task ownership and concurrent durable claims without live accounts."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict,replace
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from autoreserve.config import load_config,save_config
from autoreserve.desktop_controller import DesktopController
from autoreserve.state import Journal,private_json
from test_catalog import native_config

# ==========================================
# Function: Verify task lifecycle isolation and shared duplicate protection.
# Method: Use real worker threads, filesystem locks and SQLite while replacing only external browser work.
# ==========================================
class IndependentWorkerTests(unittest.TestCase):
    # ==========================================
    # Function: Create two saved targets sharing one account directory.
    # Method: Use separate exact courts and isolated config, session and journal files.
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
        save_config(self.config,self.root/'config.json')
        private_json(self.root/'auth/session.json',{})
        self.controller=DesktopController(self.root/'config.json',self.root/'auth')
        self.started={g.id:threading.Event() for g in self.config.goals}

    # ==========================================
    # Function: Stop only test-owned threads and release every temporary account resource.
    # Method: Signal the parent stop-all path and join each worker before deleting its files.
    # ==========================================
    def tearDown(self):
        self.controller.stop()
        for worker in self.controller.runs.values():
            worker.worker.join(3)
        self.temp.cleanup()

    # ==========================================
    # Function: Keep one simulated browser session alive until its own cancellation event.
    # Method: Exercise real controller ownership and completion while waiting on an event instead of a website.
    # ==========================================
    def monitor(self, worker,config,artifacts):
        self.started[worker.active_identity].set()
        worker.stop_event.wait(5)
        worker.result={'status':'stopped','message':'测试任务停止'}

    # ==========================================
    # Function: Launch two tasks simultaneously and stop just one while retaining the other's worker and state.
    # Method: Hold both browser workers alive and verify distinct stop events and independent lifecycle flags.
    # ==========================================
    def test_parallel_start_and_scoped_stop(self):
        with patch.object(DesktopController,'monitor_with_recovery',lambda worker,config,artifacts:self.monitor(worker,config,artifacts)):
            first=self.config.goals[0].id
            self.controller.start('run',goal_id=first)
            self.assertTrue(self.started[first].wait(2))
            self.controller.start('run',goal_id='second')
            self.assertTrue(self.started['second'].wait(2))
            a,b=self.controller.runs[first],self.controller.runs['second']
            self.assertIsNot(a.stop_event,b.stop_event)
            self.assertTrue(self.controller.is_running(first))
            self.assertTrue(self.controller.is_running('second'))
            with self.assertRaisesRegex(ValueError,'已经在监测'):
                self.controller.start('run',goal_id='second')
            self.controller.stop(first)
            a.worker.join(2)
            self.assertFalse(self.controller.is_running(first))
            self.assertTrue(self.controller.is_running('second'))
            self.assertFalse(b.stop_event.is_set())
            self.assertTrue(self.controller.busy)
            self.controller.delete_goal(first)
            self.assertNotIn(first,self.controller.runs)
            self.assertTrue(self.controller.is_running('second'))

    # ==========================================
    # Function: Save and delete an unrelated goal while an existing task continues running.
    # Method: Persist a new scoped projection and verify the running worker retains its original frozen configuration.
    # ==========================================
    def test_unrelated_save_delete_during_monitoring(self):
        with patch.object(DesktopController,'monitor_with_recovery',lambda worker,config,artifacts:self.monitor(worker,config,artifacts)):
            first=self.config.goals[0].id
            self.controller.start('run',goal_id=first)
            self.assertTrue(self.started[first].wait(2))
            third=replace(self.config.goals[1],id='third',name='运行期间新建目标')
            payload=asdict(replace(self.config,goals=[third],repeat_rules=[]))
            self.controller.save_goal(payload,'third')
            self.assertIn('third',{g.id for g in load_config(self.controller.config_path).goals})
            self.assertTrue(self.controller.is_running(first))
            self.controller.delete_goal('third')
            self.assertNotIn('third',{g.id for g in load_config(self.controller.config_path).goals})
            self.assertTrue(self.controller.is_running(first))
            with self.assertRaises(ValueError):self.controller.delete_goal(first)

    # ==========================================
    # Function: Change defaults for a newly launched task without changing an already running task's interval.
    # Method: Freeze a 60-second worker, save 10-second defaults and inspect each worker's own launch configuration.
    # ==========================================
    def test_running_config_is_independent_from_new_defaults(self):
        self.config.poll_interval_seconds=60
        save_config(self.config,self.controller.config_path)
        with patch.object(DesktopController,'monitor_with_recovery',lambda worker,config,artifacts:self.monitor(worker,config,artifacts)):
            first=self.config.goals[0].id
            self.controller.start('run',goal_id=first)
            self.assertTrue(self.started[first].wait(2))
            defaults=asdict(self.config) | {'poll_interval_seconds':10}
            self.controller.save_settings(defaults)
            self.controller.start('run',goal_id='second')
            self.assertTrue(self.started['second'].wait(2))
            self.assertEqual(self.controller.runs[first].runtime_config.poll_interval_seconds,60)
            self.assertEqual(self.controller.runs['second'].runtime_config.poll_interval_seconds,10)
            self.assertTrue(self.controller.is_running(first))

    # ==========================================
    # Function: Preserve global login cancellation after a previous independent worker has completed.
    # Method: Retain a finished task in the registry and verify that stop still signals the maintenance operation's event.
    # ==========================================
    def test_maintenance_stop_after_completed_task(self):
        first=self.config.goals[0].id
        with patch.object(DesktopController,'monitor_with_recovery',lambda worker,config,artifacts:self.monitor(worker,config,artifacts)):
            self.controller.start('run',goal_id=first)
            self.assertTrue(self.started[first].wait(2))
            self.controller.stop(first)
            self.controller.runs[first].worker.join(2)
        self.controller.busy,self.controller.operation=True,'login'
        self.controller.stop_event=threading.Event()
        self.controller.stop()
        self.assertTrue(self.controller.stop_event.is_set())
        self.controller.busy,self.controller.operation=False,'idle'

    # ==========================================
    # Function: Localize one worker's browser failure without stopping a different goal.
    # Method: Fail the first private worker only after both have started and verify the second keeps its live stop event.
    # ==========================================
    def test_one_worker_failure_does_not_stop_other_goal(self):
        failure=threading.Event()
        first=self.config.goals[0].id

        # ==========================================
        # Function: Inject one task-local browser error while leaving the other task waiting normally.
        # Method: Use a separate trigger so startup ordering cannot hide concurrent ownership.
        # ==========================================
        def monitor(worker,config,artifacts):
            self.started[worker.active_identity].set()
            if worker.active_identity==first:
                failure.wait(3)
                raise RuntimeError('仅第一个浏览器读取失败')
            worker.stop_event.wait(5)
            worker.result={'status':'stopped','message':'当前目标已停止'}

        with patch.object(DesktopController,'monitor_with_recovery',monitor):
            self.controller.start('run',goal_id=first)
            self.controller.start('run',goal_id='second')
            self.assertTrue(self.started['second'].wait(2))
            failure.set()
            self.controller.runs[first].worker.join(2)
            self.assertEqual(self.controller.runs[first].result['status'],'error')
            self.assertTrue(self.controller.is_running('second'))
            self.assertFalse(self.controller.runs['second'].stop_event.is_set())

    # ==========================================
    # Function: Reserve an overlapping slot atomically across independent browser threads.
    # Method: Start two SQLite connections at a barrier and require one durable claim and one useful conflict.
    # ==========================================
    def test_concurrent_claim_allows_only_one_submit_intent(self):
        barrier=threading.Barrier(2)
        target=self.config.goals[0].candidates[0]

        # ==========================================
        # Function: Attempt the exact production submission latch from one worker thread.
        # Method: Open the journal on its owning thread and return a classified claim result.
        # ==========================================
        def claim(key):
            journal=Journal(self.root/'auth/journal.sqlite3')
            try:
                barrier.wait(2)
                try:
                    journal.arm(self.config.for_target(target),target,key)
                    return 'armed'
                except RuntimeError:
                    return 'blocked'
            finally:
                journal.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(claim,['one','two']))
        self.assertCountEqual(results,['armed','blocked'])
        journal=Journal(self.root/'auth/journal.sqlite3')
        try:self.assertEqual(len(journal.entries()),1)
        finally:journal.close()

    # ==========================================
    # Function: Keep concurrently saved account JSON valid and free of shared temporary-file races.
    # Method: Write complete distinct payloads through the production atomic writer on several threads.
    # ==========================================
    def test_concurrent_private_json_remains_complete(self):
        import json
        path=self.root/'auth/shared.json'
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda number:private_json(path,{'number':number,'payload':'x'*2048}),range(20)))
        value=json.loads(path.read_text())
        self.assertEqual(value['payload'],'x'*2048)
        self.assertEqual(list(path.parent.glob('shared.json.*.tmp')),[])

# ==========================================
# Function: Verify complete-round timing independently from browser render latency.
# Method: Use the real limiter with a virtual browser clock and exact 60-second task cadence.
# ==========================================
class CycleTimingTests(unittest.TestCase):
    # ==========================================
    # Function: Refresh two venues promptly within one round and wait once before the next round.
    # Method: Reserve real read leases, advance virtual time and compare the round starts rather than individual actions.
    # ==========================================
    def test_sixty_seconds_applies_to_complete_round(self):
        from autoreserve.config import Config
        from autoreserve.monitoring import ReadPacer
        from test_monitoring import FakeClock
        clock=FakeClock()
        pacer=ReadPacer(Config(poll_interval_seconds=60,poll_jitter_ratio=0),clock.monotonic,clock.wall,lambda a,b:0)
        self.assertTrue(pacer.begin_cycle(clock,lambda:False))
        self.assertTrue(pacer.wait(clock,lambda:False))
        first=clock.now
        self.assertTrue(pacer.wait(clock,lambda:False))
        self.assertLess(clock.now-first,1)
        pacer.end_cycle()
        self.assertAlmostEqual(pacer.state()['next_read_at']-clock.wall(),60-clock.now)
        self.assertTrue(pacer.begin_cycle(clock,lambda:False))
        self.assertAlmostEqual(clock.now,60,places=4)
        self.assertTrue(pacer.wait(clock,lambda:False))
        self.assertAlmostEqual(clock.now,60,places=4)

    # ==========================================
    # Function: Preserve a server's long cooldown across the complete-round scheduler.
    # Method: Set Retry-After longer than the task interval and require the next round to honor it.
    # ==========================================
    def test_server_backoff_remains_authoritative(self):
        from autoreserve.config import Config
        from autoreserve.monitoring import ReadPacer
        from test_monitoring import FakeClock
        clock=FakeClock()
        pacer=ReadPacer(Config(poll_interval_seconds=60,poll_jitter_ratio=0),clock.monotonic,clock.wall,lambda a,b:0)
        pacer.begin_cycle(clock,lambda:False)
        pacer.wait(clock,lambda:False)
        pacer.defer('限流','300',server=True)
        pacer.end_cycle()
        pacer.begin_cycle(clock,lambda:False)
        self.assertGreaterEqual(clock.now,300)

import os
from contextlib import contextmanager
from autoreserve.browser import open_browser
from autoreserve.config import BookingContext,Config,Goal
import test_browser as browser_fixtures

# ==========================================
# Function: Verify independent tasks launch distinct actual Chromium browsers.
# Method: Use two live loopback workers, a browser-start barrier and the shared production journal.
# ==========================================
@unittest.skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1','设置 RUN_BROWSER_TESTS=1 启用独立浏览器测试')
class IndependentBrowserTests(unittest.TestCase):
    setUpClass=classmethod(browser_fixtures.BrowserTests.setUpClass.__func__)
    tearDownClass=classmethod(browser_fixtures.BrowserTests.tearDownClass.__func__)
    setUp=browser_fixtures.BrowserTests.setUp

    # ==========================================
    # Function: Cancel and join all test browsers before deleting their isolated files.
    # Method: Keep cleanup independent of the order in which worker assertions finish.
    # ==========================================
    def tearDown(self):
        controller=getattr(self,'controller',None)
        if controller:
            controller.stop()
            for worker in controller.runs.values():worker.worker.join(5)
        browser_fixtures.BrowserTests.tearDown(self)

    # ==========================================
    # Function: Run two browser tasks with isolated native sessions and explicit local-only validation.
    # Method: Patch only account authentication and fixture permission while preserving browser, scheduler and submission code.
    # ==========================================
    def run_tasks(self, soldout=False):
        from threading import Barrier,Event
        scenario='soldout' if soldout else 'success'
        url=self.base+'/?scenario='+scenario
        context=BookingContext(url,'模拟健身中心','健身房')
        goals=[Goal('one','第一个目标',[replace(self.target,court='场地1',context=context)]),
               Goal('two','第二个目标',[replace(self.target,court='场地2',context=context)])]
        config=replace(self.config,goals=goals,targets=[],workspace_version=2,monitor_until_complete=True,max_poll_seconds=0)
        save_config(config,self.root/'config.json')
        private_json(self.root/'auth/session.json',{})
        barrier=Barrier(2)
        browsers,payments=[],[]
        checked={goal.id:Event() for goal in goals}
        original_validate=Config.validate
        original_progress=DesktopController.progress

        # ==========================================
        # Function: Permit only this test's explicitly loopback booking routes.
        # Method: Preserve normal configuration validation while opting into its existing local-fixture switch.
        # ==========================================
        def validate(current,*args,**kwargs):
            kwargs['allow_local']=True
            return original_validate(current,*args,**kwargs)

        # ==========================================
        # Function: Prove both real browser processes are alive simultaneously.
        # Method: Synchronize after launch and record payment counters before each browser is closed.
        # ==========================================
        @contextmanager
        def browser(current,auth):
            with open_browser(current,auth) as (native,page):
                browsers.append(native.browser)
                barrier.wait(12)
                try:yield native,page
                finally:
                    if not page.is_closed():payments.append(page.evaluate('typeof payClicks==="number"?payClicks:0'))

        # ==========================================
        # Function: Observe completion of each task's own initial venue scan.
        # Method: Retain production progress aggregation and mark test-only events by immutable worker identity.
        # ==========================================
        def progress(worker,event):
            original_progress(worker,event)
            if '本轮已检查' in event.get('message',''):
                checked[worker.active_identity].set()

        with patch.object(Config,'validate',validate),patch.object(DesktopController,'progress',progress),\
                patch('autoreserve.desktop_controller.open_browser',browser),\
                patch('autoreserve.desktop_controller.HOME_URL',self.base),\
                patch('autoreserve.desktop_controller.DirectoryClient.authenticated',return_value=True),\
                patch('autoreserve.runner.ReadPacer',browser_fixtures.FastPacer):
            self.controller=DesktopController(self.root/'config.json',self.root/'auth')
            self.controller.start('run',goal_id='one')
            self.controller.start('run',goal_id='two')
            self.assertTrue(checked['one'].wait(12))
            self.assertTrue(checked['two'].wait(12))
            self.assertEqual(len(browsers),2)
            self.assertIsNot(browsers[0],browsers[1])
            if soldout:
                self.controller.stop('one')
                self.controller.runs['one'].worker.join(5)
                self.assertFalse(self.controller.is_running('one'))
                self.assertTrue(self.controller.is_running('two'))
                self.assertFalse(self.controller.runs['two'].stop_event.is_set())
                self.controller.stop('two')
            for worker in self.controller.runs.values():worker.worker.join(12)
        self.assertTrue(all(not worker.busy for worker in self.controller.runs.values()))
        self.assertEqual(payments,[0,0])
        return self.controller

    # ==========================================
    # Function: Submit independent nonoverlapping targets from two real browsers.
    # Method: Require two confirmed orders and no payment from the production task worker stack.
    # ==========================================
    def test_two_real_browsers_submit_independent_targets(self):
        controller=self.run_tasks()
        self.assertEqual([worker.result['status'] for worker in controller.runs.values()],['completed','completed'])
        self.assertEqual(len(self.journal.entries()),2)

    # ==========================================
    # Function: Stop one unavailable real browser task while the other continues monitoring.
    # Method: Observe two completed scans, cancel one owner and verify the second stop event remains clear.
    # ==========================================
    def test_stopping_one_real_browser_keeps_other_running(self):
        self.run_tasks(soldout=True)
        self.assertEqual(self.journal.entries(),[])
