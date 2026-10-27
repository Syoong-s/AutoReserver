# Local validation — v0.9.0

Validated on 2026-10-27 with Python 3.14.4, GTK 3.24.52, Playwright 1.64.1 and Chromium 152.0.7977.8. No local packages were installed or global environments modified for this work. Linux with Python 3.12+, GTK 3.24+ and requirements.txt is the supported runtime; `./start-gui.sh` runs it without compilation.

- `./test.sh` from this independent repository: **192 tests passed in 211.020 seconds**. Real Chromium scenarios use a local fixture website and temporary state; no real reservations or payments occur.
- Production GTK checks cover close-only settings on the first frame, native scroll boundaries and wheel propagation through padding, fixed log/availability cards, per-goal drafts and weekly settings, asynchronous saving, notifications and cooperative shutdown. All 16 GTK tests also passed separately with the X11 backend; the full suite uses the default Wayland session locally.
- Native resolved-background checks confirm distinct selected surfaces for planned, running and recurring goals. Cached labels skip unchanged writes.
- A fresh-interpreter regression blocks all Tk imports and verifies all three production GUI entry points still route to GTK. Historical Tk widgets are loaded only when constructing the legacy client.
- Configuration tests cover application-relative paths, validated legacy migration and backup, new-file priority, damaged input and explicit test overrides.
- Release tests cover private-data exclusion, launcher executable modes, deterministic archives, checksums, version-tag rejection and wheel metadata/resources.
- Python AST/LF, Bash, JavaScript, GtkBuilder XML and workflow YAML checks passed. A final short sidebar caption adjustment was followed by another production GTK run.
- Existing setuptools 78.1.1 built wheel/sdist successfully. Standard-library release tooling built the portable tar.gz/ZIP and SHA256SUMS. All four archives were inspected for GTK assets and private-data exclusion.
- A portable archive was extracted outside both source directories and launched from an unrelated cwd. Its actual native GTK window mapped, configuration and journal stayed inside the extracted folder, and SIGINT closed it normally. The extracted wheel imported independently with its GTK assets present.

The GitHub workflow was reviewed and parsed locally; hosted CI and remote publishing have not been executed. Portable archives do not bundle Python, GTK or Chromium. Local logs and screenshots are kept outside this public repository.


# Local validation — v0.10.0

Changes are confined to this independent repo. `./test.sh` passed **204 tests in 205.435 seconds**. After final bootstrap hardening, the 9 initialization regressions and 4 archive/privacy tests passed again. The 3 native desktop integration tests also passed, including an actual GIO parse/launch of paths with spaces, Unicode, quotes, dollar/backtick/percent/backslash/equal characters, plus a real relocated ELF's argv boundaries.

A real clean setup installed uv 0.13.6 and the four exact pinned Python packages inside `.runtime/` and `.venv/`; downloaded Chromium **156.0.8078.4** and its headless shell; downloaded/extracted missing libasound2-data, libasound2t64, libnspr4, libnss3 and fonts-noto-cjk into `.runtime/native/`. No sudo, apt installation, global Python changes or global environment installation occurred. Host GTK 3.24.52 / Python 3.14.4 were reused. Both configuration initialization flags became true only after actual local browser verification. The routine verified preflight measured 1.15 seconds in one local run; this is an observation, not a cross-system benchmark.

The native entry opened the actual GTK workspace from an unrelated working directory. Menu integration was exercised using an app-local temporary XDG data directory to preserve the user's existing menu. Setup-progress responsiveness was independently checked: an artificial 240ms worker completed while 8 GTK heartbeats ran; no native callback errors occurred.

The architecture-specific release was extracted to a different directory with Chinese characters and a percent sign. Already downloaded app-local dependencies were reused with all HTTP(S) proxies pointed at a closed local port, forcing an offline check. Font paths repaired themselves, a fresh initialized config/receipt was created, the GTK window mapped, and SIGINT closed it with exit 0. No original source directory, personal config or network download was needed in that extracted run.

Python AST/LF, GTK XML/SVG, Bash, JavaScript and Git whitespace checks passed. Wheel, source archive, portable tar.gz/ZIP and checksums were built and inspected for the ELF mode/assets and exclusions of `.runtime`, `.venv`, initialization receipts and personal data. The public example retains initialized=false. Logs and screenshots are under artifacts/first-run/ and excluded from releases. Hosted GitHub CI/publishing has not been executed locally.

# Local validation — v0.11.0

Changes are confined to this independent repo. `./test.sh` passed **225 tests in 203.096 seconds** with Python 3.14.4, GTK 3.24.52 and Playwright 1.64.1. No packages were installed for the timed-release feature. Python compilation, Bash and JavaScript syntax checks also passed.

Fourteen deterministic rush tests cover per-goal JSON defaults/ranges, unchanged order identity, exact preparation and release gates, a shared four-read first-second/rolling-second quota, waking ordinary waits without defeating Retry-After, bounded same-origin HEAD calibration and response disposal, cached/invalid clock rejection, monotonic deadlines, local-clock opt-out/manual offsets, weekly and cross-midnight generation, next-day copies and template conversion.

Four additional real Chromium scenarios use only the loopback fixture with the production limiter: early preparation with no selection/submission until release; preparation stock becoming unavailable at release; first-second 429 stopping acceleration/submission; and cancellation after prewarm. Existing browser scenarios continue to cover loading races, persistent submission locks, rate-limit handling, priorities and slot conflicts. No real orders or payments were attempted.

All 19 native GTK regressions passed, including the new ordered/independent fold state, per-goal rush switch and saved weekly conversion, and a live countdown that preserves explicit clock uncertainty without unfolding the editor. Native Cairo previews were reviewed using the production app-local CJK font configuration. The preview uses isolated temporary settings and metadata; the user's running client was not closed or rewritten. Evidence is under the ignored `artifacts/rush/` folder.

The ordinary 16 cross-venue tests passed separately before the full suite. HTTP Date is treated as a coarse estimate (500ms plus half-RTT sampling uncertainty), not a millisecond clock or a guaranteed reservation outcome. See RUSH.md for the algorithm references and operational limits. Release archives remain free of user configuration, downloaded runtime dependencies and private account state; GitHub-hosted publishing is a separate user action.

Final review also exercised a failed recalibration after an earlier successful estimate: the clock now clears the previous offset/uncertainty and explicitly reanchors to local time, matching its fallback message. All 14 rush unit tests passed again after that correction. Wheel, source distribution, native portable tar.gz/ZIP and SHA256SUMS were rebuilt from the final sources.

# Local validation — v0.12.0

Linux's full `./test.sh` passed **233 tests in 205.466 seconds** after the shared platform changes. The 19 native GTK regressions passed again after the Windows font fallback adjustment; the eight new portability/bootstrap/archive tests are also independently runnable. Python AST/LF, GTK/Windows manifest XML, Bash, JavaScript and Git whitespace checks cover the final source tree.

The Windows package was cross-built from WSL using the host's existing .NET Framework compiler and pinned CPython **3.14.5**, GTK **3.24.52**, GLib **2.90.1**, Playwright **1.64.1** and its Windows Chromium **156.0.8078.4**. All downloaded artifacts were checked against exact SHA256/size pins. Source/build/download work stayed in this repo; native execution was tested in an isolated temporary folder on the Windows local drive. No global Python, GTK, compiler environment or system package was installed, and TeleMail was only read for its existing Windows dependency inventory.

The real `AutoReserve.exe --self-test` passed **seven native Windows cases** covering settings on the first frame, independent folds, per-goal rush/weekly persistence, sidebar boundaries, clock uncertainty, real local Chromium order completion and release-time selection gating. Its extra two-process check verified that the Windows account lock excludes another process and releases correctly afterward. Tests used temporary configs and a loopback-only demo site; no real reservations or payments occurred. The normal GUI entry also mapped its actual GtkApplication workspace and exited with code 0 after WM_CLOSE was posted only to that test's own GTK process.

Native review caught two platform-specific startup issues before release: WSL network shares do not support the required Windows byte-range lock, and an embedded-Python `_pth` root entry needs its terminating slash to normalize correctly. The launcher now directs unsupported filesystems to local extraction, and the builder writes the relocatable isolated import paths with that slash. Missing-dependency probes retain their stderr in the app-local setup log. Initialization and durable account files remain private, outside the public source allowlist and release payload.

Windows evidence, screenshots and logs are under the ignored `artifacts/windows/` directory. GitHub's workflow now has separate native Windows and Linux jobs and gates publishing on both; the hosted workflow itself and remote Release publishing have not been run locally. The launcher is a Windows x64 .NET GUI PE, not a WSL launcher, and the complete ZIP is required alongside the EXE.
