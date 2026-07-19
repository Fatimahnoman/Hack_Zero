import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from golden_tier_external_world.storage.backends.base import StorageBackend


class SqliteBackend(StorageBackend):
    def __init__(self, db_path: str | Path, table_name: str = "vault") -> None:
        self._path = Path(db_path)
        self._table = table_name
        self._lock = threading.RLock()
        self._local = threading.local()

        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with self._connection() as conn:
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {self._table} ("
                "key TEXT PRIMARY KEY, "
                "value TEXT NOT NULL, "
                "updated_at TEXT DEFAULT (datetime('now'))"
                ")"
            )

    def _connection(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                str(self._path),
                timeout=10,
                check_same_thread=False,
            )
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
        return self._local.conn

    def read(self) -> dict[str, Any]:
        with self._lock:
            conn = self._connection()
            result: dict[str, Any] = {}
            try:
                cursor = conn.execute(
                    f"SELECT key, value FROM {self._table}"
                )
                for key, value_str in cursor:
                    try:
                        result[key] = json.loads(value_str)
                    except (json.JSONDecodeError, TypeError):
                        result[key] = value_str
            except sqlite3.OperationalError:
                pass
            return result

    def write(self, data: dict[str, Any]) -> None:
        with self._lock:
            conn = self._connection()
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(f"DELETE FROM {self._table}")
                for key, value in data.items():
                    value_str = json.dumps(value, ensure_ascii=False, default=str)
                    conn.execute(
                        f"INSERT INTO {self._table} (key, value) VALUES (?, ?)",
                        (key, value_str),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def clear(self) -> None:
        with self._lock:
            conn = self._connection()
            conn.execute(f"DELETE FROM {self._table}")
            conn.commit()

    def close(self) -> None:
        if hasattr(self._local, "conn") and self._local.conn is not None:
            self._local.conn.close()
            self._local.conn = None

    @property
    def path(self) -> Path:
        return self._path

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
