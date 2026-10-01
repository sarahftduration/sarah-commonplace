"""Shared domain operations. Adapters never implement notebook rules."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from .errors import fail
from .models import (
    ENTITY_TYPES,
    POLARITIES,
    PRIORITIES,
    RELATION_TYPES,
    REPRODUCIBILITY,
    SLUG_RE,
    STATUSES,
    bounded_array,
    canonical,
    decode_cursor,
    encode_cursor,
    enum,
    int_range,
    json_object,
    optional_text,
    page_limit,
    required_text,
    validate_uuid,
)


@dataclass(frozen=True)
class Context:
    notebook_id: str
    generation: str
    project_id: int | None
    project: str | None
    agent_id: str | None
    session_id: str | None
    now: int


def record(connection: sqlite3.Connection, table: str, entity_id: Any) -> dict | None:
    if table not in {
        "projects",
        "agents",
        "sessions",
        "claims",
        "evidence",
        "requests",
        "leases",
        "relationships",
        "refs",
    }:
        raise AssertionError("Unsafe table")
    row = connection.execute(f"SELECT * FROM {table} WHERE id=?", (entity_id,)).fetchone()
    return dict(row) if row else None


def _public(row: dict) -> dict:
    result = row.copy()
    for key in ("metadata_json", "payload_json"):
        if key in result:
            result[key.removesuffix("_json")] = json.loads(result.pop(key)) if result[key] else None
    if "archived_at" in result:
        result["archived"] = result["archived_at"] is not None
    return result


class Core:
    def __init__(self, connection: sqlite3.Connection, context: Context):
        self.db = connection
        self.ctx = context

    @property
    def project_id(self) -> int:
        assert self.ctx.project_id is not None
        return self.ctx.project_id

    @property
    def actor(self) -> tuple[str, str]:
        assert self.ctx.agent_id and self.ctx.session_id
        return self.ctx.agent_id, self.ctx.session_id

    def run(self, operation: str, params: dict) -> Any:
        handler = getattr(self, operation, None)
        if handler is None or operation.startswith("_"):
            fail("not_found", "Unknown operation")
        return handler(params)

    def event(
        self,
        event_type: str,
        entity_type: str,
        entity_id: Any,
        summary: str,
        payload: dict | None = None,
        project_id: int | None = None,
    ) -> None:
        agent, session = self.actor
        self.db.execute(
            """INSERT INTO activity(project_id,timestamp,actor_agent_id,actor_session_id,
               event_type,entity_type,entity_id,summary,payload_json)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                self.project_id if project_id is None and self.ctx.project_id else project_id,
                self.ctx.now,
                agent,
                session,
                event_type,
                entity_type,
                str(entity_id),
                summary,
                canonical(payload) if payload is not None else None,
            ),
        )

    def _entity(self, kind: str, entity_id: Any) -> dict:
        enum(kind, "entity_type", ENTITY_TYPES)
        entity_id = int_range(entity_id, "entity_id", 1)
        table = {"claim": "claims", "evidence": "evidence", "request": "requests"}[kind]
        row = record(self.db, table, entity_id)
        if row is None or row["project_id"] != self.project_id:
            fail("not_found", f"{kind} was not found")
        return row

    def _index(self, kind: str, entity_id: int) -> None:
        from .search import index_entity

        index_entity(self.db, self.project_id, kind, entity_id)

    def _tags(self, kind: str, entity_id: int, values: Any) -> list[str]:
        names = []
        join = {"claim": "claim_tags", "evidence": "evidence_tags", "request": "request_tags"}[kind]
        column = f"{kind}_id"
        for value in bounded_array(values, "tags"):
            name = required_text(value, "tag").casefold()
            if not name or len(name.encode("utf-8")) > 65536:
                fail("validation_error", "Invalid tag")
            self.db.execute(
                "INSERT OR IGNORE INTO tags(project_id,name) VALUES(?,?)", (self.project_id, name)
            )
            tag_id = self.db.execute(
                "SELECT id FROM tags WHERE project_id=? AND name=?", (self.project_id, name)
            ).fetchone()[0]
            cursor = self.db.execute(
                f"INSERT OR IGNORE INTO {join}(project_id,{column},tag_id) VALUES(?,?,?)",
                (self.project_id, entity_id, tag_id),
            )
            if cursor.rowcount:
                names.append(name)
        return names

    def _reference(self, value: Any) -> dict:
        if isinstance(value, int) and not isinstance(value, bool):
            row = record(self.db, "refs", int_range(value, "reference", 1))
            if row is None or row["project_id"] != self.project_id:
                fail("not_found", "Reference was not found")
            return row
        if not isinstance(value, dict):
            fail("validation_error", "Reference must be an ID or object")
        if set(value) - {"kind", "label", "uri", "value", "metadata"}:
            fail("validation_error", "Unknown reference field")
        kind = required_text(value.get("kind"), "reference.kind")
        label = optional_text(value.get("label"), "reference.label")
        uri = optional_text(value.get("uri"), "reference.uri")
        literal = optional_text(value.get("value"), "reference.value")
        if uri is None and literal is None:
            fail("validation_error", "Reference needs uri or value")
        meta = json_object(value.get("metadata"), "reference.metadata")
        agent, session = self.actor
        cursor = self.db.execute(
            """INSERT INTO refs(project_id,kind,label,uri,value,metadata_json,
               created_by_agent_id,created_by_session_id,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (self.project_id, kind, label, uri, literal, meta, agent, session, self.ctx.now),
        )
        return record(self.db, "refs", cursor.lastrowid)

    def _refs(self, kind: str, entity_id: int, values: Any) -> list[int]:
        join = {"claim": "claim_refs", "evidence": "evidence_refs", "request": "request_refs"}[kind]
        column = f"{kind}_id"
        added = []
        for value in bounded_array(values, "refs"):
            ref = self._reference(value)
            cursor = self.db.execute(
                f"INSERT OR IGNORE INTO {join}(project_id,{column},ref_id) VALUES(?,?,?)",
                (self.project_id, entity_id, ref["id"]),
            )
            if cursor.rowcount:
                added.append(ref["id"])
        return added

    def _attachments(self, kind: str, entity_id: int, params: dict) -> tuple[list[str], list[int]]:
        tags = self._tags(kind, entity_id, params.get("tags"))
        refs = self._refs(kind, entity_id, params.get("refs"))
        self._index(kind, entity_id)
        return tags, refs

    def _decorate(self, kind: str, row: dict) -> dict:
        entity_id = row["id"]
        join_tags = {"claim": "claim_tags", "evidence": "evidence_tags", "request": "request_tags"}[
            kind
        ]
        join_refs = {"claim": "claim_refs", "evidence": "evidence_refs", "request": "request_refs"}[
            kind
        ]
        col = f"{kind}_id"
        result = _public(row)
        result["tags"] = [
            r[0]
            for r in self.db.execute(
                f"SELECT t.name FROM {join_tags} x JOIN tags t ON t.id=x.tag_id "
                f"WHERE x.{col}=? ORDER BY t.name",
                (entity_id,),
            )
        ]
        result["refs"] = [
            _public(dict(r))
            for r in self.db.execute(
                f"SELECT r.* FROM {join_refs} x JOIN refs r ON r.id=x.ref_id "
                f"WHERE x.{col}=? ORDER BY r.id",
                (entity_id,),
            )
        ]
        return result

    def _page_rows(self, table: str, where: str, args: list, params: dict, bind: dict) -> dict:
        limit = page_limit(params)
        bind = {**bind, "generation": self.ctx.generation}
        cursor = decode_cursor(params.get("cursor"), bind)
        last_id = cursor.get("last_id", 0)
        rows = self.db.execute(
            f"SELECT * FROM {table} WHERE {where} AND id>? ORDER BY id LIMIT ?",
            (*args, last_id, limit + 1),
        ).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        return {
            "items": [_public(dict(r)) for r in rows],
            "next_cursor": encode_cursor({"bind": bind, "last_id": rows[-1]["id"]})
            if more
            else None,
        }

    def context(self, params: dict) -> dict:
        from .schema import SCHEMA_VERSION

        return {
            "api_versions": ["v1"],
            "schema_version": SCHEMA_VERSION,
            "notebook_id": self.ctx.notebook_id,
            "generation": self.ctx.generation,
            "server_time": self.ctx.now,
            "project": self.ctx.project,
            "agent_id": self.ctx.agent_id,
            "session_id": self.ctx.session_id,
        }

    def database_info(self, params: dict) -> dict:
        from .schema import SCHEMA_VERSION

        return {
            "notebook_id": self.ctx.notebook_id,
            "generation": self.ctx.generation,
            "schema_version": SCHEMA_VERSION,
            "sqlite_version": sqlite3.sqlite_version,
        }

    def start_session(self, params: dict) -> dict:
        session_id = validate_uuid(params.get("session_id"), "session_id")
        meta = json_object(params.get("metadata"), "metadata")
        assert self.ctx.agent_id
        existing = record(self.db, "sessions", session_id)
        if existing:
            if existing["agent_id"] != self.ctx.agent_id or existing["metadata_json"] != meta:
                fail("conflict", "Session ID already has different identity or metadata")
            if existing["ended_at"] is not None:
                fail("session_closed", "Ended session cannot be reopened")
            return _public(existing)
        agent = record(self.db, "agents", self.ctx.agent_id)
        if not agent:
            self.db.execute(
                """INSERT INTO agents(
                     id,display_name,kind,role,metadata_json,created_at,last_seen_at)
                   VALUES(?,NULL,'unknown',NULL,'{}',?,?)""",
                (self.ctx.agent_id, self.ctx.now, self.ctx.now),
            )
        self.db.execute(
            "INSERT INTO sessions(id,agent_id,started_at,metadata_json) VALUES(?,?,?,?)",
            (session_id, self.ctx.agent_id, self.ctx.now, meta),
        )
        # Bootstrap events belong to the newly created research session.
        self.ctx = Context(
            self.ctx.notebook_id,
            self.ctx.generation,
            None,
            None,
            self.ctx.agent_id,
            session_id,
            self.ctx.now,
        )
        if not agent:
            self.event(
                "agent.registered", "agent", self.ctx.agent_id, "Agent registered", project_id=None
            )
        self.event("session.started", "session", session_id, "Session started", project_id=None)
        return _public(record(self.db, "sessions", session_id))

    def register_agent(self, params: dict) -> dict:
        agent_id = required_text(params.get("id"), "id")
        display = optional_text(params.get("display_name"), "display_name")
        kind = enum(params.get("kind", "unknown"), "kind", {"human", "agent", "tool", "unknown"})
        role = optional_text(params.get("role"), "role")
        meta = json_object(params.get("metadata"), "metadata")
        existing = record(self.db, "agents", agent_id)
        if existing:
            if any(
                (existing[k] != v)
                for k, v in {
                    "display_name": display,
                    "kind": kind,
                    "role": role,
                    "metadata_json": meta,
                }.items()
            ):
                fail("conflict", "Agent is already registered with different metadata")
            return _public(existing)
        self.db.execute(
            """INSERT INTO agents(id,display_name,kind,role,metadata_json,created_at,last_seen_at)
               VALUES(?,?,?,?,?,?,?)""",
            (agent_id, display, kind, role, meta, self.ctx.now, self.ctx.now),
        )
        self.event("agent.registered", "agent", agent_id, "Agent registered", project_id=None)
        return _public(record(self.db, "agents", agent_id))

    def end_session(self, params: dict) -> dict:
        assert self.ctx.session_id
        session = record(self.db, "sessions", self.ctx.session_id)
        if session["ended_at"] is not None:
            return _public(session)
        self.db.execute(
            "UPDATE sessions SET ended_at=? WHERE id=?", (self.ctx.now, self.ctx.session_id)
        )
        active = self.db.execute(
            """SELECT l.* FROM leases l JOIN requests r ON r.id=l.request_id
               WHERE l.session_id=? AND l.released_at IS NULL AND l.completed_at IS NULL
               AND l.expires_at>? AND r.cancelled_at IS NULL AND r.completed_at IS NULL""",
            (self.ctx.session_id, self.ctx.now),
        ).fetchall()
        for lease in active:
            self.db.execute(
                "UPDATE leases SET released_at=?,release_note=? WHERE id=?",
                (self.ctx.now, "Session ended", lease["id"]),
            )
            self.event(
                "request.lease_released",
                "lease",
                lease["id"],
                "Lease released on session end",
                {"request_id": lease["request_id"]},
                project_id=lease["project_id"],
            )
        self.event(
            "session.ended", "session", self.ctx.session_id, "Session ended", project_id=None
        )
        return _public(record(self.db, "sessions", self.ctx.session_id))

    def create_project(self, params: dict) -> dict:
        slug = required_text(params.get("slug"), "slug")
        if not SLUG_RE.fullmatch(slug):
            fail("validation_error", "slug must use lowercase letters, digits, and hyphens")
        name = required_text(params.get("name"), "name")
        description = optional_text(params.get("description"), "description")
        if self.db.execute("SELECT 1 FROM projects WHERE slug=?", (slug,)).fetchone():
            fail("conflict", "Project slug already exists")
        cursor = self.db.execute(
            "INSERT INTO projects(slug,name,description,created_at) VALUES(?,?,?,?)",
            (slug, name, description, self.ctx.now),
        )
        self.event(
            "project.created",
            "project",
            cursor.lastrowid,
            "Project created",
            project_id=cursor.lastrowid,
        )
        return _public(record(self.db, "projects", cursor.lastrowid))

    def list_projects(self, params: dict) -> dict:
        return self._page_rows("projects", "1=1", [], params, {"operation": "list_projects"})

    def get_project(self, params: dict) -> dict:
        slug = required_text(params.get("slug"), "slug")
        row = self.db.execute("SELECT * FROM projects WHERE slug=?", (slug,)).fetchone()
        if row is None:
            fail("not_found", "Project was not found")
        return _public(dict(row))

    def set_project_archived(self, params: dict) -> dict:
        slug = required_text(params.get("slug"), "slug")
        archived = params.get("archived")
        if not isinstance(archived, bool):
            fail("validation_error", "archived must be a boolean")
        note = required_text(params.get("note"), "note")
        row = self.db.execute("SELECT * FROM projects WHERE slug=?", (slug,)).fetchone()
        if row is None:
            fail("not_found", "Project was not found")
        before = row["archived_at"] is not None
        if before != archived:
            self.db.execute(
                "UPDATE projects SET archived_at=? WHERE id=?",
                (self.ctx.now if archived else None, row["id"]),
            )
            self.event(
                "project.archived" if archived else "project.unarchived",
                "project",
                row["id"],
                "Project archive state changed",
                {"before": before, "after": archived, "note": note},
                project_id=row["id"],
            )
        return _public(record(self.db, "projects", row["id"]))

    def _insert_claim(self, params: dict) -> dict:
        topic = required_text(params.get("topic"), "topic")
        statement = required_text(params.get("statement"), "statement")
        rationale = optional_text(params.get("rationale"), "rationale")
        confidence = params.get("confidence")
        if confidence is not None:
            enum(confidence, "confidence", {"tentative", "likely", "strong"})
        priority = enum(params.get("priority", "normal"), "priority", PRIORITIES)
        agent, session = self.actor
        cursor = self.db.execute(
            """INSERT INTO claims(project_id,author_agent_id,author_session_id,topic,statement,
               rationale,confidence,priority,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                self.project_id,
                agent,
                session,
                topic,
                statement,
                rationale,
                confidence,
                priority,
                self.ctx.now,
                self.ctx.now,
            ),
        )
        self._attachments("claim", cursor.lastrowid, params)
        self.event("claim.created", "claim", cursor.lastrowid, "Claim created")
        return self._decorate("claim", record(self.db, "claims", cursor.lastrowid))

    def create_claim(self, params: dict) -> dict:
        return self._insert_claim(params)

    def get_claim(self, params: dict) -> dict:
        claim = self._entity("claim", params.get("id"))
        result = self._decorate("claim", claim)
        for key in ("include_evidence", "include_relationships"):
            if key in params and not isinstance(params[key], bool):
                fail("validation_error", f"{key} must be a boolean")
        if params.get("include_evidence", True):
            rows = self.db.execute(
                "SELECT * FROM evidence WHERE claim_id=? ORDER BY id LIMIT 51", (claim["id"],)
            ).fetchall()
            result["evidence"] = [self._decorate("evidence", dict(r)) for r in rows[:50]]
            result["evidence_next_cursor"] = (
                encode_cursor(
                    {
                        "bind": {
                            "operation": "list_evidence",
                            "claim_id": claim["id"],
                            "polarity": None,
                            "method": None,
                            "project": self.ctx.project,
                            "generation": self.ctx.generation,
                        },
                        "last_id": rows[49]["id"],
                    }
                )
                if len(rows) > 50
                else None
            )
        if params.get("include_relationships", True):
            rows = self.db.execute(
                """SELECT * FROM relationships WHERE project_id=? AND
                   (from_claim_id=? OR to_claim_id=?) ORDER BY id LIMIT 51""",
                (self.project_id, claim["id"], claim["id"]),
            ).fetchall()
            result["relationships"] = [_public(dict(r)) for r in rows[:50]]
            result["relationships_next_cursor"] = (
                encode_cursor(
                    {
                        "bind": {
                            "operation": "list_relationships",
                            "claim_id": claim["id"],
                            "project": self.ctx.project,
                            "generation": self.ctx.generation,
                        },
                        "last_id": rows[49]["id"],
                    }
                )
                if len(rows) > 50
                else None
            )
        return result

    def set_claim_status(self, params: dict) -> dict:
        claim = self._entity("claim", params.get("id"))
        status = enum(params.get("status"), "status", STATUSES - {"superseded"})
        note = required_text(params.get("note"), "note")
        if claim["status"] == "superseded":
            fail("conflict", "Superseded claim cannot change status")
        if claim["status"] != status:
            self.db.execute(
                "UPDATE claims SET status=?,updated_at=? WHERE id=?",
                (status, self.ctx.now, claim["id"]),
            )
            self.event(
                "claim.status_changed",
                "claim",
                claim["id"],
                "Claim status changed",
                {"before": claim["status"], "after": status, "note": note},
            )
        return self._decorate("claim", record(self.db, "claims", claim["id"]))

    def supersede_claim(self, params: dict) -> dict:
        old = self._entity("claim", params.get("old_id"))
        if old["status"] == "superseded":
            fail("conflict", "Claim is already superseded")
        new = self._insert_claim(params)
        self.db.execute(
            "UPDATE claims SET status='superseded',superseded_by_claim_id=?,"
            "updated_at=? WHERE id=?",
            (new["id"], self.ctx.now, old["id"]),
        )
        agent, session = self.actor
        self.db.execute(
            """INSERT INTO relationships(project_id,from_claim_id,to_claim_id,type,
               author_agent_id,author_session_id,created_at) VALUES(?,?,?,'supersedes',?,?,?)""",
            (self.project_id, new["id"], old["id"], agent, session, self.ctx.now),
        )
        self.event(
            "claim.superseded",
            "claim",
            old["id"],
            "Claim superseded",
            {"before": old["status"], "after": "superseded", "new_claim_id": new["id"]},
        )
        return new

    def add_evidence(self, params: dict) -> dict:
        claim = self._entity("claim", params.get("claim_id"))
        polarity = enum(params.get("polarity"), "polarity", POLARITIES)
        method = required_text(params.get("method"), "method")
        summary = required_text(params.get("summary"), "summary")
        procedure = optional_text(params.get("procedure"), "procedure")
        observation = optional_text(params.get("observation"), "observation")
        reproducibility = enum(
            params.get("reproducibility", "unreplicated"), "reproducibility", REPRODUCIBILITY
        )
        replicate_id = params.get("replicates_evidence_id")
        if replicate_id is not None:
            previous = self._entity("evidence", replicate_id)
            if previous["claim_id"] != claim["id"]:
                fail("validation_error", "Replication evidence must target the same claim")
        agent, session = self.actor
        cursor = self.db.execute(
            """INSERT INTO evidence(project_id,claim_id,author_agent_id,author_session_id,
               polarity,method,summary,procedure,observation,reproducibility,
               replicates_evidence_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                self.project_id,
                claim["id"],
                agent,
                session,
                polarity,
                method,
                summary,
                procedure,
                observation,
                reproducibility,
                replicate_id,
                self.ctx.now,
            ),
        )
        self._attachments("evidence", cursor.lastrowid, params)
        self.event(
            "evidence.added",
            "evidence",
            cursor.lastrowid,
            "Evidence added",
            {"claim_id": claim["id"], "polarity": polarity},
        )
        return self._decorate("evidence", record(self.db, "evidence", cursor.lastrowid))

    def list_evidence(self, params: dict) -> dict:
        claim = self._entity("claim", params.get("claim_id"))
        polarity = params.get("polarity")
        if polarity is not None:
            enum(polarity, "polarity", POLARITIES)
        method = params.get("method")
        if method is not None:
            method = required_text(method, "method")
        bind = {
            "operation": "list_evidence",
            "claim_id": claim["id"],
            "polarity": polarity,
            "method": method,
            "project": self.ctx.project,
        }
        where = "project_id=? AND claim_id=?"
        args = [self.project_id, claim["id"]]
        if polarity:
            where += " AND polarity=?"
            args.append(polarity)
        if method:
            where += " AND method=?"
            args.append(method)
        page = self._page_rows("evidence", where, args, params, bind)
        page["items"] = [self._decorate("evidence", r) for r in page["items"]]
        return page

    def _request_result(self, row: dict) -> dict:
        result = self._decorate("request", row)
        completed = self.db.execute(
            "SELECT COUNT(*) FROM leases WHERE request_id=? AND completed_at IS NOT NULL",
            (row["id"],),
        ).fetchone()[0]
        active = self.db.execute(
            """SELECT COUNT(*) FROM leases WHERE request_id=? AND released_at IS NULL
               AND completed_at IS NULL AND expires_at>?""",
            (row["id"], self.ctx.now),
        ).fetchone()[0]
        if row["cancelled_at"] is not None:
            status = "cancelled"
        elif completed == row["desired_redundancy"]:
            status = "completed"
        elif active:
            status = "leased"
        else:
            status = "open"
        result.update(
            status=status,
            completed_count=completed,
            active_lease_count=active if status not in {"completed", "cancelled"} else 0,
            available_slots=(row["desired_redundancy"] - completed - active)
            if status not in {"completed", "cancelled"}
            else 0,
        )
        return result

    def _lease_result(self, row: dict) -> dict:
        result = _public(row)
        result["evidence_ids"] = [
            r[0]
            for r in self.db.execute(
                "SELECT evidence_id FROM lease_evidence WHERE lease_id=? ORDER BY evidence_id",
                (row["id"],),
            )
        ]
        result["resulting_claim_ids"] = [
            r[0]
            for r in self.db.execute(
                "SELECT claim_id FROM lease_claims WHERE lease_id=? ORDER BY claim_id", (row["id"],)
            )
        ]
        request_state = self.db.execute(
            "SELECT completed_at,cancelled_at FROM requests WHERE id=?", (row["request_id"],)
        ).fetchone()
        result["active"] = (
            row["released_at"] is None
            and row["completed_at"] is None
            and row["expires_at"] > self.ctx.now
            and request_state["completed_at"] is None
            and request_state["cancelled_at"] is None
        )
        return result

    def create_request(self, params: dict) -> dict:
        req_type = required_text(params.get("type"), "type")
        title = required_text(params.get("title"), "title")
        instructions = required_text(params.get("instructions"), "instructions")
        claim_id = params.get("claim_id")
        if claim_id is not None:
            self._entity("claim", claim_id)
        priority = enum(params.get("priority", "normal"), "priority", PRIORITIES)
        redundancy = int_range(params.get("desired_redundancy", 1), "desired_redundancy", 1, 50)
        agent, session = self.actor
        cursor = self.db.execute(
            """INSERT INTO requests(project_id,created_by_agent_id,created_by_session_id,
               claim_id,type,title,instructions,priority,desired_redundancy,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                self.project_id,
                agent,
                session,
                claim_id,
                req_type,
                title,
                instructions,
                priority,
                redundancy,
                self.ctx.now,
            ),
        )
        self._attachments("request", cursor.lastrowid, params)
        self.event(
            "request.created",
            "request",
            cursor.lastrowid,
            "Request created",
            {"desired_redundancy": redundancy},
        )
        return self._request_result(record(self.db, "requests", cursor.lastrowid))

    def get_request(self, params: dict) -> dict:
        row = self._entity("request", params.get("id"))
        result = self._request_result(row)
        rows = self.db.execute(
            "SELECT * FROM leases WHERE request_id=? ORDER BY id LIMIT 51", (row["id"],)
        ).fetchall()
        result["leases"] = [self._lease_result(dict(r)) for r in rows[:50]]
        result["leases_next_cursor"] = (
            encode_cursor(
                {
                    "bind": {
                        "operation": "list_leases",
                        "request_id": row["id"],
                        "agent_id": None,
                        "project": self.ctx.project,
                        "generation": self.ctx.generation,
                    },
                    "last_id": rows[49]["id"],
                }
            )
            if len(rows) > 50
            else None
        )
        return result

    def _available(self, row: dict) -> bool:
        result = self._request_result(row)
        if result["available_slots"] <= 0:
            return False
        agent, _ = self.actor
        if self.db.execute(
            "SELECT 1 FROM leases WHERE request_id=? AND agent_id=? AND completed_at IS NOT NULL",
            (row["id"], agent),
        ).fetchone():
            return False
        if self.db.execute(
            """SELECT 1 FROM leases WHERE request_id=? AND agent_id=?
               AND released_at IS NULL AND completed_at IS NULL AND expires_at>?""",
            (row["id"], agent, self.ctx.now),
        ).fetchone():
            return False
        return True

    def _acquire(self, row: dict, seconds: int) -> dict:
        if row["cancelled_at"] is not None or row["completed_at"] is not None:
            fail("request_closed", "Request is closed")
        if not self._available(row):
            fail("lease_conflict", "Request has no available slot for this agent")
        agent, session = self.actor
        cursor = self.db.execute(
            """INSERT INTO leases(project_id,request_id,agent_id,session_id,leased_at,expires_at)
               VALUES(?,?,?,?,?,?)""",
            (
                self.project_id,
                row["id"],
                agent,
                session,
                self.ctx.now,
                self.ctx.now + seconds * 1000,
            ),
        )
        self.event(
            "request.leased",
            "lease",
            cursor.lastrowid,
            "Request leased",
            {"request_id": row["id"], "expires_at": self.ctx.now + seconds * 1000},
        )
        return self._lease_result(record(self.db, "leases", cursor.lastrowid))

    def lease_request(self, params: dict) -> dict:
        row = self._entity("request", params.get("request_id"))
        seconds = int_range(params.get("lease_seconds", 900), "lease_seconds", 1, 86400)
        return self._acquire(row, seconds)

    def lease_next_request(self, params: dict) -> dict | None:
        seconds = int_range(params.get("lease_seconds", 900), "lease_seconds", 1, 86400)
        req_type = params.get("type")
        priority = params.get("priority")
        if req_type is not None:
            req_type = required_text(req_type, "type")
        if priority is not None:
            enum(priority, "priority", PRIORITIES)
        tags = [
            required_text(x, "tag").casefold() for x in bounded_array(params.get("tags"), "tags")
        ]
        where = "r.project_id=? AND r.cancelled_at IS NULL AND r.completed_at IS NULL"
        args: list[Any] = [self.project_id]
        if req_type:
            where += " AND r.type=?"
            args.append(req_type)
        if priority:
            where += " AND r.priority=?"
            args.append(priority)
        for tag in tags:
            where += (
                " AND EXISTS(SELECT 1 FROM request_tags x JOIN tags t ON t.id=x.tag_id "
                "WHERE x.request_id=r.id AND t.name=?)"
            )
            args.append(tag)
        rows = self.db.execute(
            f"""SELECT r.* FROM requests r WHERE {where}
                 ORDER BY CASE r.priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1
                 WHEN 'normal' THEN 2 ELSE 3 END, r.created_at, r.id""",
            args,
        ).fetchall()
        for row in rows:
            if self._available(dict(row)):
                return self._acquire(dict(row), seconds)
        return None

    def _owned_lease(self, lease_id: Any) -> dict:
        lease_id = int_range(lease_id, "lease_id", 1)
        row = record(self.db, "leases", lease_id)
        if row is None or row["project_id"] != self.project_id:
            fail("not_found", "Lease was not found")
        if (row["agent_id"], row["session_id"]) != self.actor:
            fail("lease_not_owned", "Lease belongs to another agent or session")
        return row

    def _active_lease(self, row: dict) -> None:
        request = self._entity("request", row["request_id"])
        if row["released_at"] is not None or row["completed_at"] is not None:
            fail("lease_conflict", "Lease is no longer active")
        if row["expires_at"] <= self.ctx.now:
            fail("lease_expired", "Lease has expired", lease_id=row["id"])
        if request["cancelled_at"] is not None or request["completed_at"] is not None:
            fail("request_closed", "Request is closed")

    def renew_lease(self, params: dict) -> dict:
        row = self._owned_lease(params.get("lease_id"))
        seconds = int_range(params.get("lease_seconds", 900), "lease_seconds", 1, 86400)
        self._active_lease(row)
        new_expiry = max(row["expires_at"], self.ctx.now + seconds * 1000)
        if new_expiry != row["expires_at"]:
            self.db.execute("UPDATE leases SET expires_at=? WHERE id=?", (new_expiry, row["id"]))
            self.event(
                "request.lease_renewed",
                "lease",
                row["id"],
                "Lease renewed",
                {"before": row["expires_at"], "after": new_expiry},
            )
        return self._lease_result(record(self.db, "leases", row["id"]))

    def release_lease(self, params: dict) -> dict:
        row = self._owned_lease(params.get("lease_id"))
        note = optional_text(params.get("note"), "note")
        if row["released_at"] is not None:
            if row["release_note"] == note:
                return self._lease_result(row)
            fail("conflict", "Lease was released with different note")
        self._active_lease(row)
        self.db.execute(
            "UPDATE leases SET released_at=?,release_note=? WHERE id=?",
            (self.ctx.now, note, row["id"]),
        )
        self.event(
            "request.lease_released",
            "lease",
            row["id"],
            "Lease released",
            {"request_id": row["request_id"], "note": note},
        )
        return self._lease_result(record(self.db, "leases", row["id"]))

    def complete_request(self, params: dict) -> dict:
        row = self._owned_lease(params.get("lease_id"))
        note = optional_text(params.get("note"), "note")
        note = note.strip() if note is not None else None
        evidence_ids = sorted(
            set(
                int_range(x, "evidence_id", 1)
                for x in bounded_array(params.get("evidence_ids"), "evidence_ids")
            )
        )
        claim_ids = sorted(
            set(
                int_range(x, "resulting_claim_id", 1)
                for x in bounded_array(params.get("resulting_claim_ids"), "resulting_claim_ids")
            )
        )
        if not note and not evidence_ids and not claim_ids:
            fail("validation_error", "Completion needs a note or result IDs")
        if row["completed_at"] is not None:
            original = self._lease_result(row)
            if (
                original["completion_note"] == note
                and original["evidence_ids"] == evidence_ids
                and original["resulting_claim_ids"] == claim_ids
            ):
                return original
            fail("conflict", "Lease was completed with different results")
        self._active_lease(row)
        request = self._entity("request", row["request_id"])
        for evidence_id in evidence_ids:
            evidence = self._entity("evidence", evidence_id)
            if request["claim_id"] is not None and evidence["claim_id"] != request["claim_id"]:
                fail("validation_error", "Result evidence must target request claim")
        for claim_id in claim_ids:
            self._entity("claim", claim_id)
        self.db.execute(
            "UPDATE leases SET completed_at=?,completion_note=? WHERE id=?",
            (self.ctx.now, note, row["id"]),
        )
        self.db.executemany(
            "INSERT INTO lease_evidence(project_id,lease_id,evidence_id) VALUES(?,?,?)",
            [(self.project_id, row["id"], x) for x in evidence_ids],
        )
        self.db.executemany(
            "INSERT INTO lease_claims(project_id,lease_id,claim_id) VALUES(?,?,?)",
            [(self.project_id, row["id"], x) for x in claim_ids],
        )
        completed = self.db.execute(
            "SELECT COUNT(*) FROM leases WHERE request_id=? AND completed_at IS NOT NULL",
            (row["request_id"],),
        ).fetchone()[0]
        if completed == request["desired_redundancy"]:
            self.db.execute(
                "UPDATE requests SET completed_at=? WHERE id=?",
                (self.ctx.now, request["id"]),
            )
        self.event(
            "request.completed",
            "lease",
            row["id"],
            "Request contribution completed",
            {
                "request_id": row["request_id"],
                "completed_count": completed,
                "evidence_ids": evidence_ids,
                "resulting_claim_ids": claim_ids,
            },
        )
        return self._lease_result(record(self.db, "leases", row["id"]))

    def cancel_request(self, params: dict) -> dict:
        row = self._entity("request", params.get("request_id"))
        note = required_text(params.get("note"), "note")
        if row["cancelled_at"] is not None:
            if row["cancellation_note"] == note:
                return self._request_result(row)
            fail("conflict", "Request was cancelled with different note")
        if row["completed_at"] is not None:
            fail("request_closed", "Completed request cannot be cancelled")
        self.db.execute(
            "UPDATE requests SET cancelled_at=?,cancellation_note=? WHERE id=?",
            (self.ctx.now, note, row["id"]),
        )
        active = self.db.execute(
            """SELECT id FROM leases WHERE request_id=? AND released_at IS NULL
               AND completed_at IS NULL AND expires_at>?""",
            (row["id"], self.ctx.now),
        ).fetchall()
        for lease in active:
            self.db.execute(
                "UPDATE leases SET released_at=?,release_note=? WHERE id=?",
                (self.ctx.now, "Request cancelled", lease["id"]),
            )
            self.event(
                "request.lease_released",
                "lease",
                lease["id"],
                "Lease released on cancellation",
                {"request_id": row["id"]},
            )
        self.event("request.cancelled", "request", row["id"], "Request cancelled", {"note": note})
        return self._request_result(record(self.db, "requests", row["id"]))

    def get_lease(self, params: dict) -> dict:
        row = self._entity_lease(params.get("lease_id"))
        return self._lease_result(row)

    def _entity_lease(self, lease_id: Any) -> dict:
        lease_id = int_range(lease_id, "lease_id", 1)
        row = record(self.db, "leases", lease_id)
        if row is None or row["project_id"] != self.project_id:
            fail("not_found", "Lease was not found")
        return row

    def list_leases(self, params: dict) -> dict:
        request_id = params.get("request_id")
        agent_id = params.get("agent_id")
        where = "project_id=?"
        args: list[Any] = [self.project_id]
        if request_id is not None:
            self._entity("request", request_id)
            where += " AND request_id=?"
            args.append(request_id)
        if agent_id is not None:
            agent_id = required_text(agent_id, "agent_id")
            where += " AND agent_id=?"
            args.append(agent_id)
        bind = {
            "operation": "list_leases",
            "request_id": request_id,
            "agent_id": agent_id,
            "project": self.ctx.project,
        }
        page = self._page_rows("leases", where, args, params, bind)
        page["items"] = [self._lease_result(r) for r in page["items"]]
        return page

    def relate_claims(self, params: dict) -> dict:
        source = self._entity("claim", params.get("from_claim_id"))
        target = self._entity("claim", params.get("to_claim_id"))
        if source["id"] == target["id"]:
            fail("validation_error", "Claim cannot relate to itself")
        relation = enum(params.get("type"), "type", RELATION_TYPES)
        note = optional_text(params.get("note"), "note")
        agent, session = self.actor
        try:
            cursor = self.db.execute(
                """INSERT INTO relationships(project_id,from_claim_id,to_claim_id,type,
                   author_agent_id,author_session_id,created_at,note) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    self.project_id,
                    source["id"],
                    target["id"],
                    relation,
                    agent,
                    session,
                    self.ctx.now,
                    note,
                ),
            )
        except sqlite3.IntegrityError:
            fail("conflict", "Relationship already exists")
        self.event(
            "relationship.created",
            "relationship",
            cursor.lastrowid,
            "Relationship created",
            {"from_claim_id": source["id"], "to_claim_id": target["id"], "type": relation},
        )
        return _public(record(self.db, "relationships", cursor.lastrowid))

    def list_relationships(self, params: dict) -> dict:
        claim = self._entity("claim", params.get("claim_id"))
        bind = {
            "operation": "list_relationships",
            "claim_id": claim["id"],
            "project": self.ctx.project,
        }
        return self._page_rows(
            "relationships",
            "project_id=? AND (from_claim_id=? OR to_claim_id=?)",
            [self.project_id, claim["id"], claim["id"]],
            params,
            bind,
        )

    def attach_reference(self, params: dict) -> dict:
        kind = enum(params.get("entity_type"), "entity_type", ENTITY_TYPES)
        entity = self._entity(kind, params.get("entity_id"))
        ref = self._reference(params.get("reference"))
        join = {"claim": "claim_refs", "evidence": "evidence_refs", "request": "request_refs"}[kind]
        cursor = self.db.execute(
            f"INSERT OR IGNORE INTO {join}(project_id,{kind}_id,ref_id) VALUES(?,?,?)",
            (self.project_id, entity["id"], ref["id"]),
        )
        if cursor.rowcount:
            self._index(kind, entity["id"])
            self.event(
                "reference.attached",
                kind,
                entity["id"],
                "Reference attached",
                {"reference_id": ref["id"]},
            )
        return _public(ref)

    def add_tags(self, params: dict) -> list[str]:
        kind = enum(params.get("entity_type"), "entity_type", ENTITY_TYPES)
        entity = self._entity(kind, params.get("entity_id"))
        added = self._tags(kind, entity["id"], params.get("tags"))
        if added:
            self._index(kind, entity["id"])
            self.event("tags.added", kind, entity["id"], "Tags added", {"tags": added})
        return self._decorate(kind, entity)["tags"]

    def _search_params(self, params: dict) -> dict:
        checked = dict(params)
        if "query" in checked:
            checked["query"] = optional_text(checked["query"], "query")
        for key in ("topic", "status", "author", "ref_kind", "type", "priority"):
            if checked.get(key) is not None:
                checked[key] = required_text(checked[key], key)
        if "tags" in checked:
            checked["tags"] = [
                required_text(x, "tag").casefold() for x in bounded_array(checked["tags"], "tags")
            ]
        if "entity_types" in checked:
            checked["entity_types"] = [
                enum(x, "entity_type", ENTITY_TYPES)
                for x in bounded_array(checked["entity_types"], "entity_types")
            ]
        if "available_only" in checked and not isinstance(checked["available_only"], bool):
            fail("validation_error", "available_only must be a boolean")
        if checked.get("cursor") is not None and (
            not isinstance(checked["cursor"], str) or len(checked["cursor"]) > 4096
        ):
            fail("validation_error", "Invalid cursor")
        return checked

    def search(self, params: dict) -> dict:
        from .search import search

        return search(
            self.db,
            self.project_id,
            self._search_params(params),
            generation=self.ctx.generation,
            now_ms=self.ctx.now,
        )

    def search_claims(self, params: dict) -> dict:
        from .search import search

        return search(
            self.db,
            self.project_id,
            self._search_params(params),
            entity_types=["claim"],
            generation=self.ctx.generation,
        )

    def search_requests(self, params: dict) -> dict:
        from .search import search

        return search(
            self.db,
            self.project_id,
            self._search_params(params),
            entity_types=["request"],
            now_ms=self.ctx.now,
            generation=self.ctx.generation,
        )

    def recent_activity(self, params: dict) -> dict:
        since = int_range(params.get("since_seq", 0), "since_seq", 0)
        limit = page_limit(params, 100)
        types = [
            required_text(x, "event type") for x in bounded_array(params.get("types"), "types")
        ]
        high = self.db.execute("SELECT COALESCE(MAX(seq),0) FROM activity").fetchone()[0]
        where = "project_id=? AND seq>? AND seq<=?"
        args: list[Any] = [self.project_id, since, high]
        if types:
            where += " AND event_type IN (" + ",".join("?" for _ in types) + ")"
            args.extend(types)
        rows = self.db.execute(
            f"SELECT * FROM activity WHERE {where} ORDER BY seq LIMIT ?", (*args, limit + 1)
        ).fetchall()
        more = len(rows) > limit
        page = rows[:limit]
        return {
            "events": [_public(dict(r)) for r in page],
            "next_seq": page[-1]["seq"] if more else max(since, high),
            "has_more": more,
        }

    def project_snapshot(self, params: dict) -> dict:
        pid = self.project_id
        newest = self.db.execute("SELECT COALESCE(MAX(seq),0) FROM activity").fetchone()[0]
        counts = {
            r["status"]: r["count"]
            for r in self.db.execute(
                "SELECT status,COUNT(*) AS count FROM claims WHERE project_id=? GROUP BY status",
                (pid,),
            )
        }
        request_rows = [
            dict(r)
            for r in self.db.execute(
                """SELECT * FROM requests WHERE project_id=? AND priority IN ('high','critical')
               AND completed_at IS NULL AND cancelled_at IS NULL
               ORDER BY CASE priority WHEN 'critical' THEN 0 ELSE 1 END,created_at,id LIMIT 51""",
                (pid,),
            )
        ]
        open_high_total = self.db.execute(
            """SELECT COUNT(*) FROM requests WHERE project_id=?
               AND priority IN ('high','critical') AND completed_at IS NULL
               AND cancelled_at IS NULL""",
            (pid,),
        ).fetchone()[0]
        open_high = [self._request_result(r) for r in request_rows]
        expired_rows = self.db.execute(
            """SELECT DISTINCT r.* FROM requests r JOIN leases l ON l.request_id=r.id
               WHERE r.project_id=? AND r.completed_at IS NULL AND r.cancelled_at IS NULL
               AND l.expires_at<=? AND l.released_at IS NULL AND l.completed_at IS NULL
               ORDER BY r.id LIMIT 51""",
            (pid, self.ctx.now),
        ).fetchall()
        expired_total = self.db.execute(
            """SELECT COUNT(DISTINCT r.id) FROM requests r JOIN leases l ON l.request_id=r.id
               WHERE r.project_id=? AND r.completed_at IS NULL AND r.cancelled_at IS NULL
               AND l.expires_at<=? AND l.released_at IS NULL AND l.completed_at IS NULL""",
            (pid, self.ctx.now),
        ).fetchone()[0]
        recent_agents = [
            dict(r)
            for r in self.db.execute(
                """SELECT actor_agent_id AS agent_id,MAX(timestamp) AS last_activity_at
               FROM activity WHERE project_id=? AND timestamp>=?
               GROUP BY actor_agent_id ORDER BY last_activity_at DESC,agent_id LIMIT 51""",
                (pid, self.ctx.now - 86400000),
            )
        ]
        recent_agents_total = self.db.execute(
            """SELECT COUNT(DISTINCT actor_agent_id) FROM activity
               WHERE project_id=? AND timestamp>=?""",
            (pid, self.ctx.now - 86400000),
        ).fetchone()[0]
        recent_claims = [
            self._decorate("claim", dict(r))
            for r in self.db.execute(
                "SELECT * FROM claims WHERE project_id=? ORDER BY created_at DESC,id DESC LIMIT 51",
                (pid,),
            )
        ]
        recent_claims_total = self.db.execute(
            "SELECT COUNT(*) FROM claims WHERE project_id=?", (pid,)
        ).fetchone()[0]
        contradicted = [
            self._decorate("claim", dict(r))
            for r in self.db.execute(
                """SELECT c.* FROM claims c WHERE c.project_id=? AND EXISTS
               (SELECT 1 FROM evidence e WHERE e.claim_id=c.id AND e.polarity='contradicts')
               ORDER BY c.id LIMIT 51""",
                (pid,),
            )
        ]
        contradicted_total = self.db.execute(
            """SELECT COUNT(*) FROM claims c WHERE c.project_id=? AND EXISTS
               (SELECT 1 FROM evidence e WHERE e.claim_id=c.id AND e.polarity='contradicts')""",
            (pid,),
        ).fetchone()[0]
        accepted_unreplicated = [
            self._decorate("claim", dict(r))
            for r in self.db.execute(
                """SELECT c.* FROM claims c WHERE c.project_id=? AND c.status='accepted'
               AND NOT EXISTS (SELECT 1 FROM evidence e JOIN evidence original
                 ON original.id=e.replicates_evidence_id WHERE e.claim_id=c.id
                 AND e.reproducibility='replicated'
                 AND e.author_agent_id!=original.author_agent_id)
               ORDER BY c.id LIMIT 51""",
                (pid,),
            )
        ]
        accepted_unreplicated_total = self.db.execute(
            """SELECT COUNT(*) FROM claims c WHERE c.project_id=? AND c.status='accepted'
               AND NOT EXISTS (SELECT 1 FROM evidence e JOIN evidence original
                 ON original.id=e.replicates_evidence_id WHERE e.claim_id=c.id
                 AND e.reproducibility='replicated'
                 AND e.author_agent_id!=original.author_agent_id)""",
            (pid,),
        ).fetchone()[0]
        high_no_evidence = [
            self._decorate("claim", dict(r))
            for r in self.db.execute(
                """SELECT c.* FROM claims c WHERE c.project_id=?
               AND c.priority IN ('high','critical')
               AND NOT EXISTS(SELECT 1 FROM evidence e WHERE e.claim_id=c.id)
               ORDER BY c.id LIMIT 51""",
                (pid,),
            )
        ]
        high_no_evidence_total = self.db.execute(
            """SELECT COUNT(*) FROM claims c WHERE c.project_id=?
               AND c.priority IN ('high','critical')
               AND NOT EXISTS(SELECT 1 FROM evidence e WHERE e.claim_id=c.id)""",
            (pid,),
        ).fetchone()[0]

        def bounded(items: list, total: int | None = None) -> dict:
            total = len(items) if total is None else total
            return {"items": items[:50], "total": total, "truncated": total > 50}

        return {
            "newest_seq": newest,
            "claim_counts": {status: counts.get(status, 0) for status in sorted(STATUSES)},
            "high_priority_open_requests": bounded(open_high, open_high_total),
            "requests_with_expired_leases": bounded(
                [self._request_result(dict(r)) for r in expired_rows], expired_total
            ),
            "recently_active_agents": bounded(recent_agents, recent_agents_total),
            "recent_claims": bounded(recent_claims, recent_claims_total),
            "claims_with_contradicting_evidence": bounded(contradicted, contradicted_total),
            "accepted_claims_lacking_independent_replication": bounded(
                accepted_unreplicated, accepted_unreplicated_total
            ),
            "high_priority_claims_with_no_evidence": bounded(
                high_no_evidence, high_no_evidence_total
            ),
        }
