"""Application-relative workspace paths and one-time legacy config migration."""

from pathlib import Path

from .config import load_config, save_config


# ==========================================
# Function: Anchor portable application data to the program directory rather than the caller's cwd.
# Method: Resolve the package's containing application folder, independent of shell location.
# ==========================================
def application_dir():
    return Path(__file__).resolve().parent.parent


# ==========================================
# Function: Return the single default user-config location.
# Method: Keep runtime configuration beneath the application-local config folder.
# ==========================================
def default_config_path():
    return application_dir()/'config'/'config.json'


# ==========================================
# Function: Preserve the existing account/journal location across the GUI architecture migration.
# Method: Anchor private state to the program folder without moving an active SQLite database.
# ==========================================
def default_auth_dir():
    return application_dir()/'.auth'


# ==========================================
# Function: Migrate an older root-level config without overwriting a current configuration.
# Method: Validate and atomically save the new file before moving the original into config backups.
# ==========================================
def prepare_config(path, app_dir=None):
    base=Path(app_dir).resolve() if app_dir else application_dir()
    path=Path(path).resolve()
    target=base/'config'/'config.json'
    if path!=target:
        return path
    target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    legacy=base/'config.json'
    if not target.exists() and legacy.is_file():
        value=load_config(legacy)
        save_config(value,target)
        target.chmod(0o600)
        backup=target.parent/'legacy-config.json'
        if backup.exists():
            index=1
            while (target.parent/f'legacy-config-{index}.json').exists():
                index+=1
            backup=target.parent/f'legacy-config-{index}.json'
        legacy.replace(backup)
    return target
