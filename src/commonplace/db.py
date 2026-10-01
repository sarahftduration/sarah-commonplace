"""Database ownership, runtime checks, and connection setup."""

from __future__ import annotations

import fcntl
import json
import os
import re
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .errors import DomainError, fail
from .schema import DDL, SCHEMA_VERSION

MIN_SQLITE = (3, 51, 3)


def check_runtime() -> None:
    if sqlite3.sqlite_version_info < MIN_SQLITE:
        fail(
            "unsupported_runtime",
            "SQLite 3.51.3 or newer is required",
            loaded_sqlite=sqlite3.sqlite_version,
        )
    with sqlite3.connect(":memory:") as connection:
        try:
            connection.execute("CREATE VIRTUAL TABLE fts_check USING fts5(content)")
        except sqlite3.OperationalError as exc:
            raise DomainError("unsupported_runtime", "SQLite FTS5 is required") from exc


@contextmanager
def instance_lock(path: str | Path) -> Iterator[None]:
    """Lock a stable sidecar pathname; an abandoned pathname is harmless."""
    lock_path = Path(str(Path(path).resolve()) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DomainError("conflict", "Database is owned by another process") from exc
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def connect(path: str | Path, *, create: bool = False) -> sqlite3.Connection:
    resolved = Path(path).resolve()
    if not create and not resolved.is_file():
        fail("not_found", "Database does not exist")
    mode = "rwc" if create else "rw"
    connection = sqlite3.connect(
        f"file:{resolved.as_posix()}?mode={mode}",
        uri=True,
        timeout=5.0,
        isolation_level=None,
        check_same_thread=True,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA busy_timeout=5000")
    connection.execute("PRAGMA synchronous=FULL")
    journal = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    if journal.lower() != "wal":
        connection.close()
        fail("unsupported_runtime", "SQLite WAL mode could not be enabled")
    return connection


def initialize(path: str | Path, *, now_ms: int | None = None) -> dict:
    check_runtime()
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with instance_lock(target):
        if target.exists():
            fail("conflict", "Database destination already exists")
        connection = connect(target, create=True)
        os.chmod(target, 0o600)
        notebook_id = str(uuid.uuid4())
        generation = str(uuid.uuid4())
        now = now_ms if now_ms is not None else int(time.time() * 1000)
        try:
            connection.executescript("BEGIN IMMEDIATE;\n" + DDL)
            connection.executemany(
                "INSERT INTO metadata(key,value) VALUES(?,?)",
                [
                    ("schema_version", str(SCHEMA_VERSION)),
                    ("notebook_id", notebook_id),
                    ("generation", generation),
                ],
            )
            connection.execute(
                "INSERT INTO maintenance_log(kind,timestamp,details_json) VALUES(?,?,?)",
                ("init", now, json.dumps({"schema_version": SCHEMA_VERSION})),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            connection.close()
            target.unlink(missing_ok=True)
            raise
        connection.close()
        return {
            "notebook_id": notebook_id,
            "generation": generation,
            "schema_version": SCHEMA_VERSION,
            "sqlite_version": sqlite3.sqlite_version,
        }


def metadata(connection: sqlite3.Connection) -> dict[str, str]:
    try:
        values = {
            row["key"]: row["value"] for row in connection.execute("SELECT key,value FROM metadata")
        }
    except sqlite3.DatabaseError as exc:
        raise DomainError("schema_mismatch", "Database schema is missing or invalid") from exc
    try:
        version = int(values["schema_version"])
        uuid.UUID(values["notebook_id"])
        uuid.UUID(values["generation"])
    except (KeyError, ValueError) as exc:
        raise DomainError("schema_mismatch", "Database metadata is invalid") from exc
    if version != SCHEMA_VERSION:
        fail(
            "schema_mismatch",
            "Database schema version is incompatible",
            actual=version,
            supported=SCHEMA_VERSION,
        )
    return values


def verify_database(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        fail("schema_mismatch", "Database integrity check failed")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        fail("schema_mismatch", "Database foreign-key check failed")
    required_tables = set(re.findall(r"CREATE (?:VIRTUAL )?TABLE ([A-Za-z_]+)", DDL))
    actual_tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if not required_tables <= actual_tables:
        fail(
            "schema_mismatch",
            "Database is missing required tables",
            tables=sorted(required_tables - actual_tables),
        )
    fts_sql = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='search_fts'"
    ).fetchone()[0]
    if not fts_sql.upper().startswith("CREATE VIRTUAL TABLE"):
        fail("schema_mismatch", "Database search index is incompatible")
    metadata(connection)


@contextmanager
def transaction(
    connection: sqlite3.Connection, *, write: bool = False
) -> Iterator[sqlite3.Connection]:
    try:
        connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield connection
        connection.commit()
    except sqlite3.OperationalError as exc:
        connection.rollback()
        if "locked" in str(exc).lower() or "busy" in str(exc).lower():
            raise DomainError("database_busy", "Database is busy", retryable=True) from exc
        raise
    except BaseException:
        connection.rollback()
        raise
