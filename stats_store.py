"""Transactional ingestion state and recoverable publication of daily JSON."""

import gzip
import hashlib
import json
import lzma
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

from stats_io import atomic_json_write


def open_input(path):
    if str(path).endswith(".gz"):
        return gzip.open(path, "rb")
    if str(path).endswith(".xz"):
        return lzma.open(path, "rb")
    return open(path, "rb")


def input_digest(path):
    digest = hashlib.sha256()
    with open_input(path) as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def snapshot_input(path):
    """Give the existing parser a stable, plain-text input without changing it."""
    digest = hashlib.sha256()
    with (
        open_input(path) as source,
        tempfile.NamedTemporaryFile(suffix=".log") as snapshot,
    ):
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
            snapshot.write(chunk)
        snapshot.flush()
        yield snapshot.name, digest.hexdigest()


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
                ignore_deltas INTEGER NOT NULL
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

    def replace_input(self, digest, source, ignore_deltas, days):
        """Called inside the same transaction as every other input in the batch."""
        touched = {
            row[0]
            for row in self.db.execute(
                "SELECT date FROM contributions WHERE digest = ?", (digest,)
            )
        } | days.keys()
        self.db.execute(
            "INSERT INTO inputs VALUES (?, ?, ?) "
            "ON CONFLICT(digest) DO UPDATE SET source=excluded.source, "
            "ignore_deltas=excluded.ignore_deltas",
            (digest, str(source), ignore_deltas),
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
