"""User-scoped application-menu entry for the relocatable Linux launcher."""

import os
from pathlib import Path
import tempfile


# ==========================================
# Function: Quote an argument for freedesktop Exec without passing it through a shell.
# Method: Escape desktop-string and Exec quoting layers separately, including literal percent signs.
# ==========================================
def desktop_argument(value):
    value = str(value).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$')
    return '"' + value.replace('\\', '\\\\').replace('%', '%%') + '"'


# ==========================================
# Function: Generate an app-menu launcher that retains paths containing spaces or field-code characters.
# Method: Use an isolated Python execv trampoline and encode the native program path as one literal argument.
# ==========================================
def desktop_entry(project):
    project = Path(project).resolve()
    if '\n' in str(project) or '\r' in str(project):
        raise ValueError('含换行符的文件夹不能创建应用菜单快捷方式')
    code = 'import os,sys; os.execv(sys.argv[1],sys.argv[1:])'
    return '\n'.join(['[Desktop Entry]', 'Type=Application', 'Version=1.0', 'Name=AutoReserve',
                      'Comment=Sports reservation workspace', 'Comment[zh_CN]=场馆预约工作台',
                      'Exec=/usr/bin/python3 -I -c ' + desktop_argument(code) + ' ' + desktop_argument(project / 'AutoReserve'),
                      'Icon=' + str(project / 'autoreserve/gtk_ui/icon.svg').replace('\\', '\\\\'),
                      'Terminal=false', 'Categories=Utility;', 'StartupNotify=true',
                      'StartupWMClass=AutoReserve', ''])


# ==========================================
# Function: Add the application to the current user's Linux menu without installing dependencies globally.
# Method: Atomically write one XDG desktop entry pointing to this portable folder.
# ==========================================
def install_menu(project):
    project = Path(project).resolve()
    if not (project / 'AutoReserve').is_file():
        return None
    data_home = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share'))
    applications = data_home / 'applications'
    applications.mkdir(parents=True, exist_ok=True)
    path = applications / 'org.autoreserve.Desktop.desktop'
    content = desktop_entry(project)
    if path.exists() and path.read_text() == content:
        return path
    descriptor, temporary = tempfile.mkstemp(prefix='.autoreserve-', dir=applications)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            handle.write(content)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path
