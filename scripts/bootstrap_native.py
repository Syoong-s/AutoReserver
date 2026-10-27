"""Rootless Ubuntu runtime repair: download packages into the portable folder only."""

import os
from pathlib import Path
import platform
import re
import shutil
import subprocess

GTK_LIBRARIES = ('libgtk-3.so.0', 'libgdk-3.so.0', 'libglib-2.0.so.0',
                 'libgobject-2.0.so.0', 'libgio-2.0.so.0', 'libcairo.so.2')
LIB_PACKAGES = {
    'libnss3.so': 'libnss3', 'libnssutil3.so': 'libnss3', 'libsmime3.so': 'libnss3',
    'libnspr4.so': 'libnspr4', 'libplc4.so': 'libnspr4', 'libplds4.so': 'libnspr4',
    'libasound.so.2': 'libasound2', 'libgbm.so.1': 'libgbm1', 'libdrm.so.2': 'libdrm2',
    'libatk-1.0.so.0': 'libatk1.0-0', 'libatk-bridge-2.0.so.0': 'libatk-bridge2.0-0',
    'libatspi.so.0': 'libatspi2.0-0', 'libcups.so.2': 'libcups2',
    'libX11.so.6': 'libx11-6', 'libXcomposite.so.1': 'libxcomposite1',
    'libXdamage.so.1': 'libxdamage1', 'libXext.so.6': 'libxext6',
    'libXfixes.so.3': 'libxfixes3', 'libXrandr.so.2': 'libxrandr2',
    'libxcb.so.1': 'libxcb1', 'libxkbcommon.so.0': 'libxkbcommon0',
    'libpango-1.0.so.0': 'libpango-1.0-0', 'libcairo.so.2': 'libcairo2',
    'libdbus-1.so.3': 'libdbus-1-3', 'libexpat.so.1': 'libexpat1',
    'libfontconfig.so.1': 'libfontconfig1', 'libfreetype.so.6': 'libfreetype6',
}


# ==========================================
# Function: Keep private font lookup working after the portable application folder moves.
# Method: Rebuild absolute font/cache paths from the current runtime location without downloading fonts again.
# ==========================================
def font_configuration(runtime):
    from xml.sax.saxutils import escape
    runtime = Path(runtime)
    fonts = runtime / 'native/usr/share/fonts'
    if not fonts.exists():
        return None
    content = ('<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
               '<include ignore_missing="yes">/etc/fonts/fonts.conf</include><dir>' + escape(str(fonts)) +
               '</dir><cachedir>' + escape(str(runtime / 'cache/fontconfig')) + '</cachedir></fontconfig>')
    config = runtime / 'fonts.conf'
    if not config.exists() or config.read_text() != content:
        config.write_text(content)
    return config


# ==========================================
# Function: Identify Ubuntu's package-name ABI transition without executing OS metadata.
# Method: Read VERSION_ID as data and apply t64 names only on Ubuntu 24.04 or newer.
# ==========================================
def ubuntu_t64():
    values = {}
    for line in Path('/etc/os-release').read_text().splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            values[key] = value.strip('"')
    if values.get('ID') != 'ubuntu':
        raise RuntimeError('缺少系统运行库时，目前仅支持 Ubuntu 自动本地修复。')
    return tuple(int(v) for v in values['VERSION_ID'].split('.')[:2]) >= (24, 4)


# ==========================================
# Function: Resolve native libraries from app-local files before system runtime libraries.
# Method: Prepare a fresh subprocess environment with private loader, font and data paths.
# ==========================================
def native_environment(project, env):
    result = dict(env)
    runtime = Path(project) / '.runtime'
    prefix = runtime / 'native'
    triplet = {'x86_64': 'x86_64-linux-gnu', 'aarch64': 'aarch64-linux-gnu'}.get(platform.machine(), '')
    libraries = [prefix / 'usr/lib' / triplet, prefix / 'lib' / triplet, prefix / 'usr/lib', prefix / 'lib']
    # Loader overrides from unrelated Python/Conda environments must not leak into this client.
    result['LD_LIBRARY_PATH'] = os.pathsep.join(str(p) for p in libraries if p.is_dir())
    result['XDG_DATA_DIRS'] = os.pathsep.join([str(prefix / 'usr/share'), '/usr/local/share', '/usr/share'])
    fonts = font_configuration(runtime)
    if fonts:
        result['FONTCONFIG_FILE'] = str(fonts)
    else:
        result.pop('FONTCONFIG_FILE', None)
    return result


# ==========================================
# Function: Detect native GTK failures in a clean interpreter rather than a polluted loader.
# Method: Load each required soname in a child and return a concise diagnostic.
# ==========================================
def gtk_available(python, env):
    source = 'import ctypes; ' + '; '.join('ctypes.CDLL(%r)' % name for name in GTK_LIBRARIES)
    return subprocess.run([str(python), '-c', source], env=env, capture_output=True).returncode == 0


# ==========================================
# Function: Identify Chromium's missing shared libraries without opening a website.
# Method: Parse only unresolved sonames from the Linux loader report.
# ==========================================
def missing_libraries(executable, env):
    result = subprocess.run(['ldd', str(executable)], env=env, capture_output=True, text=True, timeout=30)
    return re.findall(r'^\s*(\S+)\s+=>\s+not found\s*$', result.stdout, re.MULTILINE)


# ==========================================
# Function: Download Ubuntu runtime packages without apt installation or global cache writes.
# Method: Redirect every writable apt directory, retain read-only system status, then unpack .deb files.
# ==========================================
class NativeInstaller:
    # ==========================================
    # Function: Bind runtime repair to one portable application directory.
    # Method: Inject the logged command runner and create only application-local staging folders.
    # ==========================================
    def __init__(self, project, run):
        self.root = Path(project) / '.runtime'
        self.apt = self.root / 'apt'
        self.prefix = self.root / 'native'
        self.run = run
        self.updated = False

    # ==========================================
    # Function: Keep apt's lists, downloads, locks, logs and extended state out of global locations.
    # Method: Override absolute writable paths while reading the host's package inventory.
    # ==========================================
    def options(self):
        for relative in ('state/lists/partial', 'cache/archives/partial', 'log'):
            (self.apt / relative).mkdir(parents=True, exist_ok=True)
        config = self.apt / 'no-hooks.conf'
        config.write_text(''.join('#clear ' + key + ';\n' for key in (
            'APT::Update::Post-Invoke', 'APT::Update::Post-Invoke-Success',
            'DPkg::Pre-Invoke', 'DPkg::Post-Invoke', 'DPkg::Pre-Install-Pkgs')))
        values = {'Dir::State': str(self.apt / 'state'),
                  'Dir::State::status': '/var/lib/dpkg/status',
                  'Dir::Cache': str(self.apt / 'cache'), 'Dir::Log': str(self.apt / 'log'),
                  'Debug::NoLocking': '1', 'APT::Get::List-Cleanup': '0'}
        return ['-c', str(config), *[word for key, value in values.items() for word in ('-o', key + '=' + value)]]

    # ==========================================
    # Function: Repair absent libraries while leaving installed system packages untouched.
    # Method: Use authenticated apt downloads only, unpack locally and skip the host libc/loader.
    # ==========================================
    def install(self, packages):
        if not shutil.which('apt-get') or not shutil.which('dpkg-deb'):
            raise RuntimeError('自动本地修复需要 Ubuntu 的 apt-get 和 dpkg-deb。')
        if not packages or any(not re.fullmatch(r'[a-z0-9][a-z0-9+.-]*', p) for p in packages):
            raise ValueError('无效的本地运行库包名')
        options = self.options()
        if not self.updated:
            self.run(['apt-get', *options, 'update'], '读取 Ubuntu 运行库目录（保存在程序文件夹）')
            self.updated = True
        self.run(['apt-get', *options, '--download-only', '--assume-yes', '--no-install-recommends',
                  '--reinstall', 'install', *sorted(set(packages))], '下载缺少的 Linux 运行库')
        self.prefix.mkdir(parents=True, exist_ok=True)
        for archive in sorted((self.apt / 'cache/archives').glob('*.deb')):
            if archive.name.startswith(('libc6_', 'libc-bin_')):
                continue
            self.run(['dpkg-deb', '--extract', str(archive), str(self.prefix)], '解压本地运行库：' + archive.name.split('_')[0])
        schemas = self.prefix / 'usr/share/glib-2.0/schemas'
        if schemas.is_dir() and shutil.which('glib-compile-schemas'):
            self.run(['glib-compile-schemas', str(schemas)], '整理本地 GTK 配置')
        font_configuration(self.root)

    # ==========================================
    # Function: Resolve only the native packages Chromium actually cannot load.
    # Method: Translate unresolved sonames to Ubuntu packages, respecting the t64 transition.
    # ==========================================
    def repair_browser(self, missing):
        use_t64 = ubuntu_t64()
        packages = []
        for library in missing:
            package = LIB_PACKAGES.get(library)
            if not package:
                raise RuntimeError('无法自动识别系统运行库：' + library)
            if use_t64 and package in {'libasound2', 'libatk1.0-0', 'libatk-bridge2.0-0', 'libatspi2.0-0', 'libcups2'}:
                package += 't64'
            packages.append(package)
        self.install(packages)
