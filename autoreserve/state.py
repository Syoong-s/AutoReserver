"""Private session files, exclusive process lock, and durable submission journal."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time

from .filelock import acquire, release

_JSON_LOCKS=[threading.RLock() for _ in range(64)]


# ==========================================
# Function: Write private JSON state without partial replacement.
# Method: Serialize writes per path, use unique staging files and retry transient Windows sharing violations.
# ==========================================
def private_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock=_JSON_LOCKS[hash(os.path.normcase(str(path.absolute())))%len(_JSON_LOCKS)]
    with lock:
        fd,tmp=tempfile.mkstemp(prefix=path.name+'.',suffix='.tmp',dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(tmp, 0o600)
            for attempt in range(7):
                try:
                    os.replace(tmp, path)
                    break
                except PermissionError as exc:
                    if os.name!='nt' or getattr(exc,'winerror',None) not in {5,32,33} or attempt==6:
                        raise
                    time.sleep(min(.01*2**attempt,.1))
        finally:
            Path(tmp).unlink(missing_ok=True)


# ==========================================
# Function: Prevent concurrent use of the same account state.
# Method: Hold a nonblocking POSIX or Windows byte-range lock for the command lifetime.
# ==========================================
@contextmanager
def account_lock(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    with (directory / "account.lock").open("a+b") as handle:
        try:
            acquire(handle)
        except BlockingIOError as exc:
            raise RuntimeError("同一登录目录已有程序运行，请先关闭另一个进程") from exc
        try:
            yield
        finally:
            release(handle)


# ==========================================
# Function: Persist one task and its occupied slots before submission.
# Method: Use SQLite uniqueness constraints and a single transaction.
# ==========================================
class Journal:
    # ==========================================
    # Function: Open the submission database.
    # Method: Enable full synchronous writes and initialize unique records.
    # ==========================================
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.path)
        os.chmod(self.path, 0o600)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_key TEXT PRIMARY KEY, status TEXT NOT NULL,
                detail TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS slots (
                slot_key TEXT PRIMARY KEY, task_key TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS attempts (
                task_key TEXT NOT NULL, status TEXT NOT NULL,
                detail TEXT NOT NULL, updated_at TEXT NOT NULL
            );
        """)

    # ==========================================
    # Function: Close the database explicitly.
    # Method: Release the SQLite connection.
    # ==========================================
    def close(self):
        self.db.close()

    # ==========================================
    # Function: Block an already attempted task or overlapping slots.
    # Method: Query durable records irrespective of success or uncertainty.
    # ==========================================
    def ensure_new(self, config, task_key=None):
        row = self.db.execute("SELECT status FROM tasks WHERE task_key=?", (task_key or config.task_key(),)).fetchone()
        if row:
            raise RuntimeError(f"此任务已有提交记录（{row[0]}），请打开左栏「历史订单」查看；需要重新监测时可删除对应本地记录")
        for target in config.targets:
            for key in config.slot_keys(target):
                row = self.db.execute("SELECT t.detail FROM slots s JOIN tasks t ON t.task_key=s.task_key WHERE s.slot_key=?", (key,)).fetchone()
                if row:
                    detail = json.loads(row[0])
                    order = str(detail.get('order_id') or detail.get('goal_name') or '已有提交记录')
                    raise RuntimeError(f"候选包含曾提交的时段：{target.date} {target.court} {' / '.join(target.slots)}，记录 {order}。请打开左栏「历史订单」查看；需要重新监测时可删除对应本地记录")

    # ==========================================
    # Function: Arm the submission latch before the final click.
    # Method: Acquire SQLite's write reservation before checking claims so concurrent browsers cannot arm the same slot.
    # ==========================================
    def arm(self, config, target, task_key=None):
        key = task_key or config.task_key()
        try:
            self.db.execute('BEGIN IMMEDIATE')
            self.ensure_new(config, task_key)
            self.db.execute("INSERT INTO tasks VALUES (?, ?, ?, ?)", (
                key, "submit_intent", json.dumps({
                    "date": target.date, "court": target.court, "slots": target.slots,
                    "venue": config.for_target(target).venue, "sport": config.for_target(target).sport,
                }, ensure_ascii=False), datetime.now(timezone.utc).isoformat(),
            ))
            for slot_key in config.slot_keys(target):
                self.db.execute("INSERT INTO slots VALUES (?, ?)", (slot_key, key))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    # ==========================================
    # Function: Persist a definitive or uncertain outcome.
    # Method: Update state without removing slot claims.
    # ==========================================
    def update(self, task_key, status, detail):
        previous = self.record(task_key)
        detail = (previous["detail"] if previous else {}) | detail
        with self.db:
            self.db.execute("UPDATE tasks SET status=?, detail=?, updated_at=? WHERE task_key=?", (
                status, json.dumps(detail, ensure_ascii=False), datetime.now(timezone.utc).isoformat(), task_key,
            ))

    # ==========================================
    # Function: Read the latest durable state for a stable goal identity.
    # Method: Return its stored result without claiming or modifying slots.
    # ==========================================
    def record(self, task_key):
        row = self.db.execute("SELECT status, detail, updated_at FROM tasks WHERE task_key=?", (task_key,)).fetchone()
        return {"status": row[0], "detail": json.loads(row[1]), "updated_at": row[2]} if row else None

    # ==========================================
    # Function: Retry only an explicitly rejected submission in multi-goal mode.
    # Method: Archive the rejection and atomically release its unused slot claims.
    # ==========================================
    def archive_rejection(self, task_key):
        with self.db:
            row = self.db.execute("SELECT * FROM tasks WHERE task_key=?", (task_key,)).fetchone()
            if not row or row[1] != "rejected":
                raise RuntimeError("仅明确被网站拒绝的订单可以自动重试")
            self.db.execute("INSERT INTO attempts VALUES (?, ?, ?, ?)", row)
            self.db.execute("DELETE FROM slots WHERE task_key=?", (task_key,))
            self.db.execute("DELETE FROM tasks WHERE task_key=?", (task_key,))

    # ==========================================
    # Function: Explicitly reset one execution without deleting its historical order.
    # Method: Archive its immutable result and release only its own active submission and slot claims in one transaction.
    # ==========================================
    def reset_goal(self, task_key):
        try:
            self.db.execute('BEGIN IMMEDIATE')
            row = self.db.execute('SELECT * FROM tasks WHERE task_key=?', (task_key,)).fetchone()
            if row:
                self.db.execute('INSERT INTO attempts VALUES (?, ?, ?, ?)', row)
                self.db.execute('DELETE FROM slots WHERE task_key=?', (task_key,))
                self.db.execute('DELETE FROM tasks WHERE task_key=?', (task_key,))
            self.db.commit()
            return row is not None
        except BaseException:
            self.db.rollback()
            raise

    # ==========================================
    # Function: Preserve legacy single-order progress when opening the GUI.
    # Method: Rename the task key and its slot ownership in one transaction.
    # ==========================================
    def migrate_key(self, previous, current):
        if previous == current or not self.record(previous) or self.record(current):
            return
        with self.db:
            self.db.execute("UPDATE tasks SET task_key=? WHERE task_key=?", (current, previous))
            self.db.execute("UPDATE slots SET task_key=? WHERE task_key=?", (current, previous))

    # ==========================================
    # Function: Preserve legacy slot claims across equivalent multi-venue route variants.
    # Method: Add canonical aliases for a known recorded order without removing old claims.
    # ==========================================
    def add_slot_aliases(self, config, target, task_key):
        if self.record(task_key) is None:
            return
        with self.db:
            self.db.executemany("INSERT OR IGNORE INTO slots VALUES (?, ?)",
                                [(key, task_key) for key in config.slot_keys(target)])

    # ==========================================
    # Function: Display submission history without exposing authentication.
    # Method: Return rows with decoded result details.
    # ==========================================
    def entries(self):
        return [{"task_key": key, "status": status, "detail": json.loads(detail), "updated_at": stamp}
                for key, status, detail, stamp in self.db.execute("SELECT * FROM tasks ORDER BY updated_at DESC")]

    # ==========================================
    # Function: Expose all current and archived local order attempts to the history window.
    # Method: Give task rows and archived attempts distinct stable deletion identities without changing booking keys.
    # ==========================================
    def history(self):
        current = [dict(row, entry_id='task:'+row['task_key']) for row in self.entries()]
        previous = [{'entry_id':f'attempt:{rowid}', 'task_key':key, 'status':status,
                     'detail':json.loads(detail), 'updated_at':stamp}
                    for rowid,key,status,detail,stamp in self.db.execute('SELECT rowid,* FROM attempts')]
        return sorted(current+previous, key=lambda row:row['updated_at'], reverse=True)

    # ==========================================
    # Function: Remove an explicitly selected local history record and its corresponding duplicate-booking claims.
    # Method: Delete task/slot rows atomically or remove only the selected archived attempt; never contact the website.
    # ==========================================
    def delete_history(self, entry_id):
        if not isinstance(entry_id,str):
            raise ValueError('订单记录标识无效')
        kind,separator,key = entry_id.partition(':')
        if not separator or not key:
            raise ValueError('订单记录标识无效')
        with self.db:
            if kind == 'task':
                self.db.execute('DELETE FROM slots WHERE task_key=?',(key,))
                return self.db.execute('DELETE FROM tasks WHERE task_key=?',(key,)).rowcount > 0
            if kind == 'attempt' and key.isdecimal():
                return self.db.execute('DELETE FROM attempts WHERE rowid=?',(int(key),)).rowcount > 0
        raise ValueError('订单记录标识无效')
