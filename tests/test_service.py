from __future__ import annotations

import uuid

import pytest

from commonplace.db import initialize
from commonplace.errors import DomainError
from commonplace.maintenance import restore_database
from commonplace.service import Service


def call(service, operation, context, params=None, operation_id=None):
    from commonplace.models import READ_OPERATIONS

    operation_id = operation_id or (None if operation in READ_OPERATIONS else str(uuid.uuid4()))
    return service.call(operation, context, params or {}, operation_id)


def ok(service, operation, context, params=None, operation_id=None):
    result = call(service, operation, context, params, operation_id)
    assert result["ok"], result
    return result["data"]


@pytest.fixture
def notebook(tmp_path):
    path = tmp_path / "notebook.db"
    info = initialize(path)
    now = [1_700_000_000_000]
    with Service(path, backup_dir=tmp_path / "backups", clock=lambda: now[0]) as service:
        base = {key: info[key] for key in ("notebook_id", "generation")}

        def actor(name):
            session_id = str(uuid.uuid4())
            ok(service, "start_session", {**base, "agent_id": name}, {"session_id": session_id})
            return {**base, "agent_id": name, "session_id": session_id}

        author = actor("author")
        ok(service, "create_project", author, {"slug": "demo", "name": "Demo"})
        author["project"] = "demo"
        yield service, path, tmp_path, base, now, actor, author


def test_claim_evidence_and_project_boundaries(notebook):
    service, _, _, _, _, actor, author = notebook
    first = ok(
        service,
        "create_claim",
        author,
        {
            "topic": "combat",
            "statement": "19A4:02D6 tracks HP",
            "priority": "high",
            "tags": ["  Vitality ", "vitality"],
            "refs": [{"kind": "memory-address", "value": "19A4:02D6"}],
        },
    )
    assert first["tags"] == ["vitality"]
    evidence = ok(
        service,
        "add_evidence",
        author,
        {
            "claim_id": first["id"],
            "polarity": "supports",
            "method": "watchpoint",
            "summary": "Value changed after damage",
        },
    )
    other = actor("replicator")
    other["project"] = "demo"
    replication = ok(
        service,
        "add_evidence",
        other,
        {
            "claim_id": first["id"],
            "polarity": "supports",
            "method": "memory-edit",
            "summary": "Direct edit changed status panel",
            "reproducibility": "replicated",
            "replicates_evidence_id": evidence["id"],
        },
    )
    assert replication["replicates_evidence_id"] == evidence["id"]
    replacement = ok(
        service,
        "supersede_claim",
        author,
        {
            "old_id": first["id"],
            "topic": "combat",
            "statement": "Word tracks vitality",
        },
    )
    old = ok(service, "get_claim", author, {"id": first["id"]})
    assert old["status"] == "superseded"
    assert old["superseded_by_claim_id"] == replacement["id"]
    assert any(
        r["type"] == "supersedes" and r["from_claim_id"] == replacement["id"]
        for r in old["relationships"]
    )
    assert (
        call(
            service,
            "set_claim_status",
            author,
            {"id": first["id"], "status": "accepted", "note": "No"},
        )["error"]["code"]
        == "conflict"
    )
    matches = ok(service, "search", author, {"query": "19A4:02D6"})["items"]
    assert [(item["entity_type"], item["id"]) for item in matches] == [("claim", first["id"])]


def test_cross_project_links_are_rejected(notebook):
    service, _, _, base, _, _, author = notebook
    first = ok(service, "create_claim", author, {"topic": "one", "statement": "First project"})
    db_context = {key: author[key] for key in (*base.keys(), "agent_id", "session_id")}
    ok(service, "create_project", db_context, {"slug": "other", "name": "Other"})
    other = {**author, "project": "other"}
    second = ok(service, "create_claim", other, {"topic": "two", "statement": "Second project"})
    assert (
        call(
            service,
            "add_evidence",
            other,
            {
                "claim_id": first["id"],
                "polarity": "neutral",
                "method": "test",
                "summary": "Wrong project",
            },
        )["error"]["code"]
        == "not_found"
    )
    assert (
        call(
            service,
            "relate_claims",
            other,
            {
                "from_claim_id": second["id"],
                "to_claim_id": first["id"],
                "type": "related",
            },
        )["error"]["code"]
        == "not_found"
    )
    assert (
        call(
            service,
            "create_request",
            other,
            {
                "type": "test",
                "title": "Wrong target",
                "instructions": "No",
                "claim_id": first["id"],
            },
        )["error"]["code"]
        == "not_found"
    )


def test_redundant_leases_expiry_and_terminal_retries(notebook):
    service, _, _, _, now, actor, author = notebook
    claim = ok(service, "create_claim", author, {"topic": "hp", "statement": "Candidate"})
    req = ok(
        service,
        "create_request",
        author,
        {
            "type": "replicate",
            "title": "Check",
            "instructions": "Try twice",
            "claim_id": claim["id"],
            "desired_redundancy": 2,
        },
    )
    b = actor("worker-b")
    c = actor("worker-c")
    d = actor("worker-d")
    for worker in (b, c, d):
        worker["project"] = "demo"
    first = ok(service, "lease_request", b, {"request_id": req["id"], "lease_seconds": 1})
    second = ok(service, "lease_request", c, {"request_id": req["id"]})
    b_other_session = actor("worker-b")
    b_other_session["project"] = "demo"
    assert (
        call(
            service,
            "renew_lease",
            b_other_session,
            {
                "lease_id": first["id"],
                "lease_seconds": 10,
            },
        )["error"]["code"]
        == "lease_not_owned"
    )
    assert (
        call(
            service,
            "lease_request",
            b_other_session,
            {
                "request_id": req["id"],
            },
        )["error"]["code"]
        == "lease_conflict"
    )
    assert (
        call(service, "lease_request", d, {"request_id": req["id"]})["error"]["code"]
        == "lease_conflict"
    )
    now[0] += 1000
    assert (
        call(service, "complete_request", b, {"lease_id": first["id"], "note": "late"})["error"][
            "code"
        ]
        == "lease_expired"
    )
    replacement = ok(service, "lease_request", d, {"request_id": req["id"]})
    assert replacement["active"]
    complete_id = str(uuid.uuid4())
    result = ok(
        service, "complete_request", c, {"lease_id": second["id"], "note": "checked"}, complete_id
    )
    assert result["completed_at"] is not None
    assert ok(service, "get_request", author, {"id": req["id"]})["status"] == "leased"
    now[0] += 1000
    assert (
        ok(
            service,
            "complete_request",
            c,
            {"lease_id": second["id"], "note": "checked"},
            complete_id,
        )
        == result
    )
    ok(service, "complete_request", d, {"lease_id": replacement["id"], "note": "negative"})
    assert ok(service, "get_request", author, {"id": req["id"]})["status"] == "completed"
    assert (
        call(service, "lease_request", b, {"request_id": req["id"]})["error"]["code"]
        == "request_closed"
    )


def test_activity_pagination_and_backup_restore(notebook):
    service, path, root, base, _, _, author = notebook
    for i in range(7):
        ok(service, "create_claim", author, {"topic": "batch", "statement": f"Claim {i}"})
    first = ok(service, "recent_activity", author, {"since_seq": 0, "limit": 3})
    assert first["has_more"]
    pages = list(first["events"])
    cursor = first["next_seq"]
    while True:
        page = ok(service, "recent_activity", author, {"since_seq": cursor, "limit": 3})
        pages.extend(page["events"])
        cursor = page["next_seq"]
        if not page["has_more"]:
            break
    assert len({event["seq"] for event in pages}) == len(pages)
    assert len([event for event in pages if event["event_type"] == "claim.created"]) == 7
    filtered = []
    cursor = 0
    while True:
        page = ok(
            service,
            "recent_activity",
            author,
            {
                "since_seq": cursor,
                "limit": 2,
                "types": ["claim.created"],
            },
        )
        filtered.extend(page["events"])
        cursor = page["next_seq"]
        if not page["has_more"]:
            break
    assert len(filtered) == 7
    assert all(event["event_type"] == "claim.created" for event in filtered)
    backup_context = {key: author[key] for key in (*base.keys(), "agent_id", "session_id")}
    operation_id = str(uuid.uuid4())
    receipt = ok(service, "backup_database", backup_context, {"name": "test.db"}, operation_id)
    assert (
        ok(service, "backup_database", backup_context, {"name": "test.db"}, operation_id) == receipt
    )
    restored = restore_database(root / "restored.db", root / "backups" / "test.db")
    assert restored["notebook_id"] == base["notebook_id"]
    assert restored["generation"] != base["generation"]
    with pytest.raises(DomainError):
        Service(path)


def test_replay_survives_session_close(notebook):
    service, _, _, _, _, _, author = notebook
    operation_id = str(uuid.uuid4())
    params = {"topic": "retry", "statement": "Durable result"}
    original = call(service, "create_claim", author, params, operation_id)
    assert original["ok"]
    ended = {key: author[key] for key in ("notebook_id", "generation", "agent_id", "session_id")}
    ok(service, "end_session", ended)
    assert call(service, "create_claim", author, params, operation_id) == original
    assert (
        call(service, "create_claim", author, {**params, "statement": "Changed"}, operation_id)[
            "error"
        ]["code"]
        == "idempotency_conflict"
    )
    assert call(service, "create_claim", author, params)["error"]["code"] == "session_closed"


def test_session_end_releases_lease_even_after_archive(notebook):
    service, _, _, base, _, actor, author = notebook
    request = ok(
        service,
        "create_request",
        author,
        {
            "type": "test",
            "title": "Owned work",
            "instructions": "Try it",
        },
    )
    lease = ok(service, "lease_request", author, {"request_id": request["id"]})
    db_context = {key: author[key] for key in (*base.keys(), "agent_id", "session_id")}
    ok(
        service,
        "set_project_archived",
        db_context,
        {
            "slug": "demo",
            "archived": True,
            "note": "Pause research",
        },
    )
    assert (
        call(
            service,
            "create_claim",
            author,
            {
                "topic": "blocked",
                "statement": "Should not write",
            },
        )["error"]["code"]
        == "project_archived"
    )
    ended = ok(service, "end_session", db_context)
    assert ended["ended_at"] is not None
    reader = actor("reader")
    reader["project"] = "demo"
    found = ok(service, "get_lease", reader, {"lease_id": lease["id"]})
    assert found["released_at"] is not None
    assert found["release_note"] == "Session ended"


def test_backup_reconciles_file_published_before_receipt(notebook, monkeypatch):
    from commonplace.core import Core

    service, _, root, base, _, _, author = notebook
    context = {key: author[key] for key in (*base.keys(), "agent_id", "session_id")}
    operation_id = str(uuid.uuid4())
    original_event = Core.event
    interrupted = [False]

    def fail_once(self, event_type, *args, **kwargs):
        if event_type == "database.backup_completed" and not interrupted[0]:
            interrupted[0] = True
            raise RuntimeError("simulated crash before receipt commit")
        return original_event(self, event_type, *args, **kwargs)

    monkeypatch.setattr(Core, "event", fail_once)
    first = call(service, "backup_database", context, {"name": "window.db"}, operation_id)
    assert first["error"]["code"] == "internal_error"
    assert (root / "backups" / "window.db").is_file()
    second = ok(service, "backup_database", context, {"name": "window.db"}, operation_id)
    assert second["state"] == "complete"
    third = ok(service, "backup_database", context, {"name": "window.db"}, operation_id)
    assert third == second
    activity = service.call("recent_activity", author, {"since_seq": 0})
    assert activity["ok"]
    # Backup completion is database-wide and excluded from project polling.
    assert not any(
        x["event_type"] == "database.backup_completed" for x in activity["data"]["events"]
    )
