"""Transactional indexing and literal, project-scoped notebook search."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
import time
from typing import Any

from .errors import fail

TOKEN_RE = re.compile(r"[\w:./-]+", re.UNICODE)
IDENTIFIER_RE = re.compile(r"[_:./-]")
SNIPPET_LIMIT = 240
_TABLES = {
    "claim": ("claims", "claim_tags", "claim_refs", "claim_id"),
    "evidence": ("evidence", "evidence_tags", "evidence_refs", "evidence_id"),
    "request": ("requests", "request_tags", "request_refs", "request_id"),
}


def _tokens(text: str) -> set[str]:
    return {token.casefold() for token in TOKEN_RE.findall(text)}


def _entity_fields(
    connection: sqlite3.Connection, project_id: int, entity_type: str, entity_id: int
) -> tuple[dict[str, str], int | None]:
    table, _, _, _ = _TABLES[entity_type]
    row = connection.execute(
        f"SELECT * FROM {table} WHERE project_id=? AND id=?", (project_id, entity_id)
    ).fetchone()
    if row is None:
        fail("not_found", f"{entity_type.title()} does not exist", id=entity_id)
    data = dict(row)
    claim_id = data.get("claim_id") if entity_type == "evidence" else None
    if entity_type == "claim":
        fields = {"topic": data["topic"], "statement": data["statement"]}
        if data.get("rationale"):
            fields["rationale"] = data["rationale"]
    elif entity_type == "evidence":
        fields = {
            key: data[key]
            for key in ("polarity", "method", "summary", "procedure", "observation")
            if data.get(key)
        }
    else:
        fields = {key: data[key] for key in ("type", "title", "instructions") if data.get(key)}

    tag_table, ref_table, fk = _TABLES[entity_type][1:]
    tags = connection.execute(
        f"SELECT t.name FROM tags t JOIN {tag_table} j ON j.tag_id=t.id "
        f"WHERE j.project_id=? AND j.{fk}=? ORDER BY t.name",
        (project_id, entity_id),
    ).fetchall()
    if tags:
        fields["tags"] = " ".join(tag[0] for tag in tags)
    refs = connection.execute(
        f"SELECT r.kind,r.label,r.value,r.uri FROM refs r JOIN {ref_table} j ON j.ref_id=r.id "
        f"WHERE j.project_id=? AND j.{fk}=? ORDER BY r.id",
        (project_id, entity_id),
    ).fetchall()
    for key, col in (
        ("ref_kind", "kind"),
        ("ref_label", "label"),
        ("ref_value", "value"),
        ("ref_uri", "uri"),
    ):
        values = [str(ref[col]) for ref in refs if ref[col]]
        if values:
            fields[key] = " ".join(values)
    return fields, claim_id


def index_entity(
    connection: sqlite3.Connection, project_id: int, entity_type: str, entity_id: int
) -> None:
    """Replace one entity's FTS and exact-token rows atomically (savepoint-safe)."""
    if entity_type not in _TABLES:
        fail("validation_error", "Unsupported searchable entity type", entity_type=entity_type)
    fields, _ = _entity_fields(connection, project_id, entity_type, entity_id)
    text = "\n".join(fields.values())
    fields_json = json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    connection.execute("SAVEPOINT commonplace_index_entity")
    try:
        old = connection.execute(
            "SELECT id FROM search_docs WHERE project_id=? AND entity_type=? AND entity_id=?",
            (project_id, entity_type, entity_id),
        ).fetchone()
        if old:
            doc_id = old[0]
            old_text = connection.execute(
                "SELECT text FROM search_docs WHERE id=?", (doc_id,)
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO search_fts(search_fts,rowid,text) VALUES('delete',?,?)",
                (doc_id, old_text),
            )
            connection.execute("DELETE FROM search_docs WHERE id=?", (doc_id,))
        cursor = connection.execute(
            "INSERT INTO search_docs(project_id,entity_type,entity_id,text,fields_json) "
            "VALUES(?,?,?,?,?)",
            (project_id, entity_type, entity_id, text, fields_json),
        )
        doc_id = cursor.lastrowid
        connection.execute("INSERT INTO search_fts(rowid,text) VALUES(?,?)", (doc_id, text))
        connection.executemany(
            "INSERT INTO search_tokens(doc_id,token) VALUES(?,?)",
            ((doc_id, token) for token in sorted(_tokens(text))),
        )
        connection.execute("RELEASE commonplace_index_entity")
    except BaseException:
        connection.execute("ROLLBACK TO commonplace_index_entity")
        connection.execute("RELEASE commonplace_index_entity")
        raise


def _cursor_encode(value: dict[str, Any]) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _cursor_decode(cursor: str) -> dict[str, Any]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, TypeError, json.JSONDecodeError):
        fail("validation_error", "Invalid search cursor")


def _filter_identity(
    project_id: int,
    params: dict[str, Any],
    entity_types: list[str] | None,
    generation: str | None,
) -> str:
    bound = {
        key: params.get(key)
        for key in (
            "query",
            "topic",
            "status",
            "tags",
            "author",
            "ref_kind",
            "type",
            "priority",
            "available_only",
        )
    }
    bound["project_id"] = project_id
    bound["generation"] = generation
    bound["entity_types"] = entity_types if entity_types is not None else params.get("entity_types")
    return hashlib.sha256(
        json.dumps(bound, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _literal_fts_term(term: str) -> str:
    return '"' + term.replace('"', '""') + '"'


def _candidate_doc_ids(
    connection: sqlite3.Connection, project_id: int, terms: list[str]
) -> set[int]:
    """Intersect per-term token/FTS index hits before loading candidate documents."""
    candidates: set[int] | None = None
    for term in terms:
        if IDENTIFIER_RE.search(term):
            term_hits = {
                int(row[0])
                for row in connection.execute(
                    "SELECT d.id FROM search_tokens t JOIN search_docs d ON d.id=t.doc_id "
                    "WHERE t.token=? AND d.project_id=?",
                    (term.casefold(), project_id),
                )
            }
        else:
            term_hits = {
                int(row[0])
                for row in connection.execute(
                    "SELECT d.id FROM search_tokens t JOIN search_docs d ON d.id=t.doc_id "
                    "WHERE t.token=? AND d.project_id=?",
                    (term.casefold(), project_id),
                )
            }
            try:
                term_hits.update(
                    int(row[0])
                    for row in connection.execute(
                        "SELECT d.id FROM search_fts f JOIN search_docs d ON d.id=f.rowid "
                        "WHERE search_fts MATCH ? AND d.project_id=?",
                        (_literal_fts_term(term), project_id),
                    )
                )
            except sqlite3.OperationalError:
                # Punctuation-only terms may not be indexable by FTS5.
                pass
        candidates = term_hits if candidates is None else candidates & term_hits
        if not candidates:
            return set()
    return candidates or set()


def _fetch_docs(
    connection: sqlite3.Connection, project_id: int, candidates: set[int] | None
) -> list[sqlite3.Row]:
    if candidates is None:
        return connection.execute(
            "SELECT id,entity_type,entity_id,text,fields_json FROM search_docs WHERE project_id=?",
            (project_id,),
        ).fetchall()
    if not candidates:
        return []
    # Stay below SQLite's bound-variable limit even for a common search term.
    result = []
    ids = sorted(candidates)
    for offset in range(0, len(ids), 500):
        batch = ids[offset : offset + 500]
        placeholders = ",".join("?" for _ in batch)
        result.extend(
            connection.execute(
                "SELECT id,entity_type,entity_id,text,fields_json FROM search_docs "
                f"WHERE project_id=? AND id IN ({placeholders})",
                [project_id, *batch],
            ).fetchall()
        )
    return result


def _request_status_and_slots(
    connection: sqlite3.Connection, project_id: int, request: dict[str, Any], now_ms: int
) -> tuple[str, int]:
    completed = connection.execute(
        "SELECT COUNT(*) FROM leases WHERE project_id=? AND request_id=? "
        "AND completed_at IS NOT NULL",
        (project_id, request["id"]),
    ).fetchone()[0]
    active = connection.execute(
        "SELECT COUNT(*) FROM leases WHERE project_id=? AND request_id=? "
        "AND completed_at IS NULL AND released_at IS NULL AND expires_at>?",
        (project_id, request["id"], now_ms),
    ).fetchone()[0]
    cancelled = request["cancelled_at"] is not None
    terminal = cancelled or completed >= request["desired_redundancy"]
    slots = 0 if terminal else max(0, request["desired_redundancy"] - completed - active)
    status = (
        "cancelled"
        if cancelled
        else (
            "completed"
            if completed >= request["desired_redundancy"]
            else ("leased" if active else "open")
        )
    )
    return status, slots


def _has_filters(
    connection: sqlite3.Connection,
    project_id: int,
    row: sqlite3.Row,
    fields: dict[str, str],
    params: dict[str, Any],
    now_ms: int,
) -> bool:
    entity_type, entity_id = row["entity_type"], row["entity_id"]
    table, tag_join, ref_join, fk = _TABLES[entity_type]
    entity_row = connection.execute(
        f"SELECT * FROM {table} WHERE project_id=? AND id=?", (project_id, entity_id)
    ).fetchone()
    entity = dict(entity_row) if entity_row else {}
    wanted_tags = params.get("tags") or []
    if isinstance(wanted_tags, str):
        wanted_tags = [wanted_tags]
    if wanted_tags:
        names = {
            r[0].casefold()
            for r in connection.execute(
                f"SELECT t.name FROM tags t JOIN {tag_join} j ON j.tag_id=t.id "
                f"WHERE j.project_id=? AND j.{fk}=?",
                (project_id, entity_id),
            )
        }
        if not all(str(tag).casefold() in names for tag in wanted_tags):
            return False
    ref_kind = params.get("ref_kind")
    if (
        ref_kind
        and not connection.execute(
            f"SELECT 1 FROM refs r JOIN {ref_join} j ON j.ref_id=r.id "
            f"WHERE j.project_id=? AND j.{fk}=? AND r.kind=? LIMIT 1",
            (project_id, entity_id, ref_kind),
        ).fetchone()
    ):
        return False
    author = params.get("author")
    author_col = "author_agent_id" if entity_type != "request" else "created_by_agent_id"
    if author and entity[author_col] != author:
        return False
    if entity_type == "request":
        if params.get("type") is not None and entity["type"] != params["type"]:
            return False
        if params.get("priority") is not None and entity["priority"] != params["priority"]:
            return False
        actual_status, available_slots = _request_status_and_slots(
            connection, project_id, entity, now_ms
        )
        if params.get("status") is not None and actual_status != params["status"]:
            return False
        if params.get("available_only") is True and available_slots == 0:
            return False
    topic = params.get("topic")
    status = params.get("status")
    parent_claim = (
        entity.get("claim_id")
        if entity_type == "evidence"
        else (entity_id if entity_type == "claim" else entity.get("claim_id"))
    )
    claim = None
    if topic or status:
        if parent_claim:
            claim = connection.execute(
                "SELECT topic,status FROM claims WHERE project_id=? AND id=?",
                (project_id, parent_claim),
            ).fetchone()
        if topic and (claim is None or topic.casefold() not in claim["topic"].casefold()):
            return False
        if status and entity_type != "request":
            actual = claim["status"] if claim else None
            if actual != status:
                return False
    return True


def search(
    connection: sqlite3.Connection,
    project_id: int,
    params: dict[str, Any],
    *,
    entity_types: list[str] | None = None,
    now_ms: int | None = None,
    generation: str | None = None,
) -> dict[str, Any]:
    """Search indexed records; ``now_ms`` is the service clock for lease filters."""
    params = dict(params or {})
    query = params.get("query") or ""
    if not isinstance(query, str):
        fail("validation_error", "query must be text")
    limit = params.get("limit", 50)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
        fail("validation_error", "limit must be between 1 and 200")
    selected = entity_types if entity_types is not None else params.get("entity_types")
    if selected is None:
        selected = ["claim", "evidence", "request"]
    if isinstance(selected, str) or any(t not in _TABLES for t in selected):
        fail("validation_error", "entity_types contains an unsupported value")
    selected = sorted(set(selected))
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    terms = query.split()
    # Extract complete maximal tokens; ordinary FTS terms retain user whitespace boundaries.
    exact_terms = [match.group(0).casefold() for match in TOKEN_RE.finditer(query)]
    identity = _filter_identity(project_id, params, selected, generation)
    cursor = params.get("cursor")
    after = None
    if cursor:
        decoded = _cursor_decode(cursor)
        if decoded.get("filter") != identity:
            fail("validation_error", "Search cursor does not match these filters")
        after = decoded.get("after")

    candidates = _candidate_doc_ids(connection, project_id, terms) if query.strip() else None
    docs = _fetch_docs(connection, project_id, candidates)
    hits = []
    for doc in docs:
        if doc["entity_type"] not in selected:
            continue
        fields = json.loads(doc["fields_json"])
        if not _has_filters(connection, project_id, doc, fields, params, now_ms):
            continue
        record_tokens = {
            r[0]
            for r in connection.execute(
                "SELECT token FROM search_tokens WHERE doc_id=?", (doc["id"],)
            )
        }
        # Each whitespace term carrying identifier punctuation must survive byte-for-byte
        # (after Unicode case folding) in the auxiliary token index.
        if query.strip():
            if not exact_terms:
                continue
            required = [t.casefold() for t in terms if IDENTIFIER_RE.search(t)]
            if any(t not in record_tokens for t in required):
                continue
            regular = [t for t in terms if not IDENTIFIER_RE.search(t)]
            fts_rank = 0.0
            if regular:
                match = " AND ".join(_literal_fts_term(t) for t in regular)
                try:
                    rank_row = connection.execute(
                        "SELECT rank FROM search_fts WHERE rowid=? AND search_fts MATCH ?",
                        (doc["id"], match),
                    ).fetchone()
                except sqlite3.OperationalError:
                    rank_row = None
                if rank_row is None:
                    # Exact-token equality is also a valid literal match for ordinary terms.
                    if any(t.casefold() not in record_tokens for t in regular):
                        continue
                else:
                    fts_rank = float(rank_row[0])
            elif not required:
                continue
            # For ordinary terms, exact-token matches can satisfy terms even if FTS parser
            # tokenization differs (for example a quoted/operator word).
            if regular:
                missing_exact = [t for t in regular if t.casefold() not in record_tokens]
                if missing_exact:
                    match = " AND ".join(_literal_fts_term(t) for t in missing_exact)
                    try:
                        ok = connection.execute(
                            "SELECT 1 FROM search_fts WHERE rowid=? AND search_fts MATCH ?",
                            (doc["id"], match),
                        ).fetchone()
                    except sqlite3.OperationalError:
                        ok = None
                    if not ok:
                        continue
            tier = 0 if all(t in record_tokens for t in exact_terms) else 1
            fields_matched = [
                name
                for name, value in fields.items()
                if any(term.casefold() in value.casefold() for term in terms)
            ]
        else:
            tier, fts_rank, fields_matched = 0, 0.0, list(fields)
        sortkey = [tier, fts_rank, doc["entity_type"], int(doc["entity_id"])]
        if after is not None and sortkey <= after:
            continue
        text = doc["text"]
        snippet = _snippet(text, terms)
        item = {
            "entity_type": doc["entity_type"],
            "id": int(doc["entity_id"]),
            "snippet": snippet,
            "matched_fields": fields_matched,
        }
        if doc["entity_type"] == "evidence":
            item["claim_id"] = connection.execute(
                "SELECT claim_id FROM evidence WHERE project_id=? AND id=?",
                (project_id, doc["entity_id"]),
            ).fetchone()[0]
        hits.append((sortkey, item))
    hits.sort(key=lambda pair: tuple(pair[0]))
    page = hits[:limit]
    next_cursor = None
    if len(hits) > limit:
        next_cursor = _cursor_encode({"filter": identity, "after": page[-1][0]})
    return {"items": [item for _, item in page], "next_cursor": next_cursor}


def _snippet(text: str, terms: list[str]) -> str:
    if len(text) <= SNIPPET_LIMIT:
        return text
    folded = text.casefold()
    offsets = [
        folded.find(term.casefold()) for term in terms if term and folded.find(term.casefold()) >= 0
    ]
    start = max(0, min(offsets) - SNIPPET_LIMIT // 3) if offsets else 0
    end = min(len(text), start + SNIPPET_LIMIT)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")
