"""Choose a Python runtime by importing the complete browser dependency chain."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

PROBE = """import json, os, sys
if sys.version_info < (3, 12):
    raise RuntimeError('Python 3.12 or newer is required')
from playwright.sync_api import sync_playwright
import greenlet
if os.environ.get('AUTORESERVE_REQUIRE_GTK') == '1':
    import ctypes
    for soname in ('libgtk-3.so.0', 'libgdk-3.so.0', 'libglib-2.0.so.0', 'libgobject-2.0.so.0', 'libgio-2.0.so.0'):
        ctypes.CDLL(soname)
if os.environ.get('AUTORESERVE_REQUIRE_TK') == '1':
    import tkinter
    tkinter.Tcl().eval('info patchlevel')
print(json.dumps({'version': '.'.join(map(str, sys.version_info[:3])), 'python': sys.executable}))
"""


# ==========================================
# Function: Isolate child runtime search paths without modifying the user's shell.
# Method: Isolate approved browser/Tk dependencies and remove Python-home overrides.
# ==========================================
def runtime_environment(managed_dir, use_managed):
    env = dict(os.environ)
    env.pop("PYTHONHOME", None)
    entries = [p for p in env.get("PYTHONPATH", "").split(os.pathsep)
               if p and not Path(p).resolve().is_relative_to(managed_dir.resolve())]
    if use_managed:
        entries.insert(0, str(managed_dir))
        native = managed_dir / ".native/usr"
        if native.is_dir():
            entries.extend([str(native / "lib/python3.14"), str(native / "lib/python3.14/lib-dynload")])
            env["LD_LIBRARY_PATH"] = str(native / "lib/x86_64-linux-gnu") + (
                ":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
            env["TCL_LIBRARY"] = str(native / "share/tcltk/tcl8.6")
            env["TK_LIBRARY"] = str(native / "share/tcltk/tk8.6")
        fonts = managed_dir / ".fonts/fonts.conf"
        if fonts.is_file():
            env["FONTCONFIG_FILE"] = str(fonts)
    if entries:
        env["PYTHONPATH"] = os.pathsep.join(entries)
    else:
        env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    return env


# ==========================================
# Function: Find existing interpreter candidates without installing anything.
# Method: Check the project environment, current Python, and native Linux runtimes.
# ==========================================
def interpreter_candidates(project):
    override = os.environ.get("AUTORESERVE_PYTHON")
    if override:
        yield Path(shutil.which(override) or override), False
        yield Path(shutil.which(override) or override), True
        return
    yield project / ".venv/bin/python", False
    yield Path(sys.executable), True
    current = shutil.which("python3")
    if current:
        yield Path(current), True
    yield Path("/usr/bin/python3"), True
    yield Path("/usr/local/bin/python3"), True
    for folder in (Path("/usr/bin"), Path("/usr/local/bin")):
        for path in sorted(folder.glob("python3.[0-9]*"), reverse=True):
            if path.name.removeprefix("python3.").isdigit():
                yield path, True


# ==========================================
# Function: Select a runtime compatible with installed binary extensions.
# Method: Probe full imports in child processes, retaining concise failure causes.
# ==========================================
def select_runtime(project, require_tk=False, require_gtk=False):
    managed = Path.home() / ".local/pixi-apps/autoreserve-deps"
    failures, seen = [], set()
    for executable, use_managed in interpreter_candidates(project):
        if not executable.is_file() or not os.access(executable, os.X_OK):
            continue
        key = (str(executable.resolve()), use_managed)
        if key in seen:
            continue
        seen.add(key)
        env = runtime_environment(managed, use_managed)
        env["AUTORESERVE_REQUIRE_TK"] = "1" if require_tk else "0"
        env["AUTORESERVE_REQUIRE_GTK"] = "1" if require_gtk else "0"
        try:
            result = subprocess.run([str(executable), "-c", PROBE], env=env,
                                    capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as exc:
            failures.append(f"{executable}: {type(exc).__name__}")
            continue
        if result.returncode == 0:
            try:
                info = json.loads(result.stdout.strip().splitlines()[-1])
                return str(executable), env, info
            except (ValueError, IndexError):
                failures.append(f"{executable}: 无法读取运行环境信息")
        else:
            lines = result.stderr.strip().splitlines()
            failures.append(f"{executable}: {lines[-1] if lines else '依赖检查失败'}")
    details = "\n".join(f"  {item}" for item in failures)
    raise RuntimeError("没有找到能完整加载 Playwright 和 greenlet 的 Python。\n"
                       "启动器没有安装或修改全局环境。请检查现有依赖与解释器版本。\n" + details)


# ==========================================
# Function: Launch the application or tests under the validated runtime.
# Method: Replace the bootstrap process so signals and exit codes remain intact.
# ==========================================
def main(argv=None):
    project = Path(__file__).resolve().parent.parent
    os.chdir(project)
    args = sys.argv[1:] if argv is None else argv
    require_gtk = args[:2] == ["-m", "autoreserve"] and (len(args) == 2 or bool({"desktop", "gui"} & set(args[2:])))
    try:
        if args[:2] == ["-m", "autoreserve"] and not os.environ.get('AUTORESERVE_NO_BOOTSTRAP'):
            sys.path.insert(0, str(project))
            if args[2:] == ['--version']:
                from autoreserve import __version__
                print(__version__)
                return 0
            from scripts.bootstrap import Bootstrap
            if os.environ.get('AUTORESERVE_DESKTOP_LAUNCH') == '1':
                from scripts.setup_ui import with_progress
                executable, env, info = with_progress(lambda progress: Bootstrap(project, progress).ensure())
            else:
                executable, env, info = Bootstrap(project).ensure()
            from autoreserve.desktop_install import install_menu
            try:
                install_menu(project)
            except (OSError, ValueError) as error:
                print('应用菜单快捷方式未创建：' + str(error), file=sys.stderr)
        else:
            executable, env, info = select_runtime(project, require_gtk=require_gtk,
                                                  require_tk=os.environ.get('RUN_NATIVE_TESTS') == '1')
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"启动失败：{exc}", file=sys.stderr)
        return 1
    if Path(executable).resolve() != Path(sys.executable).resolve():
        print(f"使用兼容运行环境：Python {info['version']}（{executable}）", file=sys.stderr, flush=True)
    os.execve(executable, [executable, *args], env)


if __name__ == "__main__":
    sys.exit(main())
