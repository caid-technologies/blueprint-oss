import json
import os
import sqlite3
import threading
from contextlib import closing
from datetime import datetime
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()

DEFAULT_JOB_DB_PATH = "./blueprint_jobs.db"


def _utc_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _job_db_path() -> str:
    return os.getenv("JOB_METADATA_DB_PATH", DEFAULT_JOB_DB_PATH)


def _json_default(value: Any) -> str:
    return str(value)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, default=_json_default, separators=(",", ":"))


def _json_loads(value: Optional[str]) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _redact_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    redacted = dict(payload or {})
    if redacted.get("image_data"):
        redacted["image_data"] = "<redacted>"
        redacted["image_data_present"] = True
    return redacted


def summarize_result(result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not result:
        return None

    project_ir = result.get("project_ir") if isinstance(result, dict) else None
    if not isinstance(project_ir, dict):
        return {"result_keys": sorted(result.keys()) if isinstance(result, dict) else []}

    overview = project_ir.get("overview") or {}
    metadata = project_ir.get("assembly_metadata") or {}
    validation = project_ir.get("validation") or {}

    return {
        "project_id": metadata.get("project_id"),
        "title": overview.get("title"),
        "category": overview.get("category"),
        "estimated_cost": overview.get("estimated_cost"),
        "is_valid": project_ir.get("is_valid"),
        "component_count": len(project_ir.get("components") or []),
        "net_count": len(project_ir.get("nets") or []),
        "critical_issue_count": len(validation.get("critical") or []),
        "warning_issue_count": len(validation.get("warning") or []),
        "llm_provider": metadata.get("llm_provider"),
        "model_name": metadata.get("model_name"),
        "has_product_image": bool(metadata.get("product_image_data") or metadata.get("product_image_url")),
        "product_image_provider": metadata.get("product_image_provider") or metadata.get("image_output_provider"),
        "product_image_model": metadata.get("product_image_model") or metadata.get("image_output_model"),
        "pipeline": metadata.get("pipeline"),
    }


class JobMetadataStore:
    """Small SQLite store for durable A2A job metadata."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or _job_db_path()
        self._lock = threading.Lock()
        self._initialized = False

    def init_db(self) -> None:
        if self._initialized:
            return
        directory = os.path.dirname(os.path.abspath(self.db_path))
        if directory:
            os.makedirs(directory, exist_ok=True)

        with closing(self._connect()) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS a2a_jobs (
                    job_id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL,
                    correlation_id TEXT,
                    action TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    status TEXT NOT NULL,
                    server_owned INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    payload_json TEXT,
                    result_summary_json TEXT,
                    error TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_a2a_jobs_sender ON a2a_jobs(sender)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_a2a_jobs_status ON a2a_jobs(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_a2a_jobs_created_at ON a2a_jobs(created_at)")
            conn.commit()
        self._initialized = True

    def create_job(
        self,
        *,
        job_id: str,
        message_id: str,
        correlation_id: Optional[str],
        action: str,
        sender: str,
        recipient: str,
        payload: Dict[str, Any],
        server_owned: bool,
        status: str = "queued",
    ) -> Dict[str, Any]:
        self.init_db()
        now = _utc_now()
        with self._locked_connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO a2a_jobs (
                    job_id, message_id, correlation_id, action, sender, recipient, status,
                    server_owned, created_at, updated_at, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    message_id,
                    correlation_id,
                    action,
                    sender,
                    recipient,
                    status,
                    1 if server_owned else 0,
                    now,
                    now,
                    _json_dumps(_redact_payload(payload)),
                ),
            )
        return self.get_job(job_id) or {}

    def mark_running(self, job_id: str) -> None:
        self.init_db()
        now = _utc_now()
        with self._locked_connection() as conn:
            conn.execute(
                """
                UPDATE a2a_jobs
                SET status = ?, started_at = COALESCE(started_at, ?), updated_at = ?
                WHERE job_id = ?
                """,
                ("running", now, now, job_id),
            )

    def mark_routed(self, job_id: str) -> None:
        self._update_status(job_id, "routed")

    def mark_succeeded(self, job_id: str, result: Optional[Dict[str, Any]]) -> None:
        self.init_db()
        now = _utc_now()
        with self._locked_connection() as conn:
            conn.execute(
                """
                UPDATE a2a_jobs
                SET status = ?, completed_at = ?, updated_at = ?, result_summary_json = ?, error = NULL
                WHERE job_id = ?
                """,
                ("succeeded", now, now, _json_dumps(summarize_result(result)), job_id),
            )

    def mark_failed(self, job_id: str, error: str) -> None:
        self.init_db()
        now = _utc_now()
        with self._locked_connection() as conn:
            conn.execute(
                """
                UPDATE a2a_jobs
                SET status = ?, completed_at = ?, updated_at = ?, error = ?
                WHERE job_id = ?
                """,
                ("failed", now, now, error, job_id),
            )

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        self.init_db()
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM a2a_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def list_jobs(
        self,
        *,
        sender: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        self.init_db()
        clauses = []
        params: List[Any] = []
        if sender:
            clauses.append("sender = ?")
            params.append(sender)
        if status:
            clauses.append("status = ?")
            params.append(status)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(limit, 200)))

        with closing(self._connect()) as conn:
            rows = conn.execute(
                f"SELECT * FROM a2a_jobs {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def _update_status(self, job_id: str, status: str) -> None:
        self.init_db()
        now = _utc_now()
        with self._locked_connection() as conn:
            conn.execute(
                "UPDATE a2a_jobs SET status = ?, updated_at = ? WHERE job_id = ?",
                (status, now, job_id),
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _locked_connection(self) -> sqlite3.Connection:
        return _LockedConnection(self._lock, self._connect())

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        result["server_owned"] = bool(result["server_owned"])
        result["payload"] = _json_loads(result.pop("payload_json", None)) or {}
        result["result_summary"] = _json_loads(result.pop("result_summary_json", None))
        return result


class _LockedConnection:
    def __init__(self, lock: threading.Lock, conn: sqlite3.Connection) -> None:
        self.lock = lock
        self.conn = conn

    def __enter__(self) -> sqlite3.Connection:
        self.lock.acquire()
        return self.conn.__enter__()

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            self.conn.__exit__(exc_type, exc, tb)
            self.conn.close()
        finally:
            self.lock.release()


JOB_STORE = JobMetadataStore()
