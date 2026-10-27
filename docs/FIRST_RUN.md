# Application-local first-run initialization

AutoReserve 0.10.0 supports a mouse-first portable workflow on Ubuntu 22.04+ / WSLg with a graphical session and `/usr/bin/python3`. Extract the architecture-matching archive into a writable directory and double-click the native `AutoReserve` file. The source `./start-gui.sh` entry uses the same setup logic. A C compiler is required only to build the native launcher, never to run the release.

## Installation manifest

- Bootstrap tool: **uv 0.13.6**, downloaded from the official Astral release. SHA256 is pinned separately for Linux x86_64 and aarch64 in scripts/bootstrap.py. No remote shell installer is executed.
- Python: reuse an available compatible interpreter; if it is older than 3.12, uv supplies a private **Python 3.12** under `.runtime/python/`.
- Exact Python dependencies: **playwright 1.64.1**, **pyee 14.0.0**, **greenlet 3.5.6**, **typing_extensions 4.16.0**. Install destination: `.venv/`. Binary wheels only; no compiler/build toolchain is installed.
- Browser: Chromium plus its headless shell and supporting downloads selected by the pinned Playwright distribution, under `.runtime/browsers/`.
- Missing Ubuntu native libraries: GTK 3 runtime, the specific Chromium soname providers identified by `ldd`, and CJK fonts when absent. Versions are resolved against the host Ubuntu release's authenticated package indexes and recorded in `.runtime/logs/initialization.log`. Packages are downloaded into `.runtime/apt/` and extracted into `.runtime/native/`; apt installation, sudo, dpkg installation, and replacing the host libc/loader are never used.
- uv caches, managed Python executables, temporary files, browser downloads, logs and font caches are explicitly redirected into `.runtime/`. The parent shell, global Python, shell profiles and global package caches are not modified.

Actual local validation uses Python 3.14.4 and GTK 3.24.52. Automatic native repair targets Ubuntu; other Linux distributions need compatible native libraries supplied by their existing environment. A GUI display and a compatible base glibc remain OS prerequisites. This is a desktop client, not an HPC deployment.

## Initialization state and recovery

`config/config.json` adds the boolean `initialized`, defaulting to false for older configs and public examples. It is set true only after exact Python dependency checks, GTK loading, both browser executable checks, library checks, an actual headless Chromium launch and validated config saving succeed. Initialization uses a local simulated browser only; it never logs in, requests availability or submits orders.

`config/bootstrap.json` records the successful requirements digest, Python/platform information, or the latest failure. Setup writes JSON atomically and serializes simultaneous launches with `.runtime/setup.lock`. The booking config is migrated using the application's existing validated migration; goals, account data and journals remain intact.

An initialized flag is not a substitute for runtime checks: deleted browser files, mismatched packages or moved interpreter links trigger repair. Setting initialized=false requests revalidation, not deleting personal data. Failed runs stay incomplete and can be retried by launching again. See `.runtime/logs/initialization.log` when a download fails. Downloads need network access; ordinary verified launches work offline.

## Clickable entry and menu

The native C launcher resolves its own path and safely forwards arguments through an isolated system-Python bootstrap. It works after moving the bundle and with spaces/Unicode in the directory name. A generated user-menu `.desktop` entry points to the existing portable folder and uses freedesktop argument quoting. The shortcut is the only desktop integration outside the app folder; runtime dependencies stay local. Starting from a moved folder updates the entry. File-manager trust/execution policies may require allowing execution once. Linux launchers cannot be opened as native Windows `.exe` files.

## Tests and release scope

`./test.sh` verifies existing GUI/browser behavior and the bootstrap without initiating dependency downloads; tests use the available validated development runtime. New tests cover false/success/failure/retry states, offline repeat launch, missing files, dependency ABI mismatch, installer checksum rejection, real setup locking, native apt redirections, launcher relocation, and GIO's actual desktop-entry parser.

The release builder excludes `.venv`, `.runtime`, authentication, journals and runtime config files. The public example always has initialized=false. Python wheel integrations do not include the portable scripts; use portable releases for automatic setup.

## Primary implementation references

- [uv storage locations](https://docs.astral.sh/uv/reference/storage/)
- [uv environment controls](https://docs.astral.sh/uv/configuration/environment/)
- [Playwright browser locations](https://playwright.dev/python/docs/browsers)
- [Freedesktop Exec quoting](https://xdg.pages.freedesktop.org/xdg-specs/desktop-entry/latest/exec-variables.html)
