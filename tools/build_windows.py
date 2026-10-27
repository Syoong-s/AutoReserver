"""Build a native Windows 11 x86_64 portable ZIP using pinned app-local runtimes (Python 3.14+)."""

import argparse
import base64
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.release import public_files, version
from tools.windows_runtime import stage


# ==========================================
# Function: Address the Windows compiler through WSL mount points or the current Windows environment.
# Method: Convert WSL-owned input/output files only at the Windows process boundary, keeping build I/O in the repo.
# ==========================================
def host_path(path):
    if sys.platform == 'win32':
        return str(Path(path).resolve())
    return subprocess.check_output(['wslpath', '-w', str(Path(path).resolve())], text=True).strip()


# ==========================================
# Function: Compile a Windows-subsystem EXE with the existing .NET Framework compiler.
# Method: Use CodeDom with explicit x64/manifest options and encoded PowerShell input, avoiding shell argument interpolation.
# ==========================================
def compile_launcher(root):
    root = Path(root)
    shell = (Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
             if sys.platform == 'win32' else Path('/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'))
    if not shell.is_file():
        raise RuntimeError('Build on Windows 11 or WSL with the Windows PowerShell/.NET compiler available')
    source = host_path(ROOT / 'native/windows-launcher.cs').replace("'", "''")
    target = host_path(root / 'AutoReserve.exe').replace("'", "''")
    manifest = host_path(ROOT / 'native/windows.manifest').replace("'", "''")
    icon = host_path(ROOT / 'autoreserve/gtk_ui/icon.ico').replace("'", "''")
    command = ("$ErrorActionPreference='Stop'; $p=New-Object System.CodeDom.Compiler.CompilerParameters;"
               "$p.GenerateExecutable=$true;$p.GenerateInMemory=$false;"
               "$p.OutputAssembly='" + target + "';"
               "$p.ReferencedAssemblies.AddRange(@('System.dll','System.Core.dll','System.Drawing.dll','System.Windows.Forms.dll'));"
               "$p.CompilerOptions='/target:winexe /platform:x64 /optimize+ /win32manifest:\"" + manifest +
               "\" /win32icon:\"" + icon + "\"';"
               "$provider=New-Object Microsoft.CSharp.CSharpCodeProvider;"
               "$r=$provider.CompileAssemblyFromSource($p,[IO.File]::ReadAllText('" + source + "',[Text.Encoding]::UTF8));"
               "if($r.Errors.HasErrors){$r.Errors|ForEach-Object{[Console]::Error.WriteLine($_.ToString())};exit 1};")
    encoded = base64.b64encode(command.encode('utf-16-le')).decode('ascii')
    (root / 'AutoReserve.exe').unlink(missing_ok=True)
    subprocess.run([str(shell), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                    '-EncodedCommand', encoded], check=True, timeout=60)
    if (root / 'AutoReserve.exe').read_bytes()[:2] != b'MZ':
        raise RuntimeError('Windows launcher compiler did not produce a PE executable')


# ==========================================
# Function: Build an independent Windows folder containing only the application and its private runtimes.
# Method: Recreate staging so removed files cannot leak from an earlier build, then copy the Windows runtime allowlist.
# ==========================================
def build(output=None, expected_tag=None):
    release = version(ROOT)
    if expected_tag and expected_tag != 'v' + release:
        raise ValueError('Release tag must match v' + release)
    output = Path(output or ROOT / 'dist').resolve()
    output.mkdir(parents=True, exist_ok=True)
    folder = ROOT / '.runtime/windows-build' / ('AutoReserve-' + release)
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for relative, source in public_files(ROOT, target='windows'):
        destination = folder / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    stage(folder, ROOT / '.runtime/windows-build/downloads')
    compile_launcher(folder)
    notices = folder / '.runtime/licenses/AutoReserve'
    notices.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / 'LICENSE', notices / 'LICENSE')
    archive = output / ('AutoReserve-' + release + '-windows-x86_64-portable.zip')
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for path in sorted(folder.rglob('*')):
            if not path.is_file():
                continue
            relative = path.relative_to(folder)
            if relative.parts[0] == '.runtime' and relative.parts[1] not in {'python', 'site-packages', 'gtk', 'licenses'}:
                continue
            if '__pycache__' in relative.parts or path.suffix in {'.pyc', '.pyo', '.log', '.sqlite3'}:
                continue
            if relative.parts[0] in {'.auth', 'artifacts'} or relative.as_posix() in {'config/config.json', 'config/bootstrap-windows.json'}:
                continue
            package.write(path, folder.name + '/' + relative.as_posix())
    return archive


# ==========================================
# Function: Expose the Windows builder to local WSL development and a native Windows GitHub runner.
# Method: Validate the version before packaging and print only the resulting distributable archive path.
# ==========================================
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--expected-tag')
    args = parser.parse_args()
    print(build(args.output, args.expected_tag))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
