import sqlite3

import pytest

from commonplace.schema import DDL
from commonplace.search import _candidate_doc_ids, index_entity, search


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(DDL)
    connection.execute("INSERT INTO projects(id,slug,name,created_at) VALUES(1,'p','Project',1)")
    connection.execute(
        "INSERT INTO agents(id,kind,metadata_json,created_at,last_seen_at) "
        "VALUES('worker','agent','{}',1,1)"
    )
    connection.execute(
        "INSERT INTO sessions(id,agent_id,started_at,metadata_json) "
        "VALUES('session','worker',1,'{}')"
    )
    yield connection
    connection.close()


def add_claim(db, ident, statement, topic="memory"):
    db.execute(
        "INSERT INTO claims(project_id,author_agent_id,author_session_id,topic,statement,"
        "created_at,updated_at) "
        "VALUES(1,'worker','session',?,?,1,1)",
        (topic, statement),
    )
    index_entity(db, 1, "claim", ident)


def test_punctuation_sensitive_literal_search_and_all_terms(db):
    add_claim(db, 1, "Address 19A4:02D6; healing completed")
    add_claim(db, 2, "Address 19A4-02D6; healing completed")
    add_claim(db, 3, "0x0277FF inventory_get_item abcdef0123456789")

    assert [r["id"] for r in search(db, 1, {"query": "19A4:02D6"})["items"]] == [1]
    assert [r["id"] for r in search(db, 1, {"query": "19A4-02D6"})["items"]] == [2]
    assert [r["id"] for r in search(db, 1, {"query": "19A4:02D6 healing"})["items"]] == [1]
    assert [r["id"] for r in search(db, 1, {"query": "inventory_get_item"})["items"]] == [3]
    assert [r["id"] for r in search(db, 1, {"query": "0x0277FF"})["items"]] == [3]
    assert [r["id"] for r in search(db, 1, {"query": "abcdef0123456789"})["items"]] == [3]


def test_fts_operators_quotes_tags_and_references_are_literal_and_searchable(db):
    add_claim(db, 1, 'literal "quote" AND OR NOT parens')
    db.execute("INSERT INTO tags(project_id,name) VALUES(1,'rare-tag')")
    db.execute("INSERT INTO claim_tags(project_id,claim_id,tag_id) VALUES(1,1,1)")
    db.execute(
        "INSERT INTO refs(project_id,kind,label,uri,value,metadata_json,created_by_agent_id,"
        "created_by_session_id,created_at) "
        "VALUES(1,'source','manual label','file:///commit/abc123','sha256:deadbeef','{}','worker','session',1)"
    )
    db.execute("INSERT INTO claim_refs(project_id,claim_id,ref_id) VALUES(1,1,1)")
    index_entity(db, 1, "claim", 1)

    assert search(db, 1, {"query": '"quote" AND OR NOT'})["items"][0]["id"] == 1
    assert search(db, 1, {"query": "rare-tag"})["items"][0]["id"] == 1
    assert search(db, 1, {"query": "manual"})["items"][0]["id"] == 1
    assert search(db, 1, {"query": "file:///commit/abc123"})["items"][0]["id"] == 1
    assert search(db, 1, {"query": "sha256:deadbeef"})["items"][0]["id"] == 1


def test_cursor_binds_filters_and_pages_deterministically(db):
    add_claim(db, 1, "healing alpha")
    add_claim(db, 2, "healing beta")
    add_claim(db, 3, "healing gamma")
    page1 = search(db, 1, {"query": "healing", "limit": 2})
    assert [row["id"] for row in page1["items"]] == [1, 2]
    assert page1["next_cursor"]
    page2 = search(db, 1, {"query": "healing", "limit": 2, "cursor": page1["next_cursor"]})
    assert [row["id"] for row in page2["items"]] == [3]
    with pytest.raises(Exception, match="cursor"):
        search(db, 1, {"query": "alpha", "limit": 2, "cursor": page1["next_cursor"]})


def test_reindex_replaces_old_terms_and_obeys_outer_rollback(db):
    add_claim(db, 1, "oldterm")
    db.execute("UPDATE claims SET statement='newterm' WHERE id=1")
    index_entity(db, 1, "claim", 1)
    assert search(db, 1, {"query": "oldterm"})["items"] == []
    assert len(search(db, 1, {"query": "newterm"})["items"]) == 1

    db.commit()
    db.execute("BEGIN")
    db.execute("UPDATE claims SET statement='rollbackterm' WHERE id=1")
    index_entity(db, 1, "claim", 1)
    db.rollback()
    assert search(db, 1, {"query": "newterm"})["items"]
    assert search(db, 1, {"query": "rollbackterm"})["items"] == []


def test_query_uses_indexed_candidates_across_a_wide_corpus(db):
    for ident in range(1, 301):
        add_claim(db, ident, f"common research record {ident}")
    add_claim(db, 301, "common research needlemarker")

    plan = " ".join(
        row["detail"]
        for row in db.execute(
            "EXPLAIN QUERY PLAN SELECT doc_id FROM search_tokens WHERE token=?",
            ("needlemarker",),
        )
    )
    candidates = _candidate_doc_ids(db, 1, ["needlemarker"])
    assert "search_tokens_token_idx" in plan
    assert len(candidates) == 1
    assert [item["id"] for item in search(db, 1, {"query": "needlemarker"})["items"]] == [301]


def test_request_filters_use_effective_status_and_service_time(db):
    def add_request(kind, priority, redundancy=1, cancelled=False):
        cursor = db.execute(
            "INSERT INTO requests(project_id,created_by_agent_id,created_by_session_id,type,title,"
            "instructions,priority,desired_redundancy,created_at,cancelled_at) "
            "VALUES(1,'worker','session',?,'investigate','investigate target',?,?,1,?)",
            (kind, priority, redundancy, 1 if cancelled else None),
        )
        request_id = cursor.lastrowid
        index_entity(db, 1, "request", request_id)
        return request_id

    leased = add_request("emulator", "high", redundancy=2)
    add_request("analysis", "low")
    add_request("emulator", "high", cancelled=True)
    add_request("analysis", "low")
    db.execute(
        "INSERT INTO leases(project_id,request_id,agent_id,session_id,leased_at,expires_at) "
        "VALUES(1,?,'worker','session',100,2000)",
        (leased,),
    )

    result = search(
        db,
        1,
        {
            "query": "investigate",
            "type": "emulator",
            "priority": "high",
            "status": "leased",
            "available_only": True,
        },
        entity_types=["request"],
        now_ms=1000,
    )
    assert [item["id"] for item in result["items"]] == [leased]

    # The same lease has expired by this service time: status becomes open and a
    # partly-filled request remains available.
    expired = search(
        db,
        1,
        {
            "query": "investigate",
            "type": "emulator",
            "status": "open",
            "available_only": True,
        },
        entity_types=["request"],
        now_ms=3000,
    )
    assert [item["id"] for item in expired["items"]] == [leased]

    # Request filters run before pagination and bind into the cursor.
    first = search(db, 1, {"type": "analysis", "limit": 1}, entity_types=["request"], now_ms=1000)
    assert len(first["items"]) == 1
    assert first["next_cursor"]
    with pytest.raises(Exception, match="cursor"):
        search(
            db,
            1,
            {"type": "emulator", "limit": 1, "cursor": first["next_cursor"]},
            entity_types=["request"],
            now_ms=1000,
        )
