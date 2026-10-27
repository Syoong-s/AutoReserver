"""Native Windows dependency verification and repair confined to the portable application."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from xml.sax.saxutils import escape

from autoreserve.filelock import acquire, release
from tools.windows_runtime import manifest, stage

NO_WINDOW = 0x08000000 if sys.platform == 'win32' else 0


# ==========================================
# Function: Write setup receipts and configuration without partial or locale-dependent JSON.
# Method: Flush a unique temporary file in the same directory before atomic replacement.
# ==========================================
def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


# ==========================================
# Function: Treat a damaged or missing dependency receipt as an incomplete initialization.
# Method: Accept only JSON objects and let live probes determine the repair path.
# ==========================================
def read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


# ==========================================
# Function: Isolate Windows browser, GTK data, temporary files and font caches beneath the program folder.
# Method: Construct a child environment without changing user/system PATH, Python settings or package locations.
# ==========================================
def environment(root):
    root = Path(root).resolve()
    runtime, gtk = root / '.runtime', root / '.runtime/gtk'
    env = dict(os.environ)
    for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'TCL_LIBRARY', 'TK_LIBRARY', 'LD_LIBRARY_PATH'):
        env.pop(key, None)
    for relative in ('tmp', 'logs', 'font-cache'):
        (runtime / relative).mkdir(parents=True, exist_ok=True)
    env.update({'PATH': str(gtk / 'bin') + os.pathsep + env.get('PATH', ''),
                'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1',
                'PLAYWRIGHT_BROWSERS_PATH': str(runtime / 'browsers'), 'TEMP': str(runtime / 'tmp'),
                'TMP': str(runtime / 'tmp'), 'GTK_DATA_PREFIX': str(gtk), 'GDK_BACKEND': 'win32',
                'GSETTINGS_SCHEMA_DIR': str(gtk / 'share/glib-2.0/schemas'),
                'GSETTINGS_BACKEND': 'memory', 'DBUS_SESSION_BUS_ADDRESS': 'disabled:',
                'GDK_PIXBUF_MODULEDIR': str(gtk / 'lib/gdk-pixbuf-2.0/2.10.0/loaders'),
                'GDK_PIXBUF_MODULE_FILE': str(runtime / 'loaders.cache'),
                'FONTCONFIG_FILE': str(runtime / 'fonts.conf'), 'XDG_CACHE_HOME': str(runtime / 'cache')})
    fonts = Path(env.get('WINDIR', env.get('SystemRoot', ''))) / 'Fonts'
    font_config = ('<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">\n'
                   '<fontconfig><dir>' + escape(fonts.as_posix()) + '</dir><cachedir>' +
                   escape((runtime / 'font-cache').as_posix()) + '</cachedir></fontconfig>\n')
    config = runtime / 'fonts.conf'
    if not config.exists() or config.read_text(encoding='utf-8') != font_config:
        config.write_text(font_config, encoding='utf-8')
    return env


# ==========================================
# Function: Validate a downloaded Chromium executable without accepting a directory or marker file.
# Method: Require a regular, nonempty Windows PE image before launching the actual browser probe.
# ==========================================
def browser_available(path):
    try:
        path = Path(path)
        with path.open('rb') as handle:
            return path.stat().st_size > 64 and handle.read(2) == b'MZ'
    except (OSError, TypeError):
        return False


# ==========================================
# Function: Coordinate private Windows setup, repair and initialized flags.
# Method: Hold a portable file lock, check real imports/DLLs/browser files and verify Chromium before marking success.
# ==========================================
class WindowsBootstrap:
    # ==========================================
    # Function: Locate all state relative to one unpacked portable program.
    # Method: Use a dedicated Windows receipt and runtime cache without reusing a global Python or Linux venv.
    # ==========================================
    def __init__(self, root, progress=print):
        self.root = Path(root).resolve()
        self.progress = progress
        self.runtime = self.root / '.runtime'
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.python = self.runtime / 'python/python.exe'
        self.config_path = self.root / 'config/config.json'
        self.receipt = self.root / 'config/bootstrap-windows.json'
        self.spec = manifest(self.root)
        self.env = environment(self.root)

    # ==========================================
    # Function: Execute one local dependency probe or browser download with persistent diagnostics.
    # Method: Preserve argument boundaries and UTF-8 output, hide console windows and fail explicitly on errors.
    # ==========================================
    def command(self, args, stage_name, timeout=900):
        self.progress(stage_name)
        with (self.runtime / 'logs/windows-setup.log').open('a', encoding='utf-8') as log:
            result = subprocess.run([str(a) for a in args], cwd=self.root, env=self.env,
                                    stdout=log, stderr=subprocess.STDOUT, timeout=timeout, creationflags=NO_WINDOW)
        if result.returncode:
            raise RuntimeError(stage_name + '失败，请查看 .runtime/logs/windows-setup.log')

    # ==========================================
    # Function: Verify every exact Python wheel and its native binary imports in a fresh interpreter.
    # Method: Use the embedded runtime's isolated path file and compare installed distribution metadata.
    # ==========================================
    def packages_ready(self):
        code = ('import importlib.metadata as m; import greenlet; from playwright.sync_api import sync_playwright; '
                'from zoneinfo import ZoneInfo; ZoneInfo("Asia/Shanghai"); '
                'import json,sys; assert all(m.version(n)==v for n,v in json.loads(sys.argv[1]).items())')
        pins = {item['name']: item['version'] for item in self.spec['wheels']}
        try:
            result = subprocess.run([str(self.python), '-I', '-X', 'utf8', '-c', code, json.dumps(pins)],
                                    env=self.env, cwd=self.root, capture_output=True, timeout=30, creationflags=NO_WINDOW)
            if result.returncode:
                with (self.runtime / 'logs/windows-setup.log').open('ab') as log:
                    log.write(b'Python dependency probe:\n' + result.stdout + result.stderr)
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    # ==========================================
    # Function: Check GTK's full declared C ABI in a new process before showing the real workspace.
    # Method: Reuse the production ctypes adapter so missing DLL dependencies or exports trigger app-local repair.
    # ==========================================
    def gtk_ready(self):
        try:
            result = subprocess.run([str(self.python), '-I', '-X', 'utf8', '-c',
                                     'from autoreserve.gtk_native import GtkNative; GtkNative()'],
                                    env=self.env, cwd=self.root, capture_output=True, timeout=30, creationflags=NO_WINDOW)
            if result.returncode:
                with (self.runtime / 'logs/windows-setup.log').open('ab') as log:
                    log.write(b'GTK DLL probe:\n' + result.stdout + result.stderr)
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    # ==========================================
    # Function: Generate relocatable image loader and settings caches inside the application directory.
    # Method: Run only pinned private GTK helper executables and regenerate after moving or changing loaders.
    # ==========================================
    def prepare_data(self):
        gtk = self.runtime / 'gtk'
        modules = sorted((gtk / 'lib/gdk-pixbuf-2.0/2.10.0/loaders').glob('*.dll'))
        identity = hashlib.sha256(str(gtk).encode())
        for module in modules:
            info = module.stat()
            identity.update(f'{module.name}:{info.st_size}:{info.st_mtime_ns}'.encode())
        stamp = self.runtime / 'gtk-data.json'
        if read_json(stamp).get('key') == identity.hexdigest() and (self.runtime / 'loaders.cache').is_file():
            return
        if not modules:
            raise RuntimeError('缺少图像加载器，请重新解压完整 Windows 程序包')
        self.progress('初始化图像与字体配置')
        result = subprocess.run([str(gtk / 'bin/gdk-pixbuf-query-loaders.exe'), *map(str, modules)],
                                cwd=gtk, env=self.env, capture_output=True, timeout=30, creationflags=NO_WINDOW)
        if result.returncode or any(module.name.encode() not in result.stdout for module in modules):
            raise RuntimeError('GTK 图像加载器初始化失败：' + result.stderr.decode('utf-8', 'replace'))
        (self.runtime / 'loaders.cache').write_bytes(result.stdout)
        schemas = gtk / 'share/glib-2.0/schemas'
        self.command([gtk / 'bin/glib-compile-schemas.exe', schemas], '初始化 GTK 设置数据', 30)
        atomic_json(stamp, {'key': identity.hexdigest()})

    # ==========================================
    # Function: Resolve the pinned browser executable paths selected by the installed Playwright driver.
    # Method: Read its registry and current browser revision instead of trusting unrelated global browsers.
    # ==========================================
    def browsers(self):
        code = ('import json,os; from pathlib import Path; import playwright; '
                'from playwright.sync_api import sync_playwright\n'
                'with sync_playwright() as p: browser=p.chromium.executable_path\n'
                'manifest=json.loads((Path(playwright.__file__).parent/"driver/package/browsers.json").read_text())\n'
                'record=next(b for b in manifest["browsers"] if b["name"]=="chromium-headless-shell")\n'
                'revision=record.get("revisionOverrides",{}).get("win64",record["revision"])\n'
                'folder=Path(os.environ["PLAYWRIGHT_BROWSERS_PATH"])/("chromium_headless_shell-"+revision)\n'
                'shells=[str(p) for p in folder.rglob("*.exe") if p.name in ("headless_shell.exe","chrome-headless-shell.exe")]\n'
                'print(json.dumps([browser,shells[0] if shells else None]))')
        result = subprocess.run([str(self.python), '-I', '-X', 'utf8', '-c', code], cwd=self.root,
                                env=self.env, capture_output=True, text=True, encoding='utf-8', timeout=30, creationflags=NO_WINDOW)
        if result.returncode:
            raise RuntimeError('无法读取本地 Chromium 路径：' + result.stderr[-1000:])
        return json.loads(result.stdout)

    # ==========================================
    # Function: Preserve goal data when initialization fails or a dependency must be repaired.
    # Method: Change only an existing valid config object's initialized flag; never replace damaged user JSON.
    # ==========================================
    def mark_failed(self, error):
        atomic_json(self.receipt, {'initialized': False, 'error': str(error)})
        if self.config_path.exists():
            try:
                value = json.loads(self.config_path.read_text(encoding='utf-8'))
                if isinstance(value, dict):
                    value['initialized'] = False
                    atomic_json(self.config_path, value)
            except (ValueError, OSError):
                pass

    # ==========================================
    # Function: Verify and repair dependencies once before the reservation UI is started.
    # Method: Stage only missing private components, download Chromium if needed and validate a real blank browser launch.
    # ==========================================
    def ensure_locked(self):
        self.progress('检查运行环境…')
        cache = self.runtime / 'downloads'
        if not self.packages_ready():
            stage(self.root, cache, ('wheels',), self.progress)
            if not self.packages_ready():
                raise RuntimeError('Windows Python 依赖仍不可用，请重新解压程序包')
        if not self.gtk_ready():
            stage(self.root, cache, ('gtk',), self.progress)
            if not self.gtk_ready():
                raise RuntimeError('Windows GTK 运行库仍不可用')
        self.prepare_data()
        browser, shell = self.browsers()
        if not browser_available(browser) or not browser_available(shell):
            self.command([self.python, '-I', '-X', 'utf8', '-m', 'playwright', 'install', '--force', 'chromium'],
                         '下载程序专用 Chromium（首次启动可能需要几分钟）')
            browser, shell = self.browsers()
            if not browser_available(browser) or not browser_available(shell):
                raise RuntimeError('Windows Chromium 下载不完整，请重新启动以重试')
        receipt = {'initialized': True, 'platform': 'windows-x86_64',
                   'manifest_sha256': hashlib.sha256((self.root / 'native/windows-runtime.lock.json').read_bytes()).hexdigest()}
        if read_json(self.receipt) != receipt or read_json(self.config_path).get('initialized') is not True:
            self.command([self.python, '-I', '-X', 'utf8', '-c',
                          'from playwright.sync_api import sync_playwright\nwith sync_playwright() as p:\n b=p.chromium.launch(headless=True); b.close()'],
                         '验证 Windows 本地浏览器', 60)
            self.command([self.python, '-I', '-X', 'utf8', '-c',
                          'from autoreserve.config import Config,load_config,save_config; '
                          'from autoreserve.paths import prepare_config,default_config_path; '
                          'p=prepare_config(default_config_path()); c=load_config(p) if p.exists() else Config(); '
                          'c.initialized=True; save_config(c,p)'], '保存初始化状态', 30)
            atomic_json(self.receipt, receipt)
        return self.env

    # ==========================================
    # Function: Serialize setup across multiple mouse clicks and preserve retryable failures.
    # Method: Wait cooperatively for the private cross-platform lock and report failed initialization after work errors.
    # ==========================================
    def ensure(self):
        with (self.runtime / 'setup.lock').open('a+b') as handle:
            while True:
                try:
                    acquire(handle)
                    break
                except BlockingIOError:
                    self.progress('等待另一个窗口完成初始化…')
                    time.sleep(.2)
            try:
                return self.ensure_locked()
            except Exception as error:
                self.mark_failed(error)
                raise
            finally:
                release(handle)
