"""Memory-only official login surface shared by GTK and its private browser worker."""

import queue
import threading
import time
from typing import NamedTuple
from urllib.parse import urlsplit

from .config import HOME_URL


# ==========================================
# Function: Keep rendered content and pointer coordinates synchronized across native resizing.
# Method: Carry the exact browser viewport crop alongside each memory-only PNG.
# ==========================================
class LoginFrame(NamedTuple):
    image: bytes
    host: str
    clip: tuple[float, float, float, float]


# ==========================================
# Function: Connect a native login dialog to the existing Chromium engine without storing credentials or frames.
# Method: Bound user-input buffering and retain only the most recent in-memory rendered frame.
# ==========================================
class LoginSurface:
    # ==========================================
    # Function: Define the official page viewport and private communication channels.
    # Method: Keep Playwright objects on their worker thread and native widgets on GTK's main thread.
    # ==========================================
    def __init__(self, width=820, height=500):
        self.width, self.height = width, height
        self.inputs = queue.Queue(maxsize=256)
        self.lock = threading.Lock()
        self.frame = None
        self.closed = threading.Event()

    # ==========================================
    # Function: Forward a user gesture without exposing it to logs or persistence.
    # Method: Serialize enqueue against closure so no input survives cancellation; cap backlog during slow website responses.
    # ==========================================
    def send(self, kind, *values):
        with self.lock:
            if self.closed.is_set():
                return False
            try:
                self.inputs.put_nowait((kind, values))
                return True
            except queue.Full:
                return False

    # ==========================================
    # Function: Release sensitive transient input and image buffers after login or cancellation.
    # Method: Mark closure first, then clear both channels under their corresponding locks.
    # ==========================================
    def close(self):
        self.closed.set()
        with self.lock:
            self.frame = None
        with self.inputs.mutex:
            self.inputs.queue.clear()

    # ==========================================
    # Function: Present only the newest browser image to GTK.
    # Method: Swap a single bounded frame reference instead of accumulating screenshots in the UI event queue.
    # ==========================================
    def take_frame(self):
        with self.lock:
            frame, self.frame = self.frame, None
            return frame

    # ==========================================
    # Function: Render the official webpage and execute user input on its owning browser thread.
    # Method: Drain input, crop the official login card or show full verification overlays, and retain exact viewport coordinates.
    # ==========================================
    def pump(self, page):
        for _ in range(64):
            if self.closed.is_set():
                raise InterruptedError('登录已取消，原有登录保留')
            try:
                kind, values = self.inputs.get_nowait()
            except queue.Empty:
                break
            if kind == 'move':
                page.mouse.move(*values)
            elif kind in {'down', 'up'}:
                getattr(page.mouse, kind)(button=values[0])
            elif kind == 'scroll':
                page.mouse.wheel(*values)
            elif kind == 'text':
                page.keyboard.insert_text(values[0])
            elif kind == 'key':
                page.keyboard.press(values[0])
        if self.closed.is_set():
            raise InterruptedError('登录已取消，原有登录保留')
        clip = page.evaluate("""() => {
            let r = {x:0, y:0, width:innerWidth, height:innerHeight};
            const card = location.hostname === 'jaccount.sjtu.edu.cn' && document.querySelector('.login-layout');
            const visible = e => {const s=getComputedStyle(e), b=e.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0' &&
                    b.width > 1 && b.height > 1 && b.right > 0 && b.bottom > 0 && b.left < innerWidth && b.top < innerHeight;};
            const overlay = [...document.querySelectorAll('[role=dialog], dialog[open], .modal, .geetest_panel, .yidun_popup, iframe')]
                .some(e => visible(e) && (!card || !card.contains(e)));
            if (card && visible(card) && !overlay) {
                const b=card.getBoundingClientRect();
                const x=Math.max(0,b.left-16), y=Math.max(0,b.top-16);
                const right=Math.min(innerWidth,b.right+16), bottom=Math.min(innerHeight,b.bottom+16);
                if (right>x && bottom>y) r={x,y,width:right-x,height:bottom-y};
            }
            return r;
        }""")
        if not isinstance(clip,dict):  # Keep transport-only test doubles independent from layout evaluation.
            clip={'x':0,'y':0,'width':self.width,'height':self.height}
        frame = page.screenshot(type='png', timeout=3000, clip=clip, caret='initial')
        with self.lock:
            if not self.closed.is_set():
                self.frame = LoginFrame(frame, urlsplit(page.url).hostname or '正在跳转',
                                        tuple(clip[key] for key in ('x','y','width','height')))


# ==========================================
# Function: Enter the same school-account authorization route used by the official public login button.
# Method: Use the verified sports-site OAuth client and callback; retain loopback homes for isolated tests.
# ==========================================
def login_url(home=HOME_URL):
    if urlsplit(home).hostname != 'sports.sjtu.edu.cn':
        return home
    return ('https://jaccount.sjtu.edu.cn/oauth2/authorize?response_type=code&'
            'client_id=mB5nKHqC00MusWAgnqSF&redirect_uri=https%3A%2F%2Fsports.sjtu.edu.cn%2Foauth2Login')


# ==========================================
# Function: Detect actual server-confirmed login after returning from official authentication.
# Method: Poll one asynchronous same-origin identity request without requiring the frontend to have initialized its token first.
# ==========================================
def authenticated(page, home=HOME_URL):
    expected, actual = urlsplit(home), urlsplit(page.url)
    if (actual.scheme, actual.netloc) != (expected.scheme, expected.netloc):
        return False
    return page.evaluate("""async () => {
        const key = Symbol.for('autoreserve.login-check');
        const previous = window[key];
        if (previous && previous.pending) return false;
        if (previous && previous.authenticated) return true;
        if (previous && Date.now() - previous.finished < 2000) return false;
        const state = window[key] = {pending:true, authenticated:false, finished:0};
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 15000);
        void (async () => {
            try {
                const response = await fetch('/system/user/currentUser', {
                    credentials:'same-origin', cache:'no-store', signal:controller.signal});
                if (response.status !== 200) return;
                const result = await response.json(), user = result.data;
                if (String(result.code) !== '0' || !user || !(user.loginName || user.userName || user.userId)) return;
                // Mirror the official homepage bootstrap only after the server has confirmed this identity.
                const issued = user.params && user.params.JSESSIONID;
                const current = sessionStorage.getItem('token');
                if ((!current || current === 'undefined' || current === 'null') && typeof issued === 'string' && issued) {
                    sessionStorage.setItem('token', issued);
                    sessionStorage.setItem('isname', String(user.loginName || user.userName || user.userId));
                    sessionStorage.setItem('onAndOff', '0');
                    if (user.userType !== undefined) sessionStorage.setItem('userTypeCurrentUser', String(user.userType));
                }
                state.authenticated = true;
            } catch { /* Navigation, offline periods and timeouts retry without trusting local login markers. */ }
            finally { clearTimeout(timer); state.pending=false; state.finished=Date.now(); }
        })();
        return false;
    }""")


# ==========================================
# Function: Keep an official login page interactive inside the native client until authentication succeeds.
# Method: Pump its private browser surface and verify the official callback periodically without a manual save step.
# ==========================================
def await_login(page, surface, stop, home=HOME_URL):
    from playwright.sync_api import Error
    last_probe = 0
    failures = 0
    while not stop() and not page.is_closed() and not surface.closed.is_set():
        try:
            surface.pump(page)
            if time.monotonic() - last_probe >= .35:
                last_probe = time.monotonic()
                if authenticated(page, home):
                    return
            failures = 0
        except Error:
            if stop() or surface.closed.is_set() or page.is_closed():
                break
            failures += 1
            if failures >= 5:
                raise RuntimeError('官方登录页面暂时无法显示，请关闭登录弹窗后重试') from None
        page.wait_for_timeout(120)
    raise InterruptedError('登录已取消，原有登录和目标保留')
