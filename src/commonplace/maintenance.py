"""Offline database commands. These never run through a research client."""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

from .db import (
    check_runtime,
    connect,
    initialize,
    instance_lock,
    metadata,
    transaction,
    verify_database,
)
from .errors import fail


def init_database(path: str | Path) -> dict:
    return initialize(path)


def migrate_database(path: str | Path) -> dict:
    """v0.1 has no predecessor schema; validate and preserve its identity."""
    check_runtime()
    with instance_lock(path):
        connection = connect(path)
        try:
            verify_database(connection)
            info = metadata(connection)
            return {**info, "sqlite_version": sqlite3.sqlite_version, "changed": False}
        finally:
            connection.close()


def restore_database(destination: str | Path, source: str | Path) -> dict:
    check_runtime()
    target = Path(destination).resolve()
    backup = Path(source).resolve()
    if target == backup:
        fail("validation_error", "Restore destination must differ from backup")
    if not backup.is_file():
        fail("not_found", "Backup file does not exist")
    target.parent.mkdir(parents=True, exist_ok=True)
    with instance_lock(target):
        if target.exists():
            fail("conflict", "Restore destination already exists")
        source_connection = sqlite3.connect(
            f"file:{backup.as_posix()}?mode=ro", uri=True, isolation_level=None
        )
        source_connection.row_factory = sqlite3.Row
        source_connection.execute("PRAGMA foreign_keys=ON")
        try:
            verify_database(source_connection)
            original = metadata(source_connection)
            destination_connection = connect(target, create=True)
            os.chmod(target, 0o600)
            try:
                source_connection.backup(destination_connection)
                verify_database(destination_connection)
                generation = str(uuid.uuid4())
                now = int(time.time() * 1000)
                with transaction(destination_connection, write=True):
                    destination_connection.execute(
                        "UPDATE metadata SET value=? WHERE key='generation'", (generation,)
                    )
                    destination_connection.execute(
                        "INSERT INTO maintenance_log(kind,timestamp,details_json) VALUES(?,?,?)",
                        (
                            "restore",
                            now,
                            json.dumps({"previous_generation": original["generation"]}),
                        ),
                    )
                verify_database(destination_connection)
                return {
                    "notebook_id": original["notebook_id"],
                    "generation": generation,
                    "previous_generation": original["generation"],
                    "schema_version": int(original["schema_version"]),
                    "sqlite_version": sqlite3.sqlite_version,
                }
            except BaseException:
                destination_connection.close()
                target.unlink(missing_ok=True)
                Path(str(target) + "-wal").unlink(missing_ok=True)
                Path(str(target) + "-shm").unlink(missing_ok=True)
                raise
            finally:
                destination_connection.close()
        finally:
            source_connection.close()
