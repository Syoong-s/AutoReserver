"""Private native Windows entry point used by AutoReserve.exe, without WSL or a system Python."""

import os
from pathlib import Path
import runpy
import sys
import traceback


# ==========================================
# Function: Verify private Windows dependencies and start the unchanged native GTK reservation workspace.
# Method: Resolve all paths from this file, serialize desktop ownership and retain the current Python interpreter.
# ==========================================
def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    if sys.argv[1:] == ['--version']:
        from autoreserve import __version__
        print(__version__, flush=True)
        return 0
    from scripts.bootstrap_windows import WindowsBootstrap
    from autoreserve.filelock import acquire, release
    try:
        setup = WindowsBootstrap(root, lambda message: print('SETUP:' + message, flush=True))
        os.environ.update(setup.ensure())
        if sys.argv[1:2] == ['--self-test']:
            from scripts.windows_smoke import self_test
            test_source = None
            if len(sys.argv) > 2:
                if len(sys.argv) != 4 or sys.argv[2] != '--test-source':
                    raise ValueError('Usage: --self-test [--test-source SOURCE_DIRECTORY]')
                test_source = Path(sys.argv[3]).resolve()
            self_test(root, test_source)
            print('AUTORESERVE_READY', flush=True)
            return 0
        with (root / '.runtime/desktop.lock').open('a+b') as handle:
            try:
                acquire(handle)
            except BlockingIOError as error:
                raise RuntimeError('此程序文件夹已有客户端运行，请先切换到已打开的窗口。') from error
            try:
                sys.argv = ['autoreserve', *sys.argv[1:]]
                runpy.run_module('autoreserve', run_name='__main__')
            finally:
                release(handle)
    except Exception as error:
        traceback.print_exc()
        print('ERROR:' + str(error), flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
