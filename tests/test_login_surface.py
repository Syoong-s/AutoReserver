"""Memory-only native login input and automatic official callback/caching workflows on loopback fixtures."""

from http.server import ThreadingHTTPServer
from pathlib import Path
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from autoreserve.config import save_config
from autoreserve.desktop_controller import DesktopController
from autoreserve.login_surface import LoginSurface, login_url
from autoreserve.state import private_json
from test_catalog import native_config, directory_fixture
from test_catalog_browser import DirectoryHandler

LOGIN_HTML = b'''<!doctype html><title>Local official login fixture</title>
<style>input,button{position:absolute;left:40px;width:200px;height:30px}#user{top:30px}#password{top:80px}button{top:130px}</style>
<form onsubmit="event.preventDefault();fetch('/test-login',{method:'POST'}).then(()=>{sessionStorage.setItem('token','local-login-token');location.href='/';})">
<input id=user aria-label=Username required><input id=password aria-label=Password type=password required><button>Log in</button></form>'''


# ==========================================
# Function: Verify bounded transient login transport independently of browser or native GUI dependencies.
# Method: Mock browser input while checking buffer disposal and the official authorization route.
# ==========================================
class LoginSurfaceTests(unittest.TestCase):
    # ==========================================
    # Function: Bound input and keep only one rendered frame without storing credential data.
    # Method: Exercise real text/key/mouse dispatch then close and require complete buffer disposal.
    # ==========================================
    def test_memory_buffers_and_input_ownership(self):
        surface, page = LoginSurface(640,400), Mock()
        page.url='https://jaccount.sjtu.edu.cn/login?private-code=not-for-the-ui'
        page.screenshot.return_value=b'local-frame'
        for kind,values in [('move',(10,20)),('down',('left',)),('up',('left',)),('scroll',(0,120)),
                            ('text',('temporary-text',)),('key',('Tab',))]:
            self.assertTrue(surface.send(kind,*values))
        surface.pump(page)
        self.assertEqual(surface.take_frame(),(b'local-frame','jaccount.sjtu.edu.cn'))
        self.assertIsNone(surface.take_frame())
        page.keyboard.insert_text.assert_called_once_with('temporary-text')
        page.screenshot.assert_called_once_with(type='png',timeout=3000)
        for _ in range(256):self.assertTrue(surface.send('key','Tab'))
        self.assertFalse(surface.send('key','Tab'))
        surface.close()
        self.assertTrue(surface.inputs.empty())
        self.assertFalse(surface.send('text','not accepted'))

    # ==========================================
    # Function: Enter the actual sports-site school-account authorization flow automatically.
    # Method: Verify the official client/callback and keep test loopback URLs isolated from live authentication.
    # ==========================================
    def test_official_authorization_route(self):
        self.assertTrue(login_url().startswith('https://jaccount.sjtu.edu.cn/oauth2/authorize?'))
        self.assertIn('client_id=mB5nKHqC00MusWAgnqSF',login_url())
        self.assertIn('sports.sjtu.edu.cn%2Foauth2Login',login_url())
        self.assertEqual(login_url('http://127.0.0.1:1234/'),'http://127.0.0.1:1234/')


# ==========================================
# Function: Serve a local interactive account form without contacting the real identity provider.
# Method: Reuse counted directory endpoints and mark authentication only after a simulated user submission.
# ==========================================
class LoginHandler(DirectoryHandler):
    # ==========================================
    # Function: Present a deterministic form and retain the authenticated identity endpoint.
    # Method: Serve the login UI for ordinary routes and delegate only current-user reads.
    # ==========================================
    def do_GET(self):
        if self.path=='/system/user/currentUser':
            return super().do_GET()
        self.send_response(200)
        self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Content-Length',str(len(LOGIN_HTML)))
        self.end_headers()
        self.wfile.write(LOGIN_HTML)

    # ==========================================
    # Function: Complete the local form without reading or storing passwords in the fixture server.
    # Method: Mark the test identity authenticated and retain existing static metadata response contracts.
    # ==========================================
    def do_POST(self):
        if self.path=='/test-login':
            self.server.logged_in=True
            self.reply({'code':0})
        else:
            super().do_POST()


# ==========================================
# Function: Validate real automatic login, cancellation and initialization after interacting through the memory surface.
# Method: Run the production controller and actual headless Chromium exclusively against a loopback server.
# ==========================================
@unittest.skipUnless(os.environ.get('RUN_BROWSER_TESTS')=='1','设置 RUN_BROWSER_TESTS=1 启用登录浏览器测试')
class EmbeddedLoginBrowserTests(unittest.TestCase):
    # ==========================================
    # Function: Prepare private configuration and an unauthenticated local identity provider.
    # Method: Keep all sessions, images and metadata outside the user's application files.
    # ==========================================
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        save_config(native_config(),self.root/'config.json')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),LoginHandler)
        self.server.calls,self.server.logged_in=[],False
        self.server_thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.server_thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}/'
        self.controller=DesktopController(self.root/'config.json',self.root/'auth')
        self.surface=LoginSurface()

    # ==========================================
    # Function: Stop login before releasing fixture state and its local server.
    # Method: Join the real browser worker and close all test-owned resources deterministically.
    # ==========================================
    def tearDown(self):
        self.surface.close()
        self.controller.stop()
        if self.controller.worker:
            self.controller.worker.join(10)
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(3)
        self.temp.cleanup()

    # ==========================================
    # Function: Wait for a production-rendered official-page frame before emulating input.
    # Method: Poll the single image slot with a bounded timeout and fail if the worker exits early.
    # ==========================================
    def wait_frame(self):
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            frame=self.surface.take_frame()
            if frame:
                self.assertTrue(frame[0].startswith(b'\x89PNG'))
                self.assertEqual(frame[1],'127.0.0.1')
                return
            if not self.controller.busy:
                self.fail(str(self.controller.result))
            time.sleep(.02)
        self.fail('No rendered login frame')

    # ==========================================
    # Function: Automatically save confirmed login and show only necessary metadata initialization progress.
    # Method: Type and submit through surface gestures, then require ordered completion/cache events and no booking traffic.
    # ==========================================
    def test_form_login_autosaves_then_initializes_directory(self):
        with patch('autoreserve.desktop_controller.HOME_URL',self.base):
            self.controller.start('login',login_surface=self.surface)
            self.wait_frame()
            for kind,values in [('move',(60,45)),('down',('left',)),('up',('left',)),('text',('local-user',)),
                                ('key',('Tab',)),('text',('local-test-password',)),('key',('Tab',)),('key',('Enter',))]:
                self.surface.send(kind,*values)
            self.controller.worker.join(20)
        self.assertFalse(self.controller.busy)
        self.assertEqual(self.controller.auth_status,'已登录')
        self.assertTrue((self.root/'auth/session.json').exists())
        self.assertEqual(self.controller.cache.load()['venues'],directory_fixture()['venues'])
        events=[]
        while not self.controller.events.empty():events.append(self.controller.events.get_nowait())
        kinds=[kind for kind,_ in events]
        self.assertLess(kinds.index('login_complete'),kinds.index('directory_progress'))
        stages=[value['stage'] for kind,value in events if kind=='directory_progress']
        self.assertEqual((stages[0],stages[-1]),('start','done'))
        self.assertTrue(self.surface.inputs.empty())
        self.assertIsNone(self.surface.take_frame())
        self.assertFalse(any('fieldDetail' in path or 'ConfirmOrder' in path for path in self.server.calls))
        self.assertEqual(list(self.root.rglob('*.png')),[])

    # ==========================================
    # Function: Preserve the previously saved session when the user dismisses unconfirmed login.
    # Method: Seed old session files, close a real login surface before submission and verify byte-for-byte preservation.
    # ==========================================
    def test_cancel_preserves_original_session_and_never_writes_images(self):
        private_json(self.root/'auth/session.json',{'origin':self.base,'values':{'old':'kept'}})
        private_json(self.root/'auth/storage.json',{'cookies':[],'origins':[]})
        before={path.name:path.read_bytes() for path in (self.root/'auth').glob('*.json')}
        with patch('autoreserve.desktop_controller.HOME_URL',self.base):
            self.controller.start('login',login_surface=self.surface)
            self.wait_frame()
            self.surface.close()
            self.controller.worker.join(10)
        self.assertFalse(self.controller.busy)
        self.assertEqual(before,{path.name:path.read_bytes() for path in (self.root/'auth').glob('*.json')})
        self.assertIsNone(self.controller.cache.load())
        self.assertEqual(list(self.root.rglob('*.png')),[])

    # ==========================================
    # Function: Avoid saving cookies before the official callback has populated its frontend session state.
    # Method: Return an authenticated current-user identity while leaving sessionStorage empty and require login to remain open.
    # ==========================================
    def test_server_identity_alone_does_not_capture_incomplete_callback(self):
        self.server.logged_in=True
        with patch('autoreserve.desktop_controller.HOME_URL',self.base):
            self.controller.start('login',login_surface=self.surface)
            self.wait_frame()
            time.sleep(2.3)
            self.assertTrue(self.controller.busy)
            self.assertFalse((self.root/'auth/session.json').exists())
            self.assertIsNone(self.controller.cache.load())
            self.surface.close()
            self.controller.worker.join(10)
