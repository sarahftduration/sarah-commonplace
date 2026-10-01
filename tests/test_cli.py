import json
import sqlite3

from commonplace import cli


def test_cli_operation_prints_envelope_and_sets_project_context(monkeypatch, capsys):
    captured = {}

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def call(self, operation, **kwargs):
            captured["operation"] = operation
            captured.update(kwargs)
            return {"ok": True, "data": {"id": 17}, "error": None, "meta": {}}

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(cli, "CommonplaceClient", FakeClient)
    monkeypatch.setenv("COMMONPLACE_URL", "http://localhost:8765")
    monkeypatch.setenv("COMMONPLACE_TOKEN_FILE", "/tmp/token")
    monkeypatch.setenv("COMMONPLACE_PROJECT", "combat")
    monkeypatch.setenv("COMMONPLACE_AGENT_ID", "worker-a")
    monkeypatch.setenv("COMMONPLACE_SESSION_ID", "session-1")
    monkeypatch.setenv("COMMONPLACE_NOTEBOOK_ID", "notebook-1")
    monkeypatch.setenv("COMMONPLACE_GENERATION", "generation-1")
    code = cli.main(["claim", "create", "--params", '{"statement":"x"}', "--json"])
    result = json.loads(capsys.readouterr().out)
    assert code == 0
    assert result["data"] == {"id": 17}
    assert captured["operation"] == "create_claim"
    assert captured["context"]["project"] == "combat"
    assert captured["context"]["session_id"] == "session-1"
    assert captured["operation_id"]


def test_cli_start_session_bootstraps_proposed_id(monkeypatch, capsys):
    captured = {}

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def call(self, operation, **kwargs):
            captured.update(operation=operation, **kwargs)
            return {
                "ok": True,
                "data": {"id": kwargs["params"]["session_id"]},
                "error": None,
                "meta": {},
            }

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(cli, "CommonplaceClient", FakeClient)
    monkeypatch.setenv("COMMONPLACE_URL", "http://localhost:8765")
    monkeypatch.setenv("COMMONPLACE_TOKEN_FILE", "/tmp/token")
    monkeypatch.setenv("COMMONPLACE_AGENT_ID", "worker-a")
    monkeypatch.setenv("COMMONPLACE_NOTEBOOK_ID", "book")
    monkeypatch.setenv("COMMONPLACE_GENERATION", "gen")
    monkeypatch.setenv("COMMONPLACE_SESSION_ID", "old-session")
    assert cli.main(["session", "start", "--session", "proposed", "--json"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert captured["operation"] == "start_session"
    assert captured["params"]["session_id"] == "proposed"
    assert "session_id" not in captured["context"]
    assert captured["operation_id"]
    assert printed["meta"]["session_id"] == "proposed"


def test_cli_human_forms_map_spec_fields(monkeypatch, capsys):
    calls = []

    class FakeClient:
        def __init__(self, *_args, **_kwargs):
            pass

        def call(self, operation, **kwargs):
            calls.append((operation, kwargs))
            return {"ok": True, "data": {}, "error": None, "meta": {}}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(cli, "CommonplaceClient", FakeClient)
    monkeypatch.setenv("COMMONPLACE_URL", "http://localhost:8765")
    monkeypatch.setenv("COMMONPLACE_TOKEN_FILE", "/tmp/token")
    monkeypatch.setenv("COMMONPLACE_PROJECT", "combat")
    monkeypatch.setenv("COMMONPLACE_AGENT_ID", "worker-a")
    monkeypatch.setenv("COMMONPLACE_SESSION_ID", "session-1")
    monkeypatch.setenv("COMMONPLACE_NOTEBOOK_ID", "notebook-1")
    monkeypatch.setenv("COMMONPLACE_GENERATION", "generation-1")

    cli.main(["claim", "create", "--topic", "combat", "--confidence", "likely", "tracks HP"])
    cli.main(["evidence", "add", "184", "--supports", "--method", "watchpoint", "value changed"])
    cli.main(
        [
            "request",
            "create",
            "--type",
            "replicate",
            "--claim",
            "184",
            "--redundancy",
            "2",
            "--instructions",
            "repeat the experiment",
            "Independent verification",
        ]
    )
    cli.main(["request", "next", "--lease", "15m"])
    cli.main(["recent", "--since", "920"])
    cli.main(["search", "19A4:02D6"])
    cli.main(["db", "backup", "--name", "nightly"])
    capsys.readouterr()

    assert calls[0][1]["params"] == {
        "topic": "combat",
        "confidence": "likely",
        "statement": "tracks HP",
    }
    assert calls[1][1]["params"] == {
        "claim_id": "184",
        "polarity": "supports",
        "method": "watchpoint",
        "summary": "value changed",
    }
    assert calls[2][1]["params"] == {
        "type": "replicate",
        "claim_id": 184,
        "desired_redundancy": 2,
        "instructions": "repeat the experiment",
        "title": "Independent verification",
    }
    assert calls[3][1]["params"] == {"lease_seconds": 900}
    assert calls[4][1]["params"] == {"since_seq": 920}
    assert calls[5][1]["params"] == {"query": "19A4:02D6"}
    assert calls[6][1]["params"] == {"name": "nightly"}
    assert calls[6][1]["timeout"] == 120


def test_cli_host_maintenance_selects_only_its_arguments(tmp_path, capsys):
    database = tmp_path / "notebook.db"
    assert cli.main(["db", "init", "--db", str(database)]) == 0
    assert database.is_file()
    assert cli.main(["db", "migrate", "--db", str(database)]) == 0
    backup = tmp_path / "backup.db"
    with sqlite3.connect(database) as source, sqlite3.connect(backup) as destination:
        source.backup(destination)
    restored = tmp_path / "restored.db"
    assert cli.main(["db", "restore", "--db", str(restored), "--from", str(backup)]) == 0
    assert restored.is_file()
    assert cli.main(["db", "init", "--db", str(database)]) != 0
    assert "already" in capsys.readouterr().err.lower()
