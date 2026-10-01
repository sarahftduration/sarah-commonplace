from __future__ import annotations

import threading
import time

import pytest

from commonplace import db
from commonplace.errors import DomainError
from commonplace.maintenance import migrate_database
from commonplace.service import Service


def test_initialization_rolls_back_on_schema_failure(tmp_path, monkeypatch):
    path = tmp_path / "broken.db"
    monkeypatch.setattr(db, "DDL", db.DDL + "\nCREATE TABLE broken(")
    with pytest.raises(db.sqlite3.Error):
        db.initialize(path)
    assert not path.exists()


def test_unsupported_sqlite_fails_before_database_creation(tmp_path, monkeypatch):
    monkeypatch.setattr(db.sqlite3, "sqlite_version_info", (3, 50, 0))
    with pytest.raises(DomainError) as error:
        db.initialize(tmp_path / "new.db")
    assert error.value.code == "unsupported_runtime"
    assert not (tmp_path / "new.db").exists()


def test_live_owner_excludes_second_service_and_offline_maintenance(tmp_path):
    path = tmp_path / "notebook.db"
    db.initialize(path)
    with Service(path):
        with pytest.raises(DomainError) as service_error:
            Service(path)
        assert service_error.value.code == "conflict"
        with pytest.raises(DomainError) as maintenance_error:
            migrate_database(path)
        assert maintenance_error.value.code == "conflict"
    assert migrate_database(path)["changed"] is False
    with Service(path) as restarted:
        assert restarted.ready


def test_startup_rejects_missing_schema_table(tmp_path):
    path = tmp_path / "notebook.db"
    db.initialize(path)
    connection = db.connect(path)
    connection.execute("DROP TABLE search_tokens")
    connection.close()
    with pytest.raises(DomainError) as error:
        Service(path)
    assert error.value.code == "schema_mismatch"


def test_shutdown_marks_unready_and_drains_inflight_call(tmp_path, monkeypatch):
    path = tmp_path / "notebook.db"
    db.initialize(path)
    service = Service(path)
    entered = threading.Event()
    release = threading.Event()

    def slow_call(*_args, **_kwargs):
        entered.set()
        assert release.wait(2)
        return {"ok": True}

    monkeypatch.setattr(service, "_call_impl", slow_call)
    caller = threading.Thread(target=lambda: service.call("context"))
    caller.start()
    assert entered.wait(2)
    closer = threading.Thread(target=service.close)
    closer.start()
    deadline = time.monotonic() + 2
    while service.ready and time.monotonic() < deadline:
        time.sleep(0.001)
    assert not service.ready
    assert service.call("context")["error"]["code"] == "service_unavailable"
    assert closer.is_alive()
    release.set()
    caller.join(2)
    closer.join(2)
    assert not caller.is_alive() and not closer.is_alive()
    with Service(path) as restarted:
        assert restarted.ready
