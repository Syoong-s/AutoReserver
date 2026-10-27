"""First-run dependency checks and repair, confined to this application folder."""

import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

from scripts.bootstrap_native import NativeInstaller, gtk_available, missing_libraries, native_environment, ubuntu_t64

UV_VERSION = '0.13.6'
UV_HASHES = {'x86_64': 'f29c3e94ffabd7e6f3ba521dba169a9b7ec12100bf3257087f0f24f6123f3645',
             'aarch64': '229e194255c7958d86d56e8790d969a6f74736592133f0dfd0076f754c642c1d'}
MINIMUM_PYTHON = (3, 12)
CHECK = """import importlib.metadata as m, json, os, sys
from pathlib import Path
import playwright
from playwright.sync_api import sync_playwright
import greenlet
expected = json.loads(sys.argv[1])
assert sys.version_info >= (3, 12)
assert all(m.version(name) == value for name, value in expected.items())
with sync_playwright() as p:
    executable = p.chromium.executable_path
manifest = json.loads((Path(playwright.__file__).parent / 'driver/package/browsers.json').read_text())
revision = next(b['revision'] for b in manifest['browsers'] if b['name'] == 'chromium-headless-shell')
headless = Path(os.environ['PLAYWRIGHT_BROWSERS_PATH']) / ('chromium_headless_shell-' + revision)
shells = [p for p in headless.rglob('*') if p.name in ('headless_shell', 'chrome-headless-shell') and p.is_file()]
print(json.dumps({'python': sys.executable, 'version': list(sys.version_info[:3]), 'browser': executable,
                  'headless': str(shells[0]) if shells else None}))
"""


# ==========================================
# Function: Save first-run progress and initialization flags without partial JSON files.
# Method: Replace a same-directory private temporary file after flushing its contents.
# ==========================================
def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


# ==========================================
# Function: Parse the pinned runtime manifest before installing any Python package.
# Method: Accept exact name/version pins and reject ambiguous or executable requirement syntax.
# ==========================================
def requirements(project):
    import re
    result = {}
    for line in (Path(project) / 'requirements.txt').read_text().splitlines():
        value = line.strip()
        if not value or value.startswith('#'):
            continue
        if not re.fullmatch(r'[A-Za-z0-9_.-]+==[A-Za-z0-9_.+-]+', value):
            raise ValueError('首次初始化要求精确版本依赖：' + value)
        name, version = value.split('==')
        result[name] = version
    return result


# ==========================================
# Function: Isolate Python packages, browser downloads, uv data and temporary files per application.
# Method: Remove inherited Python/venv overrides and explicitly anchor each writable runtime directory.
# ==========================================
def environment(project):
    project = Path(project).resolve()
    runtime = project / '.runtime'
    env = dict(os.environ)
    for name in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV', 'TCL_LIBRARY', 'TK_LIBRARY', 'UV_SYSTEM_PYTHON', 'UV_PYTHON'):
        env.pop(name, None)
    env.update({'PYTHONNOUSERSITE': '1', 'PYTHONDONTWRITEBYTECODE': '1',
                'PLAYWRIGHT_BROWSERS_PATH': str(runtime / 'browsers'),
                'UV_CACHE_DIR': str(runtime / 'cache/uv'), 'UV_PYTHON_INSTALL_DIR': str(runtime / 'python'),
                'UV_PYTHON_BIN_DIR': str(runtime / 'bin'), 'UV_PYTHON_INSTALL_BIN': '0',
                'UV_TOOL_DIR': str(runtime / 'tools'), 'UV_TOOL_BIN_DIR': str(runtime / 'bin'),
                'UV_PYTHON_CACHE_DIR': str(runtime / 'cache/python'), 'UV_NO_CONFIG': '1',
                'XDG_CACHE_HOME': str(runtime / 'cache'), 'TMPDIR': str(runtime / 'tmp')})
    for relative in ('tmp', 'cache', 'logs'):
        (runtime / relative).mkdir(parents=True, exist_ok=True)
    return native_environment(project, env)


# ==========================================
# Function: Check dependencies using the selected interpreter and its exact binary ABI.
# Method: Verify installed versions and retrieve the browser path without opening a page.
# ==========================================
def probe(python, project, env):
    try:
        result = subprocess.run([str(python), '-c', CHECK, json.dumps(requirements(project))],
                                cwd=project, env=env, text=True, capture_output=True, timeout=30)
        if result.returncode == 0:
            return json.loads(result.stdout.strip().splitlines()[-1])
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return None


# ==========================================
# Function: Treat a missing or damaged setup receipt as an incomplete initialization.
# Method: Read only JSON objects and let live runtime checks rebuild the receipt safely.
# ==========================================
def read_receipt(path):
    try:
        value = json.loads(Path(path).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


# ==========================================
# Function: Detect removed, truncated or non-executable browser payloads before opening the workspace.
# Method: Require an executable ELF file instead of trusting directory markers from older downloads.
# ==========================================
def browser_available(path):
    if not path:
        return False
    path = Path(path)
    try:
        with path.open('rb') as handle:
            return os.access(path, os.X_OK) and handle.read(4) == b'\x7fELF'
    except OSError:
        return False


# ==========================================
# Function: Coordinate concurrent launches and persist initialization only after verified success.
# Method: Lock one local setup process, repair missing components and retain useful failure logs.
# ==========================================
class Bootstrap:
    # ==========================================
    # Function: Bind a first-run operation to the portable program folder.
    # Method: Store dependency fingerprints and setup state separately from booking preferences.
    # ==========================================
    def __init__(self, project, progress=print):
        self.project = Path(project).resolve()
        self.runtime = self.project / '.runtime'
        self.state_path = self.project / 'config/bootstrap.json'
        self.config_path = self.project / 'config/config.json'
        self.progress = progress
        self.env = environment(self.project)
        self.log = self.runtime / 'logs/initialization.log'
        self.native = NativeInstaller(self.project, self.run)

    # ==========================================
    # Function: Execute dependency tools with a visible stage and durable local diagnostics.
    # Method: Pass argv directly with bounded timeouts and append output only to the local setup log.
    # ==========================================
    def run(self, arguments, message):
        self.progress(message)
        with self.log.open('a', encoding='utf-8') as handle:
            handle.write('\n' + message + '\n')
            handle.flush()
            result = subprocess.run([str(a) for a in arguments], cwd=self.project, env=self.env,
                                    stdout=handle, stderr=subprocess.STDOUT, timeout=1800)
        if result.returncode:
            raise RuntimeError(message + '失败。详细日志：' + str(self.log))

    # ==========================================
    # Function: Fetch a pinned dependency installer without running a remote installation script.
    # Method: Verify official release SHA256 and extract only the uv executable into the application.
    # ==========================================
    def uv(self):
        architecture = platform.machine()
        if architecture not in UV_HASHES:
            raise RuntimeError('自动安装目前支持 Linux x86_64 / aarch64。')
        executable = self.runtime / 'bin/uv'
        if executable.is_file():
            try:
                result = subprocess.run([str(executable), '--version'], capture_output=True, text=True, timeout=10, env=self.env)
                if result.returncode == 0 and result.stdout.split()[:2] == ['uv', UV_VERSION]:
                    return executable
            except OSError:
                pass
            executable.unlink()
        self.progress('下载本地依赖管理器 uv ' + UV_VERSION)
        filename = 'uv-' + architecture + '-unknown-linux-gnu.tar.gz'
        url = 'https://github.com/astral-sh/uv/releases/download/' + UV_VERSION + '/' + filename
        archive = self.runtime / 'tmp' / filename
        with urllib.request.urlopen(url, timeout=60) as response, archive.open('wb') as output:
            shutil.copyfileobj(response, output)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != UV_HASHES[architecture]:
            archive.unlink()
            raise RuntimeError('uv 下载校验失败，请重试。')
        with tarfile.open(archive) as package:
            members = [member for member in package.getmembers() if member.isfile() and member.name.endswith('/uv')]
            if len(members) != 1:
                raise RuntimeError('uv 下载包格式无效。')
            executable.parent.mkdir(parents=True, exist_ok=True)
            temporary = executable.with_suffix('.tmp')
            with package.extractfile(members[0]) as source, temporary.open('wb') as output:
                shutil.copyfileobj(source, output)
            temporary.chmod(0o755)
            temporary.replace(executable)
        archive.unlink()
        return executable

    # ==========================================
    # Function: Prepare a project venv even when Ubuntu's optional python3-venv package is absent.
    # Method: Use uv's venv implementation, downloading a private Python 3.12 only when necessary.
    # ==========================================
    def install_python(self):
        uv = self.uv()
        target = self.project / '.venv'
        python = target / 'bin/python'
        # A deleted/moved interpreter can leave an otherwise intact portable venv behind.
        valid = False
        if python.exists():
            valid = subprocess.run([str(python), '-c', 'import sys; assert sys.version_info >= (3,12)'],
                                   env=self.env, capture_output=True).returncode == 0
        if not valid:
            interpreter = sys.executable if sys.version_info >= MINIMUM_PYTHON else '3.12'
            if target.exists():
                quarantine = self.runtime / ('previous-venv-' + str(time.time_ns()))
                target.rename(quarantine)
            self.run([uv, 'venv', '--relocatable', '--python', interpreter, str(target)], '创建程序内独立 Python 环境')
        self.run([uv, 'pip', 'install', '--reinstall', '--python', python, '--only-binary', ':all:',
                  '-r', self.project / 'requirements.txt'], '安装程序依赖（仅当前文件夹）')
        return python

    # ==========================================
    # Function: Mark the booking config initialized without overwriting goals or legacy migration.
    # Method: Let the application's validated schema migrate/create/save its own preferences.
    # ==========================================
    def mark_config(self):
        source = """from pathlib import Path
import os
os.umask(0o077)
from autoreserve.paths import prepare_config
from autoreserve.config import Config, load_config, save_config
path = prepare_config(Path('config/config.json'))
config = load_config(path) if path.exists() else Config(workspace_version=2, monitor_until_complete=True)
config.initialized = True
save_config(config, path)
path.chmod(0o600)
"""
        self.run([self.python, '-c', source], '保存初始化状态')

    # ==========================================
    # Function: Avoid trusting a stale initialization flag after upgrades or removed files.
    # Method: Recheck package ABI/versions, GTK and Chromium paths on every launch under one local lock.
    # ==========================================
    def ensure(self):
        self.progress('检查运行环境…')
        with (self.runtime / 'setup.lock').open('a') as lock:
            if not self._try_lock(lock):
                self.progress('另一个窗口正在初始化，等待完成…')
                fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                return self._ensure_locked()
            except Exception as error:
                atomic_json(self.state_path, {'initialized': False, 'error': str(error)})
                if self.config_path.exists():
                    try:
                        data = json.loads(self.config_path.read_text())
                        data['initialized'] = False
                        atomic_json(self.config_path, data)
                    except (ValueError, TypeError):
                        pass
                raise

    # ==========================================
    # Function: Detect a concurrent setup without suppressing unrelated filesystem errors.
    # Method: Try a nonblocking advisory lock and report only the busy-lock condition.
    # ==========================================
    def _try_lock(self, lock):
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    # ==========================================
    # Function: Install missing application dependencies and verify the complete runtime before success.
    # Method: Prefer the local venv, repair exact missing native libraries, then persist a relocatable receipt.
    # ==========================================
    def _ensure_locked(self):
        python = self.project / '.venv/bin/python'
        self.python = python if python.exists() else Path(sys.executable)
        info = probe(self.python, self.project, self.env)
        if not info:
            atomic_json(self.state_path, {'initialized': False, 'stage': 'dependencies'})
            self.python = self.install_python()
            info = probe(self.python, self.project, self.env)
        if not info:
            raise RuntimeError('本地 Python 依赖验证失败，请检查初始化日志。')
        if not gtk_available(self.python, self.env):
            self.native.install(['libgtk-3-0t64' if ubuntu_t64() else 'libgtk-3-0'])
            self.env = environment(self.project)
            if not gtk_available(self.python, self.env):
                raise RuntimeError('GTK 本地运行库仍不可用，请检查初始化日志。')
        browser = Path(info['browser'])
        if not browser_available(browser) or not browser_available(info.get('headless')):
            self.run([self.python, '-m', 'playwright', 'install', '--force', 'chromium'], '下载本地 Chromium 浏览器')
            info = probe(self.python, self.project, self.env)
            if not info or not info.get('headless'):
                raise RuntimeError('Chromium 下载未完成，请重新启动以重试。')
        browser_files = [browser, Path(info['headless'])]
        for attempt in range(3):
            missing = sorted({name for executable in browser_files for name in missing_libraries(executable, self.env)})
            if not missing:
                break
            self.native.repair_browser(missing)
            self.env = environment(self.project)
        if any(not browser_available(p) or missing_libraries(p, self.env) for p in browser_files):
            raise RuntimeError('Chromium 运行库不完整，请检查初始化日志。')
        if shutil.which('fc-list'):
            fonts = subprocess.run(['fc-list', ':lang=zh'], env=self.env, capture_output=True, timeout=20)
            if not fonts.stdout.strip():
                self.native.install(['fonts-noto-cjk'])
                self.env = environment(self.project)
        receipt = {'initialized': True, 'requirements_sha256': hashlib.sha256((self.project / 'requirements.txt').read_bytes()).hexdigest(),
                   'python_version': info['version'], 'platform': platform.machine(), 'uv_version': UV_VERSION}
        previous = read_receipt(self.state_path)
        configured = read_receipt(self.config_path).get('initialized') is True
        if previous != receipt or not configured:
            # Exercise the actual browser and its subprocess loader once after any repair/update.
            self.run([self.python, '-c', 'from playwright.sync_api import sync_playwright\nwith sync_playwright() as p:\n b=p.chromium.launch(headless=True); b.close()'], '验证本地浏览器')
            self.mark_config()
            atomic_json(self.state_path, receipt)
        return str(self.python), self.env, {'version': '.'.join(map(str, info['version'])), 'python': str(self.python)}
