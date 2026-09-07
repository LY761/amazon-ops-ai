from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

class Repository:
    def __init__(self, database: Path) -> None:
        self.database = database
        database.parent.mkdir(parents=True, exist_ok=True)
        self.init()
    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection
    def init(self) -> None:
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS rpa_jobs (
                  task_id TEXT PRIMARY KEY, worker_id TEXT, claim_id TEXT UNIQUE,
                  token TEXT, result_json TEXT,
                  FOREIGN KEY(task_id) REFERENCES tasks(id)
                );
                CREATE TABLE IF NOT EXISTS tasks (
                  id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL,
                  platform TEXT NOT NULL DEFAULT 'amazon',
                  status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
                  fail_once INTEGER NOT NULL DEFAULT 0, error TEXT, report_json TEXT,
                  approval_status TEXT NOT NULL DEFAULT 'pending', approved_by TEXT, approved_at TEXT,
                  execution_started_at TEXT, completed_at TEXT,
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
                CREATE TABLE IF NOT EXISTS anomalies (
                  id TEXT PRIMARY KEY, task_id TEXT NOT NULL, sku TEXT NOT NULL,
                  anomaly_type TEXT NOT NULL, detail TEXT NOT NULL, executable INTEGER NOT NULL,
                  selected INTEGER NOT NULL DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                  UNIQUE(task_id, sku, anomaly_type), FOREIGN KEY(task_id) REFERENCES tasks(id)
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
            for name, definition in {"platform": "TEXT NOT NULL DEFAULT 'amazon'", "approval_status": "TEXT NOT NULL DEFAULT 'pending'", "approved_by": "TEXT", "approved_at": "TEXT", "execution_started_at": "TEXT", "completed_at": "TEXT", "selected_anomaly_id": "TEXT"}.items():
                if name not in columns: db.execute(f"ALTER TABLE tasks ADD COLUMN {name} {definition}")
    def create_task(self, task_id: str, key: str, fail_once: bool, platform: str = "amazon") -> tuple[dict[str, Any], bool]:
        try:
            with self.connect() as db: db.execute("INSERT INTO tasks(id,idempotency_key,status,fail_once,platform) VALUES(?,?,?,?,?)", (task_id, key, "pending", int(fail_once), platform))
            return self.get_task(task_id), True
        except sqlite3.IntegrityError:
            with self.connect() as db: row = db.execute("SELECT id FROM tasks WHERE idempotency_key=?", (key,)).fetchone()
            return self.get_task(row["id"]), False
    def get_task(self, task_id: str) -> dict[str, Any]:
        with self.connect() as db:
            task = db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task: raise KeyError(task_id)
            data = dict(task)
            data["steps"] = [dict(row) for row in db.execute("SELECT name,status,detail,artifact_path FROM steps WHERE task_id=? ORDER BY id", (task_id,))]
            data["anomalies"] = self.list_anomalies(task_id, db)
            data["attempt_history"] = [dict(row) for row in db.execute("SELECT attempt,status,error,created_at FROM attempt_history WHERE task_id=? ORDER BY attempt", (task_id,))]
            data["report"] = json.loads(data.pop("report_json")) if data.get("report_json") else None
            data["retry_count"] = max(0, data["attempt"] - 1)
            data["duration_ms"] = self._duration_ms(data)
            return data
    def list_tasks(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = [dict(row) for row in db.execute("SELECT id,idempotency_key,platform,status,attempt,error,approval_status,created_at,execution_started_at,completed_at FROM tasks ORDER BY created_at DESC")]
            for row in rows:
                row["retry_count"] = max(0, row["attempt"] - 1); row["duration_ms"] = self._duration_ms(row)
            return rows
    def metrics(self) -> dict[str, int | float]:
        tasks = self.list_tasks()
        closed = [task for task in tasks if task["status"] in {"succeeded", "failed"}]
        succeeded = [task for task in closed if task["status"] == "succeeded"]
        durations = [task["duration_ms"] for task in closed if task["duration_ms"] is not None]
        return {
            "total_tasks": len(tasks),
            "success_rate": round(len(succeeded) / len(closed) * 100, 1) if closed else 0.0,
            "failed_tasks": len(closed) - len(succeeded),
            "recovered_tasks": sum(task["retry_count"] > 0 for task in succeeded),
            "avg_handling_ms": int(sum(durations) / len(durations)) if durations else 0,
        }
    def update_task(self, task_id: str, status: str, error: str | None = None, report: dict[str, Any] | None = None, increment: bool = False, execution_started: bool = False, completed: bool = False) -> None:
        with self.connect() as db:
            now = datetime.now(timezone.utc).isoformat()
            db.execute("UPDATE tasks SET status=?, error=?, report_json=COALESCE(?,report_json), attempt=attempt+?, execution_started_at=CASE WHEN ? THEN ? ELSE execution_started_at END, completed_at=CASE WHEN ? THEN ? ELSE completed_at END, updated_at=? WHERE id=?", (status, error, json.dumps(report) if report else None, int(increment), int(execution_started), now, int(completed), now, now, task_id))
    def approve_and_select(self, task_id: str, approved_by: str, anomaly_id: str | None = None, enqueue: bool = False) -> dict[str, Any]:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            task = db.execute("SELECT status,approval_status FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task:
                raise KeyError(task_id)
            if task["status"] != "pending_approval" or task["approval_status"] != "pending":
                raise ValueError("Task is already approved or cannot be approved")
            rows = self.list_anomalies(task_id, db)
            selected = next((row for row in rows if row["id"] == anomaly_id), None) if anomaly_id else next((row for row in rows if row["executable"]), None)
            if selected is None:
                raise ValueError("Selected anomaly was not found" if anomaly_id else "No executable listing anomaly is available")
            if not selected["executable"]:
                raise ValueError("Only listing anomalies can be executed by RPA")
            now = datetime.now(timezone.utc).isoformat()
            result = db.execute(
                "UPDATE tasks SET approval_status='approved', approved_by=?, approved_at=?, selected_anomaly_id=?, updated_at=? WHERE id=? AND status='pending_approval' AND approval_status='pending'",
                (approved_by, now, selected["id"], now, task_id),
            )
            if result.rowcount != 1:
                raise ValueError("Task is already approved or cannot be approved")
            db.execute("UPDATE anomalies SET selected=CASE WHEN id=? THEN 1 ELSE 0 END WHERE task_id=?", (selected["id"], task_id))
            selected["selected"] = True
            if enqueue:
                report = json.loads(db.execute("SELECT report_json FROM tasks WHERE id=?", (task_id,)).fetchone()[0])
                report["selected_anomaly"] = selected
                report["review"] = {"status": "approved_queued", "approved_by": approved_by, "approval_required": True}
                db.execute("UPDATE tasks SET status='queued',report_json=? WHERE id=?", (json.dumps(report), task_id))
                db.execute("INSERT INTO rpa_jobs(task_id) VALUES(?)", (task_id,))
                db.execute("UPDATE steps SET status='succeeded',detail=? WHERE task_id=? AND name='human_review'", (f"Approved by {approved_by}; queued for ShadowBot", task_id))
            return selected
    def record_attempt(self, task_id: str, attempt: int, status: str, error: str | None = None) -> None:
        with self.connect() as db: db.execute("INSERT INTO attempt_history(task_id,attempt,status,error) VALUES(?,?,?,?)", (task_id, attempt, status, error))
    def upsert_step(self, task_id: str, name: str, status: str, detail: str = "", artifact_path: str | None = None) -> None:
        with self.connect() as db: db.execute("INSERT INTO steps(task_id,name,status,detail,artifact_path) VALUES(?,?,?,?,?) ON CONFLICT(task_id,name) DO UPDATE SET status=excluded.status,detail=excluded.detail,artifact_path=excluded.artifact_path", (task_id,name,status,detail,artifact_path))
    def step_succeeded(self, task_id: str, name: str) -> bool:
        with self.connect() as db:
            row = db.execute("SELECT status FROM steps WHERE task_id=? AND name=?", (task_id,name)).fetchone()
            return bool(row and row["status"] == "succeeded")
    def save_analysis(self, task_id: str, report: dict[str, Any]) -> None:
        with self.connect() as db: db.execute("INSERT INTO analysis_results(task_id,payload) VALUES(?,?) ON CONFLICT(task_id) DO UPDATE SET payload=excluded.payload", (task_id,json.dumps(report)))
    def get_analysis(self, task_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM analysis_results WHERE task_id=?", (task_id,)).fetchone()
            if not row: raise KeyError(task_id)
            return json.loads(row["payload"])
    def save_products(self, products: list[dict[str, Any]]) -> None:
        with self.connect() as db: db.executemany("INSERT INTO products(sku,payload) VALUES(?,?) ON CONFLICT(sku) DO UPDATE SET payload=excluded.payload", [(product["sku"], json.dumps(product)) for product in products])
    def product_count(self) -> int:
        with self.connect() as db: return int(db.execute("SELECT COUNT(*) FROM products").fetchone()[0])
    def save_anomalies(self, task_id: str, anomalies: list[dict[str, str | bool]]) -> None:
        with self.connect() as db:
            db.executemany(
                "INSERT OR IGNORE INTO anomalies(id,task_id,sku,anomaly_type,detail,executable) VALUES(?,?,?,?,?,?)",
                [(f"{task_id}:{row['sku']}:{row['anomaly_type']}", task_id, row["sku"], row["anomaly_type"], row["detail"], int(bool(row["executable"]))) for row in anomalies],
            )
    def list_anomalies(self, task_id: str, db: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        if db is None:
            with self.connect() as connection: return self.list_anomalies(task_id, connection)
        return [dict(row) | {"executable": bool(row["executable"]), "selected": bool(row["selected"])} for row in db.execute("SELECT id,sku,anomaly_type,detail,executable,selected,created_at FROM anomalies WHERE task_id=? ORDER BY CASE anomaly_type WHEN 'listing' THEN 0 WHEN 'inventory' THEN 1 ELSE 2 END, sku", (task_id,))]
    def selected_anomaly(self, task_id: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute("SELECT id,sku,anomaly_type,detail,executable,selected,created_at FROM anomalies WHERE task_id=? AND selected=1", (task_id,)).fetchone()
            if not row: raise ValueError("No anomaly has been selected for RPA")
            result = dict(row); result["executable"] = bool(result["executable"]); result["selected"] = bool(result["selected"])
            return result
    @staticmethod
    def _duration_ms(data: dict[str, Any]) -> int | None:
        started, ended = data.get("execution_started_at"), data.get("completed_at")
        if not started: return None
        try:
            start = datetime.fromisoformat(started.replace("Z", "+00:00"))
            finish = datetime.fromisoformat(ended.replace("Z", "+00:00")) if ended else datetime.now(timezone.utc)
            return max(0, int((finish - start).total_seconds() * 1000))
        except (TypeError, ValueError): return None
