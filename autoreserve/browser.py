"""Playwright browser lifecycle and explicit sessionStorage persistence."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

from .state import private_json

PAYMENT_PATTERN = re.compile(
    r"(?:/venue/personal/(?:orderImmediatelyPC|order/paid)(?:\?|$)|https://cwc2\.jdcw\.sjtu\.edu\.cn/payment/)"
)


# ==========================================
# Function: Refuse payment requests in the automated browser.
# Method: Abort known payment routes before a network request is sent.
# ==========================================
def block_payment(route):
    route.abort("blockedbyclient")


# ==========================================
# Function: Validate browser dependencies with accurate interpreter diagnostics.
# Method: Distinguish a missing package from a broken binary dependency import.
# ==========================================
def require_playwright():
    try:
        from playwright.sync_api import sync_playwright
        return sync_playwright
    except ImportError as exc:
        runtime = f"Python {sys.version_info.major}.{sys.version_info.minor}（{sys.executable}）"
        if (getattr(exc, "name", "") or "").split(".")[0] == "playwright":
            reason = "当前解释器未找到 Playwright"
        else:
            reason = f"Playwright 依赖加载失败：{exc}；可能与 Python 版本不匹配"
        entry = 'AutoReserve.exe' if sys.platform == 'win32' else './start-gui.sh'
        raise RuntimeError(f"{reason}。当前使用 {runtime}。请通过 {entry} 启动并检查程序目录内的运行环境。") from exc


# ==========================================
# Function: Open a fresh manual login or reuse saved authentication for monitoring.
# Method: Skip all saved browser state for login; leave durable files untouched until capture.
# ==========================================
@contextmanager
def open_browser(config, auth_dir, login=False):
    sync_playwright = require_playwright()
    auth_dir = Path(auth_dir)
    state_path = auth_dir / "storage.json"
    session_path = auth_dir / "session.json"
    executable = config.browser_executable or os.environ.get("AUTORESERVE_BROWSER")
    if executable and not Path(executable).is_file():
        raise ValueError("browser_executable / AUTORESERVE_BROWSER 不存在，请使用当前系统的浏览器可执行文件路径")
    with sync_playwright() as playwright:
        if not executable and not Path(playwright.chromium.executable_path).is_file():
            candidates = list((Path.home() / ".cache/ms-playwright").glob("chromium-*/chrome-linux*/chrome"))
            if candidates:
                executable = str(max(candidates, key=lambda p: int(p.parents[1].name.split("-")[-1])))
        launch_env = dict(os.environ)
        # The approved local installation can supply shared libraries without changing the OS.
        import playwright as package
        lib_dir = Path(package.__file__).parent.parent / ".system/usr/lib/x86_64-linux-gnu"
        if lib_dir.is_dir():
            launch_env["LD_LIBRARY_PATH"] = str(lib_dir) + (
                ":" + launch_env["LD_LIBRARY_PATH"] if launch_env.get("LD_LIBRARY_PATH") else ""
            )
        font_config = Path(package.__file__).parent.parent / ".fonts/fonts.conf"
        if font_config.is_file():
            launch_env["FONTCONFIG_FILE"] = str(font_config)
        browser = playwright.chromium.launch(
            headless=False if login else config.headless, executable_path=executable, env=launch_env,
        )
        options = {"viewport": {"width": 1440, "height": 1050}, "locale": "zh-CN", "timezone_id": config.timezone}
        if not login and state_path.exists():
            options["storage_state"] = str(state_path)
        context = browser.new_context(**options)
        try:
            if not login and session_path.exists():
                saved = json.loads(session_path.read_text(encoding="utf-8"))
                script = """(() => {
                    const saved = SAVED;
                    if (location.origin === saved.origin) {
                        for (const [key, value] of Object.entries(saved.values)) {
                            if (sessionStorage.getItem(key) === null) sessionStorage.setItem(key, value);
                        }
                    }
                })();""".replace("SAVED", json.dumps(saved, ensure_ascii=True))
                context.add_init_script(script=script)
            context.route(PAYMENT_PATTERN, block_payment)
            page = context.new_page()
            page.set_default_timeout(config.action_timeout_ms)
            page.set_default_navigation_timeout(30000)
            yield context, page
        finally:
            context.close()
            browser.close()


# ==========================================
# Function: Save only the authorized site's session.
# Method: Store Playwright state plus sessionStorage in private files.
# ==========================================
def save_session(context, page, auth_dir, allowed_origin):
    origin = page.evaluate("location.origin")
    if origin != allowed_origin:
        raise RuntimeError("当前仍在登录服务或其他网站，请回到预约页再保存")
    private_json(Path(auth_dir) / "storage.json", context.storage_state(indexed_db=True))
    private_json(Path(auth_dir) / "session.json", {
        "origin": origin, "values": page.evaluate("Object.fromEntries(Object.entries(sessionStorage))"),
    })


# ==========================================
# Function: Extract a URL origin without its sensitive query or fragment.
# Method: Join parsed scheme and authority.
# ==========================================
def origin_of(url):
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}"
