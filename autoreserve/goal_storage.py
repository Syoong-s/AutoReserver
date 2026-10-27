"""Removal of goal-owned evidence and legacy cached plans, independent of durable order history."""

import hashlib
import json
from pathlib import Path
import shutil

from .state import private_json
from .config import config_from_dict


# ==========================================
# Function: Give each selected goal or repeat template its own evidence directory.
# Method: Hash the internal identity rather than placing user names or arbitrary strings in a filesystem path.
# ==========================================
def goal_directory(config_path, identity):
    return Path(config_path).parent / 'artifacts/goals' / hashlib.sha256(identity.encode()).hexdigest()


# ==========================================
# Function: Clear target-owned files without removing login state, shared catalog caches or order databases.
# Method: Remove isolated evidence roots and scrub only matching goal IDs from known legacy plans/backups and reports.
# ==========================================
def clear_goal_files(config_path, auth_dir, identities, keys=()):
    config_path, auth_dir = Path(config_path), Path(auth_dir)
    ids = {identity for identity in identities if not identity.startswith('rule:')}
    rules = {identity[5:] for identity in identities if identity.startswith('rule:')}
    for identity in identities:
        folder = goal_directory(config_path,identity)
        if folder.is_symlink():
            folder.unlink()
        elif folder.is_dir():
            if not folder.resolve().is_relative_to(config_path.parent.resolve()/'artifacts'):
                raise ValueError('目标文件目录指向程序配置文件夹之外，已停止清理')
            shutil.rmtree(folder)
    plans = [*auth_dir.glob('plans/*.json'),auth_dir/'workspace-v1.json',
             *config_path.parent.glob('legacy-config*.json')]
    for path in plans:
        if not path.is_file() or path.is_symlink():
            continue
        try:
            original = json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError):
            continue
        if not isinstance(original,dict):
            continue
        value = dict(original)
        owned=set(ids) if not keys else set()
        try:
            profile=config_from_dict(original)
            for goal in profile.goals:
                key=profile.goal_key(goal)
                if key in keys or any(identity.startswith('import-'+key[:24]) for identity in ids):
                    owned.add(goal.id)
        except (ValueError,TypeError,KeyError):
            if not original.get('venue'):
                owned=set(ids)
        if isinstance(value.get('goals'),list):
            value['goals'] = [g for g in value['goals'] if g.get('id') not in owned and g.get('repeat_id') not in rules]
        if isinstance(value.get('repeat_rules'),list):
            value['repeat_rules'] = [r for r in value['repeat_rules'] if r.get('id') not in rules]
        if value != original:
            if path.parent == auth_dir/'plans' and not any(value.get(k) for k in ('goals','repeat_rules','targets')):
                path.unlink()
            else:
                private_json(path,value)
    root = config_path.parent/'artifacts'
    if root.is_symlink():
        return
    for path in list(root.glob('**/result.json')):
        if path.is_symlink():
            continue
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError):
            continue
        if not isinstance(value,dict) or not isinstance(value.get('goals'),list):
            continue
        goals = value['goals']
        remaining = [g for g in goals if g.get('id') not in ids]
        if len(remaining) == len(goals):
            continue
        if not remaining and path.parent != root and not path.parent.is_symlink():
            shutil.rmtree(path.parent)
        else:
            value.update(goals=remaining,total=len(remaining),completed=sum(g.get('status')=='submitted' for g in remaining))
            private_json(path,value)
