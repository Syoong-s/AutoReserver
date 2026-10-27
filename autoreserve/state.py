"""Private session files, exclusive process lock, and durable submission journal."""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3

from .filelock import acquire, release


# ==========================================
# Function: Write private JSON state without partial replacement.
# Method: Flush and fsync a mode-0600 temporary file before rename.
# ==========================================
def private_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


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
            raise RuntimeError(f"此任务已有提交记录（{row[0]}），请先核对网站“我的订单”和 journal")
        for target in config.targets:
            for key in config.slot_keys(target):
                if self.db.execute("SELECT 1 FROM slots WHERE slot_key=?", (key,)).fetchone():
                    raise RuntimeError("候选包含曾提交的时段，请先核对网站订单，避免重复预约")

    # ==========================================
    # Function: Arm the submission latch before the final click.
    # Method: Commit task and selected slot claims in one transaction.
    # ==========================================
    def arm(self, config, target, task_key=None):
        self.ensure_new(config, task_key)
        key = task_key or config.task_key()
        with self.db:
            self.db.execute("INSERT INTO tasks VALUES (?, ?, ?, ?)", (
                key, "submit_intent", json.dumps({
                    "date": target.date, "court": target.court, "slots": target.slots,
                }, ensure_ascii=False), datetime.now(timezone.utc).isoformat(),
            ))
            for slot_key in config.slot_keys(target):
                self.db.execute("INSERT INTO slots VALUES (?, ?)", (slot_key, key))

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
