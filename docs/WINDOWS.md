# Native Windows 11 portable application — v0.12.0

AutoReserve's reservation worker and GTK editor now run directly on **Windows 11 x86_64**, through a small `AutoReserve.exe` launcher. Python code is reused; GTK is loaded from Windows DLLs instead of Linux shared objects. No WSL, system Python, administrator permission or global GTK installation is needed.

## Download and start

1. Download `AutoReserve-0.12.0-windows-x86_64-portable.zip`.
2. Extract the **entire folder** to a writable local Windows drive, such as a folder in your user profile. Do not run from inside the ZIP, a WSL network share, or a read-only program directory.
3. Double-click **AutoReserve.exe** inside the extracted folder and keep it with the accompanying files.
4. On first launch, the native startup window reports setup progress while Chromium is downloaded and verified. Subsequent verified starts use the existing private runtime.
5. Log in through the normal browser flow, save your goals and explicitly start monitoring. Initialization never submits a real booking.

The EXE is a Windows GUI application and opens no console. It uses Windows' existing .NET Framework to start the included CPython executable with isolated import paths. The portable folder includes Python 3.14.5, pinned Python wheels and GTK 3.24.52 DLLs/data/licenses. Chromium and its headless shell are downloaded separately on first use into `.runtime/browsers/`. The launcher cannot function alone; if its embedded Python files are removed, extract a fresh complete archive.

The workspace uses the Windows system titlebar for minimize, maximize, restore and taskbar activation. Both the startup EXE and the Python-owned workspace use AutoReserve's calendar icon and explicit application identity. The startup window leaves the taskbar after the workspace is created. Initial workspace dimensions and position follow the primary monitor's work area in GTK application pixels, including display scaling and taskbar space.

Portable archives contain the application, its UI assets and only the bootstrap/runtime files needed for their platform. Repository metadata, README files, developer documentation, tests, demo code and compiler inputs are excluded. The Windows runtime repair manifest remains in `native/`; AutoReserve's license notice is under `.runtime/licenses/AutoReserve/LICENSE`, and third-party notices remain inside their runtime packages.

## Configuration, recovery and isolation

- Settings stay in the **same program folder's `config/config.json`**, independent of the launch directory. Login and durable order state remain in `.auth/`.
- `config/bootstrap-windows.json` records Windows initialization separately. `initialized` becomes true only after exact package checks, DLL loading, a real local headless browser launch and configuration validation succeed.
- Failed setup preserves goals, leaves false initialization flags and can be retried by launching again. Missing Python wheels or GTK DLLs are repaired from pinned app-private downloads; missing browser binaries trigger a local Chromium repair.
- All temporary files, image loader caches, font caches and browser files stay in `.runtime/`. DLL search directories and PATH changes affect only this process and its children.
- Fontconfig reads Windows' installed fonts, including the Chinese fonts normally available on Windows 11. Moving the program folder rebuilds font and image-loader paths locally.
- Windows account/desktop/setup locks use one persistent byte via the CRT; Linux retains POSIX flock. Use a separate extracted native Windows folder rather than sharing an active WSL workspace or account folder.
- The existing default price limit, verification flow, timed-release bounds, Retry-After backoff, order deduplication and no-payment behavior are unchanged.

Startup logs are `.runtime/logs/windows-launch.log` and `windows-setup.log`. If downloads are blocked, these logs identify the failing stage. No automatic task, registry registration or system service is created. The launcher is unsigned; Windows security reputation policies may display their usual prompt.

## Build and validate

Use Python 3.14+ on Windows, or WSL with access to the host Windows .NET compiler:

```bash
python tools/build_windows.py --expected-tag v0.12.0
```

The tool uses a Windows-specific runtime allowlist, downloads checksum-pinned archives into its private `.runtime/windows-build/downloads`, stages runtime-only DLL/data files and compiles the small C# launcher with the existing .NET Framework compiler. The checked-in multi-size `autoreserve/gtk_ui/icon.ico` exports the existing calendar SVG for Windows and is embedded in the EXE. Every build recreates the unpacked staging folder so files removed from the allowlist cannot survive from an older build. No development environment is installed globally. The result is an independent ZIP under `dist/`. The unpacked staging folder is under `.runtime/windows-build/AutoReserve-0.12.0/`; native Windows execution requires a local Windows drive.

Exact dependency versions, hashes, licenses and upstream/source links are in [the manifest](WINDOWS_DEPENDENCIES.md) and `native/windows-runtime.lock.json`. Vendor archives are verified before extraction; Python wheels are unpacked into private site-packages without a global pip installation. GTK/MSYS2 package installation scripts are never run. Upstream licenses remain with their packages; public source/package URLs are retained for dependency rebuilds.

`AutoReserve.exe --self-test` works from the portable package without a source checkout. It verifies the actual Win32 titlebar, native taskbar icon, AppUserModelID, three system-minimize/restore cycles, visible monitor placement, account locking and a local headless Chromium page. It writes a JSON report and native screenshot under `.runtime/logs/`; no real account, reservation or payment is involved. The ordinary client starts when no self-test argument is provided.

CI additionally passes `--self-test --test-source SOURCE_DIRECTORY` to load settings, folds, rush persistence, sidebar and loopback booking regressions from its separate checkout. Tests and the demo server are never bundled in the portable application.

Local verification uses Python 3.14.4 for WSL packaging, the existing Python 3.12.13 with tkinter for full Linux regressions, and the bundled Python 3.14.5 / GTK 3.24.52 on Windows. Existing dependencies are reused. For Linux regression tests, prepare the application environment with `scripts.bootstrap.environment()` before running `python -m unittest discover -s tests -v`; enable `RUN_GTK_TESTS=1`, `RUN_NATIVE_TESTS=1` and `RUN_BROWSER_TESTS=1` for native and local browser checks. The portable archive checks also run separately with `python -m unittest discover -s tests -p test_release.py -v`.

The 2026-10-27 local validation ran 202 current application/GTK/browser/release checks successfully with Python 3.14.4. The 34 legacy Tk/startup checks ran separately with Python 3.12.13: 33 passed initially, and one event-timing assertion passed on an isolated retry. Windows verification passed the portable EXE self-test, seven source-checkout integration cases and an additional `GDK_SCALE=2` window test. Both Linux portable formats and the Windows ZIP were built; archive membership, ZIP integrity and the delivered Windows ZIP's SHA256 were checked. A local Python wheel rebuild was unavailable because the existing interpreters have no setuptools/build frontend installed; the hosted workflow still builds the wheel in its configured build environment.

GitHub Actions has a native Windows build/EXE test job in addition to Linux's full regressions. A matching version tag publishes the Windows ZIP together with Linux and Python artifacts only after both jobs pass. A manual run produces downloadable artifacts without publishing. Hosted Actions and GitHub Release publication require pushing the repository and are not executed by a local build.

## Primary platform references

- [GTK's Windows installation and bundling guidance](https://www.gtk.org/docs/installations/windows)
- [CPython's embedded Windows distribution](https://docs.python.org/3/using/windows.html#the-embeddable-package)
- [Python zoneinfo data sources and Windows tzdata](https://docs.python.org/3/library/zoneinfo.html#data-sources)
- [Microsoft DLL search order](https://learn.microsoft.com/en-us/windows/win32/dlls/dynamic-link-library-search-order)
- [Microsoft explicit application taskbar identity](https://learn.microsoft.com/en-us/windows/win32/api/shobjidl_core/nf-shobjidl_core-setcurrentprocessexplicitappusermodelid)
- [GTK titlebar selection](https://docs.gtk.org/gtk3/method.Window.set_titlebar.html) and [GDK monitor work areas](https://docs.gtk.org/gdk3/method.Monitor.get_workarea.html)
