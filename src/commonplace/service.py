"""Authoritative service dispatch and operation-ID replay."""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from .core import Context, Core
from .db import check_runtime, connect, instance_lock, metadata, transaction, verify_database
from .errors import DomainError, fail
from .models import (
    DATABASE_WIDE,
    OPERATIONS,
    READ_OPERATIONS,
    TERMINAL_RETRIES,
    canonical,
    required_text,
    validate_params,
    validate_uuid,
)

LOG = logging.getLogger(__name__)


def _canonical_params(operation: str, params: dict) -> dict:
    normalized = dict(params)
    if operation == "complete_request":
        for key in ("evidence_ids", "resulting_claim_ids"):
            value = normalized.get(key)
            if isinstance(value, list) and all(
                isinstance(x, int) and not isinstance(x, bool) for x in value
            ):
                normalized[key] = sorted(set(value))
    return normalized


class Service:
    """One process owns one live notebook and all domain transactions."""

    def __init__(
        self,
        database: str | Path,
        *,
        backup_dir: str | Path | None = None,
        clock: Callable[[], int] | None = None,
        max_connections: int = 16,
    ) -> None:
        self.path = Path(database).resolve()
        self.backup_dir = Path(backup_dir).resolve() if backup_dir else None
        self.clock = clock or (lambda: int(time.time() * 1000))
        self._connections = threading.BoundedSemaphore(max_connections)
        self._backup_lock = threading.Lock()
        self._state = threading.Condition()
        self._active_calls = 0
        self._lock = instance_lock(self.path)
        self._closed = False
        self._ready = False
        self._lock.__enter__()
        try:
            check_runtime()
            with closing(connect(self.path)) as connection:
                verify_database(connection)
                self.info = metadata(connection)
            if self.backup_dir:
                self.backup_dir.mkdir(parents=True, exist_ok=True)
            self._ready = True
        except BaseException:
            self._lock.__exit__(None, None, None)
            raise

    @property
    def ready(self) -> bool:
        return self._ready and not self._closed

    def close(self, grace_seconds: float = 30.0) -> None:
        if not self._closed:
            deadline = time.monotonic() + grace_seconds
            with self._state:
                self._ready = False
                while self._active_calls:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        fail("service_unavailable", "In-flight operations did not drain")
                    self._state.wait(timeout=remaining)
                self._closed = True
            self._lock.__exit__(None, None, None)

    def __enter__(self) -> Service:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _resolve(
        self, connection: sqlite3.Connection, operation: str, supplied: Any, params: dict, now: int
    ) -> Context:
        if not isinstance(supplied, dict):
            fail("validation_error", "context must be an object")
        extra = set(supplied) - {"notebook_id", "generation", "project", "agent_id", "session_id"}
        if extra:
            fail("validation_error", "Unknown context field", fields=sorted(extra))
        notebook_id = supplied.get("notebook_id")
        generation = supplied.get("generation")
        if operation != "context" and (notebook_id is None or generation is None):
            fail("validation_error", "notebook_id and generation are required")
        if notebook_id is not None:
            notebook_id = validate_uuid(notebook_id, "notebook_id")
            if notebook_id != self.info["notebook_id"]:
                fail("notebook_mismatch", "Request targets a different notebook")
        if generation is not None:
            generation = validate_uuid(generation, "generation")
            if generation != self.info["generation"]:
                fail("generation_mismatch", "Notebook restore generation changed")

        agent_id = supplied.get("agent_id")
        session_id = supplied.get("session_id")
        project = supplied.get("project")
        if operation == "start_session":
            agent_id = required_text(agent_id, "agent_id")
            session_id = validate_uuid(params.get("session_id"), "session_id")
            if supplied.get("session_id") is not None and supplied["session_id"] != session_id:
                fail("validation_error", "Context session does not match proposed session")
        elif operation != "context":
            agent_id = required_text(agent_id, "agent_id")
            session_id = validate_uuid(session_id, "session_id")
        else:
            if agent_id is not None:
                agent_id = required_text(agent_id, "agent_id")
            if session_id is not None:
                session_id = validate_uuid(session_id, "session_id")
                if agent_id is None:
                    fail("validation_error", "agent_id is required with session_id")
            if (
                agent_id is not None
                and connection.execute("SELECT 1 FROM agents WHERE id=?", (agent_id,)).fetchone()
                is None
            ):
                fail("not_found", "Agent was not found")

        if operation != "context" and operation not in DATABASE_WIDE and project is None:
            fail("validation_error", "project is required")
        if operation != "context" and operation in DATABASE_WIDE and project is not None:
            fail("validation_error", "project must be omitted for this operation")
        project_id = None
        if project is not None:
            project = required_text(project, "project")
            row = connection.execute("SELECT id FROM projects WHERE slug=?", (project,)).fetchone()
            if row is None:
                fail("not_found", "Project was not found")
            project_id = row[0]

        if session_id is not None and operation != "start_session":
            row = connection.execute(
                "SELECT agent_id,ended_at FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            if row is None or (agent_id is not None and row["agent_id"] != agent_id):
                fail("session_mismatch", "Session does not match agent")
        elif operation != "context" and operation != "start_session":
            fail("session_mismatch", "Research session is required")

        return Context(
            self.info["notebook_id"],
            self.info["generation"],
            project_id,
            project,
            agent_id,
            session_id,
            now,
        )

    @staticmethod
    def _meta(ctx: Context | None, operation_id: str | None) -> dict:
        return {
            "operation_id": operation_id,
            "notebook_id": ctx.notebook_id if ctx else None,
            "generation": ctx.generation if ctx else None,
            "project": ctx.project if ctx else None,
            "agent_id": ctx.agent_id if ctx else None,
            "session_id": ctx.session_id if ctx else None,
        }

    def call(
        self,
        operation: str,
        context: dict | None = None,
        params: dict | None = None,
        operation_id: str | None = None,
    ) -> dict:
        with self._state:
            if not self.ready:
                error = DomainError(
                    "service_unavailable", "Service is not accepting work", retryable=True
                )
                return {
                    "ok": False,
                    "data": None,
                    "error": error.as_dict(),
                    "meta": self._meta(None, operation_id),
                }
            self._active_calls += 1
        try:
            return self._call_impl(operation, context, params, operation_id)
        finally:
            with self._state:
                self._active_calls -= 1
                self._state.notify_all()

    def _call_impl(
        self,
        operation: str,
        context: dict | None = None,
        params: dict | None = None,
        operation_id: str | None = None,
    ) -> dict:
        ctx = None
        try:
            if not self.ready:
                fail("service_unavailable", "Service is not accepting work")
            if operation not in OPERATIONS:
                fail("not_found", "Unknown operation", operation=operation)
            params = validate_params(operation, params if params is not None else {})
            mutation = operation not in READ_OPERATIONS
            if mutation:
                operation_id = validate_uuid(operation_id, "operation_id")
            elif operation_id is not None:
                fail("validation_error", "Reads do not accept operation_id")
            if operation == "backup_database":
                return self._backup_call(context or {}, params, operation_id)
            if not self._connections.acquire(timeout=5):
                fail("database_busy", "Service connection limit reached")
            try:
                connection = connect(self.path)
                try:
                    with transaction(connection, write=mutation):
                        now = self.clock()  # after the write lock is acquired
                        ctx = self._resolve(connection, operation, context or {}, params, now)
                        input_json = canonical(
                            {
                                "operation": operation,
                                "context": {
                                    "notebook_id": ctx.notebook_id,
                                    "generation": ctx.generation,
                                    "project": ctx.project,
                                    "agent_id": ctx.agent_id,
                                    "session_id": ctx.session_id,
                                },
                                "params": _canonical_params(operation, params),
                            }
                        )
                        if mutation:
                            backup = connection.execute(
                                """SELECT 1 FROM backup_jobs WHERE generation=? AND agent_id=?
                                   AND session_id=? AND operation_id=?""",
                                (ctx.generation, ctx.agent_id, ctx.session_id, operation_id),
                            ).fetchone()
                            if backup:
                                fail(
                                    "idempotency_conflict", "Operation ID is reserved for a backup"
                                )
                            stored = connection.execute(
                                """SELECT input_json,result_json FROM operation_results
                                   WHERE generation=? AND agent_id=? AND session_id=?
                                   AND operation_id=?""",
                                (ctx.generation, ctx.agent_id, ctx.session_id, operation_id),
                            ).fetchone()
                            if stored:
                                if stored["input_json"] != input_json:
                                    fail(
                                        "idempotency_conflict",
                                        "Operation ID was used with different input",
                                    )
                                return json.loads(stored["result_json"])
                        if operation not in {"context", "start_session", *TERMINAL_RETRIES}:
                            closed = connection.execute(
                                "SELECT ended_at FROM sessions WHERE id=?", (ctx.session_id,)
                            ).fetchone()
                            if closed["ended_at"] is not None:
                                fail("session_closed", "Research session is closed")
                        if (
                            mutation
                            and ctx.project_id is not None
                            and operation not in TERMINAL_RETRIES
                        ):
                            archived = connection.execute(
                                "SELECT archived_at FROM projects WHERE id=?", (ctx.project_id,)
                            ).fetchone()[0]
                            if archived is not None:
                                fail("project_archived", "Project is archived")
                        core = Core(connection, ctx)
                        data = core.run(operation, params)
                        ctx = core.ctx
                        envelope = {
                            "ok": True,
                            "data": data,
                            "error": None,
                            "meta": self._meta(ctx, operation_id),
                        }
                        if mutation:
                            connection.execute(
                                """INSERT INTO operation_results(generation,agent_id,session_id,
                                   operation_id,operation,input_json,result_json,created_at)
                                   VALUES(?,?,?,?,?,?,?,?)""",
                                (
                                    ctx.generation,
                                    ctx.agent_id,
                                    ctx.session_id,
                                    operation_id,
                                    operation,
                                    input_json,
                                    canonical(envelope),
                                    now,
                                ),
                            )
                            connection.execute(
                                "UPDATE agents SET last_seen_at=? WHERE id=?", (now, ctx.agent_id)
                            )
                        return envelope
                finally:
                    connection.close()
            finally:
                self._connections.release()
        except DomainError as exc:
            return {
                "ok": False,
                "data": None,
                "error": exc.as_dict(),
                "meta": self._meta(ctx, operation_id),
            }
        except (ValueError, TypeError) as exc:
            error = DomainError("validation_error", str(exc))
            return {
                "ok": False,
                "data": None,
                "error": error.as_dict(),
                "meta": self._meta(ctx, operation_id),
            }
        except sqlite3.IntegrityError:
            error = DomainError("conflict", "Database integrity constraint failed")
            return {
                "ok": False,
                "data": None,
                "error": error.as_dict(),
                "meta": self._meta(ctx, operation_id),
            }
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                error = DomainError("database_busy", "Database is busy", retryable=True)
            else:
                LOG.exception("Database operation failed: %s", operation)
                error = DomainError("internal_error", "Internal service error")
            return {
                "ok": False,
                "data": None,
                "error": error.as_dict(),
                "meta": self._meta(ctx, operation_id),
            }
        except Exception:
            LOG.exception("Service operation failed: %s", operation)
            error = DomainError("internal_error", "Internal service error")
            return {
                "ok": False,
                "data": None,
                "error": error.as_dict(),
                "meta": self._meta(ctx, operation_id),
            }

    def _backup_call(self, context: dict, params: dict, operation_id: str) -> dict:
        if self.backup_dir is None:
            fail("service_unavailable", "Backup directory is not configured")
        name = params.get("name")
        if name is not None:
            if not isinstance(name, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name
            ):
                fail("validation_error", "Backup name must be a simple basename")
            if name in {".", ".."} or ".." in name:
                fail("validation_error", "Backup name cannot contain traversal components")
        if not self._connections.acquire(timeout=5):
            fail("database_busy", "Service connection limit reached")
        try:
            connection = connect(self.path)
            try:
                with transaction(connection, write=True):
                    now = self.clock()
                    ctx = self._resolve(connection, "backup_database", context, params, now)
                    input_json = canonical(
                        {
                            "operation": "backup_database",
                            "context": {
                                "notebook_id": ctx.notebook_id,
                                "generation": ctx.generation,
                                "project": None,
                                "agent_id": ctx.agent_id,
                                "session_id": ctx.session_id,
                            },
                            "params": params,
                        }
                    )
                    if connection.execute(
                        """SELECT 1 FROM operation_results WHERE generation=? AND agent_id=?
                           AND session_id=? AND operation_id=?""",
                        (ctx.generation, ctx.agent_id, ctx.session_id, operation_id),
                    ).fetchone():
                        fail("idempotency_conflict", "Operation ID was used for another mutation")
                    job = connection.execute(
                        """SELECT * FROM backup_jobs WHERE generation=? AND agent_id=?
                           AND session_id=? AND operation_id=?""",
                        (ctx.generation, ctx.agent_id, ctx.session_id, operation_id),
                    ).fetchone()
                    if job and job["input_json"] != input_json:
                        fail("idempotency_conflict", "Operation ID was used with different input")
                    if job is None:
                        ended = connection.execute(
                            "SELECT ended_at FROM sessions WHERE id=?", (ctx.session_id,)
                        ).fetchone()[0]
                        if ended is not None:
                            fail("session_closed", "Research session is closed")
                        job_id = str(uuid.uuid4())
                        chosen = (
                            name
                            or time.strftime("commonplace-%Y%m%d-%H%M%S", time.gmtime(now / 1000))
                            + f"-{job_id[:8]}.db"
                        )
                        final = self.backup_dir / chosen
                        if final.exists() or final.is_symlink():
                            fail("conflict", "Backup name already exists")
                        connection.execute(
                            """INSERT INTO backup_jobs(id,generation,agent_id,session_id,
                               operation_id,input_json,name,state,created_at)
                               VALUES(?,?,?,?,?,?,?,'reserved',?)""",
                            (
                                job_id,
                                ctx.generation,
                                ctx.agent_id,
                                ctx.session_id,
                                operation_id,
                                input_json,
                                chosen,
                                now,
                            ),
                        )
                    else:
                        job_id = job["id"]
                        chosen = job["name"]
                        if job["state"] == "complete":
                            receipt = json.loads(job["receipt_json"])
                            return {
                                "ok": True,
                                "data": receipt,
                                "error": None,
                                "meta": self._meta(ctx, operation_id),
                            }
            finally:
                connection.close()
            if not self._backup_lock.acquire(blocking=False):
                fail(
                    "service_unavailable",
                    "Backup is in progress",
                    backup_id=job_id,
                    state="reserved",
                )
            try:
                final = self.backup_dir / chosen
                partial = self.backup_dir / f".{job_id}.partial"
                if final.exists() or final.is_symlink():
                    self._verify_backup_file(final, ctx)
                else:
                    partial.unlink(missing_ok=True)
                    fd = os.open(partial, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
                    os.close(fd)
                    try:
                        source = connect(self.path)
                        dest = sqlite3.connect(partial)
                        dest.row_factory = sqlite3.Row
                        dest.execute("PRAGMA foreign_keys=ON")
                        try:
                            source.backup(dest)
                        finally:
                            source.close()
                            dest.close()
                        self._verify_backup_file(partial, ctx)
                        with open(partial, "rb") as file:
                            os.fsync(file.fileno())
                        os.link(partial, final, follow_symlinks=False)
                        directory_fd = os.open(self.backup_dir, os.O_RDONLY)
                        try:
                            os.fsync(directory_fd)
                        finally:
                            os.close(directory_fd)
                    finally:
                        partial.unlink(missing_ok=True)
                receipt = {
                    "backup_id": job_id,
                    "name": chosen,
                    "source_notebook_id": ctx.notebook_id,
                    "source_generation": ctx.generation,
                    "created_at": job["created_at"] if job else now,
                    "completed_at": self.clock(),
                    "state": "complete",
                }
                connection = connect(self.path)
                try:
                    with transaction(connection, write=True):
                        existing = connection.execute(
                            "SELECT state,receipt_json FROM backup_jobs WHERE id=?", (job_id,)
                        ).fetchone()
                        if existing["state"] == "complete":
                            receipt = json.loads(existing["receipt_json"])
                        else:
                            connection.execute(
                                """UPDATE backup_jobs SET state='complete',completed_at=?,
                                   receipt_json=?
                                   WHERE id=?""",
                                (receipt["completed_at"], canonical(receipt), job_id),
                            )
                            Core(
                                connection,
                                Context(
                                    ctx.notebook_id,
                                    ctx.generation,
                                    None,
                                    None,
                                    ctx.agent_id,
                                    ctx.session_id,
                                    receipt["completed_at"],
                                ),
                            ).event(
                                "database.backup_completed",
                                "backup",
                                job_id,
                                "Database backup completed",
                                {"name": chosen},
                                project_id=None,
                            )
                    return {
                        "ok": True,
                        "data": receipt,
                        "error": None,
                        "meta": self._meta(ctx, operation_id),
                    }
                finally:
                    connection.close()
            finally:
                self._backup_lock.release()
        finally:
            self._connections.release()

    @staticmethod
    def _verify_backup_file(path: Path, ctx: Context) -> None:
        if path.is_symlink() or not path.is_file():
            fail("conflict", "Backup name is not a regular file")
        connection = sqlite3.connect(
            f"file:{path.as_posix()}?mode=ro", uri=True, isolation_level=None
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            verify_database(connection)
            info = metadata(connection)
            if info["notebook_id"] != ctx.notebook_id or info["generation"] != ctx.generation:
                fail("conflict", "Backup file belongs to a different notebook generation")
        finally:
            connection.close()
