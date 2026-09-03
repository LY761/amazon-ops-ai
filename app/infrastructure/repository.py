from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class Repository:
    def __init__(self, database: Path) -> None:
        self.database = database
        database.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database)
        connection.row_factory = sqlite3.Row
        return connection

    def init(self) -> None:
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                  id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
                  status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
                  fail_once INTEGER NOT NULL DEFAULT 0, error TEXT, report_json TEXT,
                  created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS steps (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, name TEXT NOT NULL,
                  status TEXT NOT NULL, detail TEXT, artifact_path TEXT,
                  UNIQUE(task_id, name), FOREIGN KEY(task_id) REFERENCES tasks(id)
                );
                CREATE TABLE IF NOT EXISTS products (sku TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS analysis_results (task_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS attempt_history (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL, attempt INTEGER NOT NULL,
                  status TEXT NOT NULL, error TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                  UNIQUE(task_id, attempt), FOREIGN KEY(task_id) REFERENCES tasks(id)
                );
            """)

    def create_task(self, task_id: str, key: str, fail_once: bool) -> tuple[dict[str, Any], bool]:
        try:
            with self.connect() as db:
                db.execute("INSERT INTO tasks(id,idempotency_key,status,fail_once) VALUES(?,?,?,?)", (task_id, key, "pending", int(fail_once)))
            return self.get_task(task_id), True
        except sqlite3.IntegrityError:
            with self.connect() as db:
                row = db.execute("SELECT id FROM tasks WHERE idempotency_key=?", (key,)).fetchone()
            return self.get_task(row["id"]), False

    def get_task(self, task_id: str) -> dict[str, Any]:
        with self.connect() as db:
            task = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task:
                raise KeyError(task_id)
            data = dict(task)
            data["steps"] = [dict(row) for row in db.execute("SELECT name,status,detail,artifact_path FROM steps WHERE task_id=? ORDER BY id", (task_id,))]
            data["attempt_history"] = [dict(row) for row in db.execute("SELECT attempt,status,error,created_at FROM attempt_history WHERE task_id=? ORDER BY attempt", (task_id,))]
            data["report"] = json.loads(data.pop("report_json")) if data.get("report_json") else None
            return data

    def list_tasks(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT id,idempotency_key,status,attempt,error,created_at FROM tasks ORDER BY created_at DESC")]

    def update_task(self, task_id: str, status: str, error: str | None = None, report: dict[str, Any] | None = None, increment: bool = False) -> None:
        with self.connect() as db:
            db.execute("UPDATE tasks SET status=?, error=?, report_json=COALESCE(?,report_json), attempt=attempt+?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, error, json.dumps(report) if report else None, int(increment), task_id))

    def record_attempt(self, task_id: str, attempt: int, status: str, error: str | None = None) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO attempt_history(task_id,attempt,status,error) VALUES(?,?,?,?)", (task_id, attempt, status, error))

    def upsert_step(self, task_id: str, name: str, status: str, detail: str = "", artifact_path: str | None = None) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO steps(task_id,name,status,detail,artifact_path) VALUES(?,?,?,?,?) ON CONFLICT(task_id,name) DO UPDATE SET status=excluded.status,detail=excluded.detail,artifact_path=excluded.artifact_path", (task_id,name,status,detail,artifact_path))

    def step_succeeded(self, task_id: str, name: str) -> bool:
        with self.connect() as db:
            row = db.execute("SELECT status FROM steps WHERE task_id=? AND name=?", (task_id,name)).fetchone()
            return bool(row and row["status"] == "succeeded")

    def save_analysis(self, task_id: str, report: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO analysis_results(task_id,payload) VALUES(?,?) ON CONFLICT(task_id) DO UPDATE SET payload=excluded.payload", (task_id,json.dumps(report)))

    def get_analysis(self, task_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM analysis_results WHERE task_id=?", (task_id,)).fetchone()
            if not row:
                raise KeyError(task_id)
            return json.loads(row["payload"])

    def save_products(self, products: list[dict[str, Any]]) -> None:
        with self.connect() as db:
            db.executemany(
                "INSERT INTO products(sku,payload) VALUES(?,?) ON CONFLICT(sku) DO UPDATE SET payload=excluded.payload",
                [(product["sku"], json.dumps(product)) for product in products],
            )

    def product_count(self) -> int:
        with self.connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM products").fetchone()[0])
