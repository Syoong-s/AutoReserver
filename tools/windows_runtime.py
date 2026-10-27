"""Fetch and stage the pinned Windows runtime without installing packages globally (Python 3.14+)."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import time
from urllib.request import Request, urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]


# ==========================================
# Function: Read the reproducible Windows Python, wheels and GTK dependency inventory.
# Method: Use the checked-in exact URLs, versions, sizes and SHA256 values instead of a moving resolver.
# ==========================================
def manifest(root=ROOT):
    return json.loads((Path(root) / 'native/windows-runtime.lock.json').read_text(encoding='utf-8'))


# ==========================================
# Function: Verify cached or downloaded dependency bytes before any extraction.
# Method: Compare both the exact size and a streamed SHA256 digest to the vendor artifact pin.
# ==========================================
def verified(path, record):
    path = Path(path)
    if not path.is_file() or path.stat().st_size != record['compressed_size']:
        return False
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest() == record['sha256']


# ==========================================
# Function: Retrieve one dependency into the application-local cache with bounded retries.
# Method: Require HTTPS, verify the complete temporary download and atomically promote only matching bytes.
# ==========================================
def download(record, cache, progress=print):
    if not record['url'].startswith('https://'):
        raise ValueError('Dependency downloads require HTTPS')
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    path = cache / record['url'].rsplit('/', 1)[-1]
    if verified(path, record):
        return path
    temporary = path.with_suffix(path.suffix + '.partial')
    for attempt in range(3):
        try:
            progress('下载 ' + path.name)
            request = Request(record['url'], headers={'User-Agent': 'AutoReserve-Windows/1'})
            with urlopen(request, timeout=45) as response, temporary.open('wb') as target:
                shutil.copyfileobj(response, target, 1024 * 1024)
            if not verified(temporary, record):
                raise ValueError('依赖文件校验失败：' + path.name)
            temporary.replace(path)
            return path
        except Exception:
            temporary.unlink(missing_ok=True)
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


# ==========================================
# Function: Prevent a Windows archive member from escaping its intended private destination.
# Method: Reject traversal, drive-qualified names, backslashes and ZIP symlinks before writing any payload.
# ==========================================
def safe_member(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
        raise ValueError('Unsafe runtime archive member: ' + name)
    return path


# ==========================================
# Function: Stage the isolated CPython embedded runtime or one vendor wheel.
# Method: Extract validated regular ZIP members, mapping wheel purelib/platlib payloads without invoking pip.
# ==========================================
def stage_zip(archive, destination, wheel=False):
    destination = Path(destination)
    with zipfile.ZipFile(archive) as source:
        for member in source.infolist():
            path = safe_member(member.filename)
            if ((member.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError('Runtime ZIP symlinks are not supported')
            if member.is_dir():
                continue
            if wheel and path.parts[0].endswith('.data'):
                if len(path.parts) < 3 or path.parts[1] not in {'purelib', 'platlib'}:
                    continue
                path = PurePosixPath(*path.parts[2:])
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(member) as incoming, target.open('wb') as output:
                shutil.copyfileobj(incoming, output)


# ==========================================
# Function: Select only redistributable GTK runtime data and native modules from an MSYS2 package.
# Method: Exclude headers, import libraries, build tools, package scripts and caches with fixed build-machine paths.
# ==========================================
def gtk_payload(path):
    name = path.as_posix()
    if name.startswith('bin/'):
        return path.suffix.lower() == '.dll' or path.name in {'gdk-pixbuf-query-loaders.exe', 'glib-compile-schemas.exe'}
    if name.startswith(('lib/gdk-pixbuf-2.0/', 'lib/gtk-3.0/', 'lib/gio/modules/')):
        return path.suffix.lower() == '.dll'
    return name.startswith(('share/icons/', 'share/licenses/', 'share/glib-2.0/schemas/', 'share/themes/')) and path.suffix != '.cache'


# ==========================================
# Function: Build the private GTK DLL/data tree without executing MSYS2 installation hooks.
# Method: Read pinned tar.zst packages with Python 3.14 and stream only allowlisted regular payloads.
# ==========================================
def stage_gtk(archive, destination):
    destination = Path(destination)
    with tarfile.open(archive, 'r:*') as source:
        for member in source:
            path = safe_member(member.name)
            if not member.isfile() or not path.parts or path.parts[0] != 'ucrt64':
                continue
            path = PurePosixPath(*path.parts[1:])
            if not gtk_payload(path):
                continue
            target = destination.joinpath(*path.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.extractfile(member) as incoming, target.open('wb') as output:
                shutil.copyfileobj(incoming, output)


# ==========================================
# Function: Prepare one chosen group of Windows runtime dependencies under a portable program folder.
# Method: Fetch independent archives concurrently, then extract sequentially so shared package paths cannot race.
# ==========================================
def stage(root, cache, groups=('python', 'wheels', 'gtk'), progress=print):
    root, cache = Path(root), Path(cache)
    spec = manifest(root)
    records = []
    if 'python' in groups:
        records.append(('python', spec['python']))
    if 'wheels' in groups:
        records.extend(('wheels', item) for item in spec['wheels'])
    if 'gtk' in groups:
        records.extend(('gtk', item) for item in spec['gtk']['packages'])
    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(lambda item: download(item[1], cache, progress), records))
    for (kind, record), archive in zip(records, paths):
        progress('准备 ' + record.get('name', 'CPython embedded'))
        if kind == 'gtk':
            stage_gtk(archive, root / '.runtime/gtk')
        else:
            stage_zip(archive, root / ('.runtime/python' if kind == 'python' else '.runtime/site-packages'), kind == 'wheels')
    if 'python' in groups:
        major, minor, *_ = spec['python']['version'].split('.')
        pth = root / f'.runtime/python/python{major}{minor}._pth'
        pth.write_text(f'python{major}{minor}.zip\n.\n../site-packages\n../../\n', encoding='utf-8')


# ==========================================
# Function: Pre-fetch the entire reviewed dependency inventory for offline packaging.
# Method: Reuse verified application-local caches without creating an environment or changing PATH.
# ==========================================
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true')
    args = parser.parse_args()
    spec = manifest()
    records = [spec['python'], *spec['wheels'], *spec['gtk']['packages']]
    cache = ROOT / '.runtime/windows-build/downloads'
    if args.fetch:
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda record: download(record, cache), records))
    else:
        stage(ROOT, cache)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
