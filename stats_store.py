"""Transactional ingestion state and recoverable publication of daily JSON."""

import fcntl
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def atomic_json_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            mode = path.stat().st_mode & 0o777 if path.exists() else 0o644
            os.fchmod(stream.fileno(), mode)
            json.dump(data, stream, sort_keys=True, indent=4)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def add_counts(target, source):
    """Merge daily counters, including nested dimensions and [total, update] pairs."""
    for key, value in source.items():
        if key == "date" and isinstance(value, str):
            if target["date"] != value:
                raise ValueError("Cannot combine statistics for different dates")
        elif isinstance(value, dict):
            add_counts(target.setdefault(key, {}), value)
        elif isinstance(value, list):
            old = target.get(key, [0, 0])
            target[key] = [old[0] + value[0], old[1] + value[1]]
        else:
            target[key] = target.get(key, 0) + value


class StatsStore:
    """Use under the destination lock, including publication, to serialize writers."""

    def __init__(self, dest):
        self.dest = Path(dest)
        self.dest.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.dest / ".ingestion.sqlite3")
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS inputs (
                digest TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                ignore_deltas INTEGER NOT NULL,
                quality TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS baseline (
                date TEXT PRIMARY KEY,
                data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS contributions (
                digest TEXT NOT NULL REFERENCES inputs(digest),
                date TEXT NOT NULL,
                data TEXT NOT NULL,
                PRIMARY KEY (digest, date)
            );
            CREATE INDEX IF NOT EXISTS contributions_date ON contributions(date);
            CREATE TABLE IF NOT EXISTS days (
                date TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                published INTEGER NOT NULL DEFAULT 0
            );
        """)

    def close(self):
        self.db.close()

    def already_processed(self, digest, ignore_deltas, reprocess=False):
        row = self.db.execute(
            "SELECT ignore_deltas FROM inputs WHERE digest = ?", (digest,)
        ).fetchone()
        if row is None or reprocess:
            return False
        if bool(row[0]) != ignore_deltas:
            raise ValueError(
                "Input was already processed with different --ignore-deltas settings; "
                "use --reprocess to replace its contribution"
            )
        return True

    def replace_input(self, digest, source, ignore_deltas, quality, days):
        """Called inside the same transaction as every other input in the batch."""
        touched = {
            row[0]
            for row in self.db.execute(
                "SELECT date FROM contributions WHERE digest = ?", (digest,)
            )
        } | days.keys()
        self.db.execute(
            "INSERT INTO inputs VALUES (?, ?, ?, ?) "
            "ON CONFLICT(digest) DO UPDATE SET source=excluded.source, "
            "ignore_deltas=excluded.ignore_deltas, quality=excluded.quality",
            (digest, str(source), ignore_deltas, json.dumps(quality)),
        )
        self.db.execute("DELETE FROM contributions WHERE digest = ?", (digest,))
        for date, data in days.items():
            baseline = self.db.execute(
                "SELECT 1 FROM baseline WHERE date = ?", (date,)
            ).fetchone()
            if baseline is None:
                path = self.dest / f"{date}.json"
                initial = (
                    json.loads(path.read_text()) if path.exists() else {"date": date}
                )
                if initial["date"] != date:
                    raise ValueError(f"Date mismatch in {path}")
                self.db.execute(
                    "INSERT INTO baseline VALUES (?, ?)", (date, json.dumps(initial))
                )
            self.db.execute(
                "INSERT INTO contributions VALUES (?, ?, ?)",
                (digest, date, json.dumps(data)),
            )
        return touched

    def rebuild_days(self, dates, empty_day):
        for date in dates:
            total = empty_day(date)
            baseline = self.db.execute(
                "SELECT data FROM baseline WHERE date = ?", (date,)
            ).fetchone()
            if baseline:
                add_counts(total, json.loads(baseline[0]))
            for (data,) in self.db.execute(
                "SELECT data FROM contributions WHERE date = ?", (date,)
            ):
                add_counts(total, json.loads(data))
            self.db.execute(
                "INSERT INTO days VALUES (?, ?, 0) "
                "ON CONFLICT(date) DO UPDATE SET data=excluded.data, published=0",
                (date, json.dumps(total)),
            )

    def publish(self):
        # A crash before acknowledgement simply republishes the committed value.
        for date, data in self.db.execute(
            "SELECT date, data FROM days WHERE published = 0"
        ).fetchall():
            path = self.dest / f"{date}.json"
            print(f"saving updated stats {path}")
            atomic_json_write(path, json.loads(data))
            with self.db:
                self.db.execute("UPDATE days SET published = 1 WHERE date = ?", (date,))
