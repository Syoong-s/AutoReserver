# Releasing AutoReserve

The independent repository contains public source only. Do not add authentication, personal configuration, order journals or generated evidence to Git. Both Git ignores and the portable archive allowlist protect those paths.

1. Update `autoreserve/__init__.py` and the documentation to the intended version.
2. Run `./test.sh` in a graphical Linux session. Tests use local fixtures and temporary state.
3. Build and inspect the artifacts locally:

```bash
python3 -m build
python3 tools/release.py --expected-tag v0.12.0
(cd dist && sha256sum -c SHA256SUMS)
```

The optional `build` frontend must already be available in your build environment. The portable builder itself uses only the standard library. The Python build backend is declared in `pyproject.toml`.

4. Commit the reviewed public source, configure your own GitHub remote and push it.
5. Push the matching version tag, for example `v0.12.0`. The checked-in workflow verifies the tag, tests both Linux and native Windows, builds and creates the GitHub Release. A manual workflow run only uploads temporary build artifacts.

The build job has read-only repository permissions. Only the publish job has `contents: write`, and it depends on successful tests and builds. Third-party actions are pinned to commits. Release credentials come from the job-scoped GitHub token.

The architecture-specific portable `.tar.gz` and `.zip` files contain a runnable program folder with source, assets, requirements, and a compiled `AutoReserve` ELF mouse launcher. Building requires an existing C compiler. They download missing Python/GTK/Chromium dependencies into the program folder on first launch; see FIRST_RUN.md. Runtime downloads, initialization flags and personal preferences are excluded from releases. The wheel includes GTK UI assets and an `autoreserve` console command. Prefer the portable launcher for application-local writable configuration.

Local validation does not execute GitHub Actions or publish a remote release. Review your repository's Actions policy and inspect the first hosted run after pushing the source/tag.

Native Windows packages are built separately with `python tools/build_windows.py --expected-tag v0.12.0` using Python 3.14+ and the existing Windows .NET Framework compiler. This works on Windows or from WSL through the host compiler. It bundles pinned embedded Python, Windows wheels and GTK DLLs/data/licenses, then creates a `windows-x86_64-portable.zip`. Chromium is installed into that program's folder on first run. See [Windows build and validation](WINDOWS.md).

The hosted Windows job runs `AutoReserve.exe --self-test` on a native Windows runner. Publishing depends on both OS jobs and merges the Windows ZIP into the release with one combined SHA256SUMS file. Runtime source pins and dependency licenses are retained in the public manifest and bundled payload; private configuration, `.auth`, logs, downloaded browsers and caches are excluded.
