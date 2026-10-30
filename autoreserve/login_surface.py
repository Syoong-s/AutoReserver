"""Memory-only official login surface shared by GTK and its private browser worker."""

import queue
import threading
import time
from urllib.parse import urlsplit

from .config import HOME_URL


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
    # Method: Drain a bounded input batch and produce an in-memory PNG, without disk screenshots or credential inspection.
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
        frame = page.screenshot(type='png', timeout=3000)
        with self.lock:
            if not self.closed.is_set():
                self.frame = (frame, urlsplit(page.url).hostname or '正在跳转')


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
# Method: Require the official callback's session token before a bounded same-origin current-user identity check.
# ==========================================
def authenticated(page, home=HOME_URL):
    expected, actual = urlsplit(home), urlsplit(page.url)
    if (actual.scheme, actual.netloc) != (expected.scheme, expected.netloc):
        return False
    return page.evaluate("""async () => {
        const token = sessionStorage.getItem('token');
        if (!token || token === 'undefined' || token === 'null') return false;
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 1200);
        try {
            const response = await fetch('/system/user/currentUser', {credentials:'same-origin', signal:controller.signal});
            if (response.status !== 200) return false;
            const result = await response.json(), user = result.data;
            return String(result.code) === '0' && !!(user && (user.loginName || user.userName || user.userId));
        } catch { return false; } finally { clearTimeout(timer); }
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
            if time.monotonic() - last_probe >= 2:
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
