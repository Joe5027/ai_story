"""SQLite 任务日志，负责幂等、状态机和重启恢复。"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .errors import AgentError
from .models import TERMINAL_STATUSES


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _json_load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


class JobJournal:
    """为单实例运行时提供 WAL 模式的持久化任务日志。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path).resolve()

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    request_hash TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    input_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    resource_group TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('queued','running','succeeded','failed','cancelled')
                    ),
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    runtime_generation INTEGER NOT NULL,
                    result_json TEXT,
                    error_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_status_created
                    ON jobs(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_jobs_resource_status
                    ON jobs(resource_group, status);

                CREATE TABLE IF NOT EXISTS artifacts (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    relative_path TEXT NOT NULL UNIQUE,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_artifacts_job
                    ON artifacts(job_id, created_at);

                CREATE TABLE IF NOT EXISTS job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS runtime_reloads (
                    id TEXT PRIMARY KEY,
                    generation INTEGER NOT NULL UNIQUE,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL,
                    capabilities_hash TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    completed_at TEXT NOT NULL
                );
                """
            )
            # Agent journal 是节点本地状态，不走 Django migration。旧安装首次启动
            # 时以幂等 ALTER 补列，保留已完成任务和产物的幂等记录。
            artifact_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(artifacts)")
            }
            if "metadata_json" not in artifact_columns:
                connection.execute(
                    "ALTER TABLE artifacts ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}'"
                )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.db_path,
            timeout=10,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _begin(connection: sqlite3.Connection) -> None:
        connection.execute("BEGIN IMMEDIATE")

    @staticmethod
    def _commit(connection: sqlite3.Connection) -> None:
        connection.execute("COMMIT")

    @staticmethod
    def _rollback(connection: sqlite3.Connection) -> None:
        if connection.in_transaction:
            connection.execute("ROLLBACK")

    @staticmethod
    def _record_event(
        connection: sqlite3.Connection,
        job_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO job_events(job_id, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (job_id, event_type, _json_dump(payload or {}), utc_now()),
        )

    @staticmethod
    def _job_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "idempotency_key": row["idempotency_key"],
            "request_hash": row["request_hash"],
            "kind": row["kind"],
            "input": _json_load(row["input_json"], {}),
            "metadata": _json_load(row["metadata_json"], {}),
            "resource_group": row["resource_group"],
            "status": row["status"],
            "cancel_requested": bool(row["cancel_requested"]),
            "runtime_generation": row["runtime_generation"],
            "result": _json_load(row["result_json"], None),
            "error": _json_load(row["error_json"], None),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
        }

    @staticmethod
    def _artifact_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "job_id": row["job_id"],
            "name": row["name"],
            "media_type": row["media_type"],
            "relative_path": row["relative_path"],
            "sha256": row["sha256"],
            "size_bytes": row["size_bytes"],
            "metadata": _json_load(row["metadata_json"], {}),
            "created_at": row["created_at"],
        }

    def healthcheck(self) -> bool:
        try:
            with self._connect() as connection:
                return connection.execute("SELECT 1").fetchone()[0] == 1
        except sqlite3.Error:
            return False

    def create_or_get_job(
        self,
        *,
        idempotency_key: str,
        request_hash: str,
        kind: str,
        input_data: dict[str, Any],
        metadata: dict[str, Any],
        resource_group: str,
        runtime_generation: int,
    ) -> tuple[dict[str, Any], bool]:
        """原子创建任务；同键同哈希复用，不同哈希明确冲突。"""

        with self._connect() as connection:
            try:
                self._begin(connection)
                existing = connection.execute(
                    "SELECT * FROM jobs WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    if existing["request_hash"] != request_hash:
                        raise AgentError(
                            409,
                            "IDEMPOTENCY_CONFLICT",
                            "Idempotency-Key 已用于不同的请求体",
                            {
                                "idempotency_key": idempotency_key,
                                "existing_request_hash": existing["request_hash"],
                                "request_hash": request_hash,
                            },
                        )
                    self._commit(connection)
                    return self._job_from_row(existing), True

                job_id = str(uuid.uuid4())
                now = utc_now()
                connection.execute(
                    """
                    INSERT INTO jobs(
                        id, idempotency_key, request_hash, kind, input_json,
                        metadata_json, resource_group, status, runtime_generation,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                    """,
                    (
                        job_id,
                        idempotency_key,
                        request_hash,
                        kind,
                        _json_dump(input_data),
                        _json_dump(metadata),
                        resource_group,
                        runtime_generation,
                        now,
                        now,
                    ),
                )
                self._record_event(connection, job_id, "queued")
                row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
                self._commit(connection)
                return self._job_from_row(row), False
            except Exception:
                self._rollback(connection)
                raise

    def get_job(self, job_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise AgentError(404, "JOB_NOT_FOUND", "任务不存在", {"job_id": job_id})
        return self._job_from_row(row)

    def list_jobs(
        self,
        *,
        status: str | None = None,
        kind: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[Any] = []
        if status:
            clauses.append("status = ?")
            values.append(status)
        if kind:
            clauses.append("kind = ?")
            values.append(kind)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(limit)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM jobs {where} ORDER BY created_at DESC LIMIT ?",
                values,
            ).fetchall()
        return [self._job_from_row(row) for row in rows]

    def fetch_queued_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'queued'
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._job_from_row(row) for row in rows]

    def queue_snapshot(self) -> dict[str, Any]:
        """按状态和资源组汇总队列，不加载提示词或任务正文。"""

        with self._connect() as connection:
            status_rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM jobs GROUP BY status"
            ).fetchall()
            resource_rows = connection.execute(
                """
                SELECT resource_group, status, COUNT(*) AS count
                FROM jobs
                WHERE status IN ('queued', 'running')
                GROUP BY resource_group, status
                """
            ).fetchall()
        return {
            "status_counts": {row["status"]: row["count"] for row in status_rows},
            "resource_counts": [dict(row) for row in resource_rows],
        }

    def claim_job(self, job_id: str) -> dict[str, Any] | None:
        """仅将仍处于 queued 的任务原子推进到 running。"""

        with self._connect() as connection:
            try:
                self._begin(connection)
                row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
                if row is None or row["status"] != "queued":
                    self._commit(connection)
                    return None
                now = utc_now()
                if row["cancel_requested"]:
                    connection.execute(
                        """
                        UPDATE jobs SET status='cancelled', updated_at=?, finished_at=?
                        WHERE id=?
                        """,
                        (now, now, job_id),
                    )
                    self._record_event(connection, job_id, "cancelled")
                    self._commit(connection)
                    return None
                connection.execute(
                    """
                    UPDATE jobs
                    SET status='running', started_at=?, updated_at=?
                    WHERE id=? AND status='queued'
                    """,
                    (now, now, job_id),
                )
                self._record_event(connection, job_id, "running")
                claimed = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
                self._commit(connection)
                return self._job_from_row(claimed)
            except Exception:
                self._rollback(connection)
                raise

    def request_cancel(self, job_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            try:
                self._begin(connection)
                row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
                if row is None:
                    raise AgentError(404, "JOB_NOT_FOUND", "任务不存在", {"job_id": job_id})
                if row["status"] in TERMINAL_STATUSES:
                    self._commit(connection)
                    return self._job_from_row(row)

                now = utc_now()
                if row["status"] == "queued":
                    connection.execute(
                        """
                        UPDATE jobs
                        SET status='cancelled', cancel_requested=1,
                            updated_at=?, finished_at=?
                        WHERE id=?
                        """,
                        (now, now, job_id),
                    )
                    self._record_event(connection, job_id, "cancelled", {"phase": "queued"})
                else:
                    connection.execute(
                        "UPDATE jobs SET cancel_requested=1, updated_at=? WHERE id=?",
                        (now, job_id),
                    )
                    self._record_event(connection, job_id, "cancel_requested")
                updated = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
                self._commit(connection)
                return self._job_from_row(updated)
            except Exception:
                self._rollback(connection)
                raise

    def is_cancel_requested(self, job_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT cancel_requested, status FROM jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
        return bool(row and (row["cancel_requested"] or row["status"] == "cancelled"))

    def mark_succeeded(self, job_id: str, result: dict[str, Any]) -> None:
        self._finish(job_id, "succeeded", result=result)

    def mark_failed(self, job_id: str, error: dict[str, Any]) -> None:
        self._finish(job_id, "failed", error=error)

    def mark_cancelled(self, job_id: str) -> None:
        self._finish(job_id, "cancelled")

    def _finish(
        self,
        job_id: str,
        status: str,
        *,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now()
        with self._connect() as connection:
            try:
                self._begin(connection)
                row = connection.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
                if row is None or row["status"] in TERMINAL_STATUSES:
                    self._commit(connection)
                    return
                connection.execute(
                    """
                    UPDATE jobs
                    SET status=?, result_json=?, error_json=?, updated_at=?, finished_at=?
                    WHERE id=?
                    """,
                    (
                        status,
                        _json_dump(result) if result is not None else None,
                        _json_dump(error) if error is not None else None,
                        now,
                        now,
                        job_id,
                    ),
                )
                self._record_event(connection, job_id, status, error or result or {})
                self._commit(connection)
            except Exception:
                self._rollback(connection)
                raise

    def recover_interrupted_jobs(self) -> dict[str, int]:
        """重启时取消已请求取消的任务，并把其余 running 任务重新排队。"""

        with self._connect() as connection:
            try:
                self._begin(connection)
                rows = connection.execute(
                    "SELECT id, cancel_requested FROM jobs WHERE status='running'"
                ).fetchall()
                requeued = 0
                cancelled = 0
                now = utc_now()
                for row in rows:
                    if row["cancel_requested"]:
                        connection.execute(
                            """
                            UPDATE jobs SET status='cancelled', updated_at=?, finished_at=?
                            WHERE id=?
                            """,
                            (now, now, row["id"]),
                        )
                        self._record_event(connection, row["id"], "cancelled", {"recovered": True})
                        cancelled += 1
                    else:
                        connection.execute(
                            """
                            UPDATE jobs
                            SET status='queued', started_at=NULL, updated_at=?
                            WHERE id=?
                            """,
                            (now, row["id"]),
                        )
                        self._record_event(connection, row["id"], "requeued", {"recovered": True})
                        requeued += 1
                self._commit(connection)
                return {"requeued": requeued, "cancelled": cancelled}
            except Exception:
                self._rollback(connection)
                raise

    def add_artifact(
        self,
        *,
        job_id: str,
        name: str,
        media_type: str,
        relative_path: str,
        sha256: str,
        size_bytes: int,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        artifact_id = str(uuid.uuid4())
        created_at = utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN")
            try:
                connection.execute(
                    """
                    INSERT INTO artifacts(
                        id, job_id, name, media_type, relative_path,
                        sha256, size_bytes, metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact_id,
                        job_id,
                        name,
                        media_type,
                        relative_path,
                        sha256,
                        size_bytes,
                        _json_dump(metadata or {}),
                        created_at,
                    ),
                )
                self._record_event(
                    connection,
                    job_id,
                    "artifact_created",
                    {"artifact_id": artifact_id, "sha256": sha256},
                )
                connection.execute("COMMIT")
            except Exception:
                self._rollback(connection)
                raise
        return self.get_artifact(artifact_id)

    def list_artifacts(self, job_id: str) -> list[dict[str, Any]]:
        self.get_job(job_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM artifacts WHERE job_id=? ORDER BY created_at ASC",
                (job_id,),
            ).fetchall()
        return [self._artifact_from_row(row) for row in rows]

    def get_artifact(self, artifact_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM artifacts WHERE id=?",
                (artifact_id,),
            ).fetchone()
        if row is None:
            raise AgentError(
                404,
                "ARTIFACT_NOT_FOUND",
                "产物不存在",
                {"artifact_id": artifact_id},
            )
        return self._artifact_from_row(row)

    def current_runtime_generation(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT MAX(generation) AS generation FROM runtime_reloads"
            ).fetchone()
        return int(row["generation"] or 1)

    def record_runtime_reload(
        self,
        *,
        reason: str,
        capabilities_hash: str,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            try:
                self._begin(connection)
                row = connection.execute(
                    "SELECT MAX(generation) AS generation FROM runtime_reloads"
                ).fetchone()
                generation = int(row["generation"] or 1) + 1
                reload_id = str(uuid.uuid4())
                now = utc_now()
                connection.execute(
                    """
                    INSERT INTO runtime_reloads(
                        id, generation, reason, status, capabilities_hash,
                        requested_at, completed_at
                    ) VALUES (?, ?, ?, 'succeeded', ?, ?, ?)
                    """,
                    (reload_id, generation, reason, capabilities_hash, now, now),
                )
                saved = connection.execute(
                    "SELECT * FROM runtime_reloads WHERE id=?", (reload_id,)
                ).fetchone()
                self._commit(connection)
                return dict(saved)
            except Exception:
                self._rollback(connection)
                raise

    def list_runtime_reloads(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM runtime_reloads
                ORDER BY generation DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
