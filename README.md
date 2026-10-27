# AutoReserve

## Quick start

Download the matching portable package from the repository's **Releases** page:

| Platform | Release package | Start |
| --- | --- | --- |
| Linux x86_64 (Ubuntu 22.04+ desktop) | `AutoReserve-0.12.0-linux-x86_64-portable.tar.gz` or `.zip` | Extract the entire folder to a writable location, then double-click `AutoReserve` inside it. |
| Windows 11 x64 | `AutoReserve-0.12.0-windows-x86_64-portable.zip` | Extract the entire folder to a writable local Windows drive, then double-click `AutoReserve.exe` inside it. No Python or WSL installation is needed. |

Keep the launcher with its extracted files. First launch requires internet access to download missing dependencies; settings stay in the program folder's `config/`.

A native Linux and Windows 11 reservation workspace for Shanghai Jiao Tong University sports facilities.

AutoReserve combines a GTK 3 desktop with a local Playwright booking worker. Create one-time goals, rank alternatives across venues and courts, or schedule separate weekly repeat goals. The application submits an available order and records its result; it does not make payments.

[中文说明](README-CN.md)

## Features

- Native client-side title bar, resize behavior, keyboard controls, and scrolling.
- Close-only settings and notification windows, configured before the first frame.
- Multiple venues, exact courts, hourly alternatives, and contiguous-hour orders.
- An inline **+** action that copies a goal to the following date with an independent identity.
- Per-goal weekday selection, trigger time, relative booking date, and pause control.
- Per-goal timed-release mode: early session checks, same-origin clock estimation, prewarmed venue pages, and a shared, bounded first-second refresh window.
- Collapsible automation and booking sections, a taller ranked alternative list, and a compact floating venue/time composer that retains unfinished selections.
- Right-panel font controls (11–20 px), remembered independently of booking settings; the execution log follows its latest 50 lines.
- Monitoring duration defaults to zero for no time limit. A zero order-price cap also means unlimited; positive caps, amount recognition and exact selection checks still apply.
- Visible sidebar surfaces and left accents: green for monitoring, blue for repeat goals, gray for planned goals, purple for completed goals, and amber for attention.
- Separate, always-visible execution-log and availability panels below the editor.
- Shared request pacing and backoff across venues; persistent order/slot protection across restarts.
- Local authentication and state. Editing venues or time slots does not request live availability.

## First launch — download, extract, double-click

**Windows 11 x86_64:** download the `windows-x86_64-portable.zip`, extract the entire folder to a writable local Windows drive and double-click **`AutoReserve.exe`**. The package includes private Python and GTK runtimes; first launch downloads Chromium into the same folder. System Python and WSL are unnecessary. See [native Windows setup and validation](docs/WINDOWS.md).

CI currently publishes x86_64 archives; aarch64 archives can be built natively with the same release tool. Use the architecture-matching Linux portable archive, extract it to a writable folder, and double-click **`AutoReserve`** inside that folder.

The first launch checks the actual runtime and shows setup progress when work is needed:

- Reuses a suitable Python when available; otherwise downloads a private Python 3.12 through a pinned, checksum-verified uv release.
- Installs missing/mismatched Python dependencies in **`.venv/`**, even if Ubuntu's optional `python3-venv` package is absent.
- Downloads Chromium and its headless shell to **`.runtime/browsers/`**.
- If needed on Ubuntu, downloads and extracts GTK/Chromium runtime libraries and CJK fonts to **`.runtime/native/`**. It uses application-local apt lists/caches and `dpkg-deb --extract`, never system package installation or sudo.
- Marks **`initialized: true`** in `config/config.json` only after a real local browser launch and successful config validation. A separate `config/bootstrap.json` records the dependency receipt and failure details.

Subsequent launches still check dependency versions, library loading and browser files. Missing files or changed requirements are repaired automatically. Failed initialization stays false and can be retried by launching again; booking goals are preserved. An internet connection is required for missing downloads. A regular verified launch uses the local runtime and does not download anything.

The program also creates a shortcut in the **current user's application menu** pointing to the portable folder. This shortcut is user-scoped; dependencies remain inside the program directory. Moving the folder may require launching `AutoReserve` from its new location once to refresh the shortcut and repair absolute interpreter links.

Source checkout users can run `./start-gui.sh` for the same automatic setup. Run `python3 tools/release.py` with an existing C compiler to build the clickable ELF first. `./run.sh`, `./run.sh gui` and `./run.sh desktop` open the same GTK application.

Keep the application folder writable. Default booking settings are always **`config/config.json` in that folder**, independent of the shell working directory. An older root-level `config.json` is validated and migrated, preserving its original as `config/legacy-config.json`. Authentication, catalog caches and the order journal stay in `.auth/` under the application folder.

Explicit `--config` and `--auth-dir` options remain available for isolated booking workspaces. Initialization receipts always belong to the portable application folder. PyGObject, Tk and WebKitGTK are not required for the production interface. The optional historical regression tests use Tk. Python wheels remain available for advanced integrations; the portable folder contains the first-run bootstrap and mouse launcher.

See [first-run details and installation manifest](docs/FIRST_RUN.md).

## Booking workflow

1. Open **Log in / re-login** in the sidebar and complete the website login in the separate browser.
2. Return to the client and save the login. Venue/project metadata is cached locally.
3. Create a goal, choose a booking date, and add venue/court/time alternatives in priority order.
4. Optionally convert that goal into a weekly repeat target and select its execution weekdays, local trigger time and days-ahead offset.
5. Optionally enable the top-right **抢购模式** switch, enter the release time and adjust preparation/cadence settings. One-time goals use an exact date/time; weekly templates use an execution-day `HH:MM:SS`.
6. Save settings and explicitly start monitoring before the preparation window.

The UI currently uses Chinese labels. Weekdays refer to the **execution day**, not the later booking date. Generated dated goals keep their independent requirements; pausing a template stops future generation and retains existing occurrences. The client must remain running and connected for monitoring. Closing an active client requests a cooperative stop and waits for the current result.

Timed-release defaults are a **45-second preparation lead**, **250ms first-second read spacing**, and **1000ms spacing for the next 8 seconds**, followed by ordinary monitoring. The account shares at most four serial read actions per first-second/rolling one-second window. Actual frequency depends on native page/network latency. HTTP Date provides only second-resolution clock samples; the UI exposes their estimated uncertainty and supports local-clock mode/manual correction. Throttling remains authoritative. See [timed-release behavior, configuration and references](docs/RUSH.md).

## Development and tests

```bash
./test.sh
python3 -m compileall -q autoreserve scripts tools tests demo
bash -n run.sh start-gui.sh test.sh
```

Tests use temporary workspaces and a local simulated website. They do not submit real reservations or make payments. A graphical display is needed for GTK, legacy Tk, and browser scenarios. On a headless Linux host, run them under Xvfb, as shown in the release workflow.

The runtime and UI were locally verified with Python **3.14.4**, GTK **3.24.52**, and Playwright **1.64.1**. End users need no compiler: the portable release includes the native launcher and an interpreted Python payload.

## Build and publish

```bash
python3 tools/release.py
# Optional Python wheel/source distribution using an existing build environment:
python3 -m build
```

The portable builder compiles a small native launcher with an existing C compiler and creates reproducible, architecture-specific `.tar.gz` and `.zip` archives plus `SHA256SUMS` in `dist/`. It uses an explicit public-file allowlist and excludes authentication, orders, screenshots, runtime configuration and bytecode even if they exist locally.

The checked-in GitHub Actions workflow tests Linux and native Windows, builds the Python package and portable archives, and publishes a GitHub Release for a matching version tag such as **`v0.12.0`**. Manual workflow runs produce downloadable build artifacts without publishing a release. Set your own GitHub remote and push source/tags when ready; this repository contains no remote-specific credentials.

See [release details](docs/RELEASING.md) and [architecture](docs/ARCHITECTURE.md).

## License

[MIT](LICENSE). GTK, Chromium, Playwright and other installed dependencies retain their respective licenses; their binaries are not vendored in this repository.
