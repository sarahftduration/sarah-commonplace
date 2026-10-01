"""Shared operation registry and input validation."""

from __future__ import annotations

import base64
import json
import re
import uuid
from typing import Any

from .errors import fail

OPERATIONS: dict[str, tuple[str, ...]] = {
    "context": (),
    "database_info": (),
    "start_session": ("session_id", "metadata"),
    "end_session": (),
    "register_agent": ("id", "display_name", "kind", "role", "metadata"),
    "create_project": ("slug", "name", "description"),
    "list_projects": ("limit", "cursor"),
    "get_project": ("slug",),
    "set_project_archived": ("slug", "archived", "note"),
    "create_claim": ("topic", "statement", "rationale", "confidence", "priority", "tags", "refs"),
    "get_claim": ("id", "include_evidence", "include_relationships"),
    "search_claims": ("query", "topic", "status", "tags", "author", "ref_kind", "limit", "cursor"),
    "set_claim_status": ("id", "status", "note"),
    "supersede_claim": (
        "old_id",
        "topic",
        "statement",
        "rationale",
        "confidence",
        "priority",
        "tags",
        "refs",
    ),
    "add_evidence": (
        "claim_id",
        "polarity",
        "method",
        "summary",
        "procedure",
        "observation",
        "reproducibility",
        "replicates_evidence_id",
        "refs",
        "tags",
    ),
    "list_evidence": ("claim_id", "polarity", "method", "limit", "cursor"),
    "create_request": (
        "type",
        "title",
        "instructions",
        "claim_id",
        "priority",
        "desired_redundancy",
        "tags",
        "refs",
    ),
    "get_request": ("id",),
    "search_requests": (
        "query",
        "status",
        "type",
        "priority",
        "tags",
        "available_only",
        "limit",
        "cursor",
    ),
    "lease_request": ("request_id", "lease_seconds"),
    "lease_next_request": ("type", "priority", "tags", "lease_seconds"),
    "renew_lease": ("lease_id", "lease_seconds"),
    "release_lease": ("lease_id", "note"),
    "complete_request": ("lease_id", "note", "evidence_ids", "resulting_claim_ids"),
    "cancel_request": ("request_id", "note"),
    "get_lease": ("lease_id",),
    "list_leases": ("request_id", "agent_id", "limit", "cursor"),
    "relate_claims": ("from_claim_id", "to_claim_id", "type", "note"),
    "list_relationships": ("claim_id", "limit", "cursor"),
    "search": (
        "query",
        "entity_types",
        "topic",
        "status",
        "tags",
        "author",
        "ref_kind",
        "limit",
        "cursor",
    ),
    "attach_reference": ("entity_type", "entity_id", "reference"),
    "add_tags": ("entity_type", "entity_id", "tags"),
    "recent_activity": ("since_seq", "limit", "types"),
    "project_snapshot": (),
    "backup_database": ("name",),
}

READ_OPERATIONS = frozenset(
    {
        "context",
        "database_info",
        "list_projects",
        "get_project",
        "get_claim",
        "search_claims",
        "list_evidence",
        "get_request",
        "search_requests",
        "get_lease",
        "list_leases",
        "list_relationships",
        "search",
        "recent_activity",
        "project_snapshot",
    }
)
DATABASE_WIDE = frozenset(
    {
        "context",
        "database_info",
        "start_session",
        "end_session",
        "register_agent",
        "create_project",
        "list_projects",
        "get_project",
        "set_project_archived",
        "backup_database",
    }
)
TERMINAL_RETRIES = frozenset({"end_session", "release_lease", "complete_request", "cancel_request"})

PRIORITIES = {"low", "normal", "high", "critical"}
STATUSES = {"open", "supported", "disputed", "rejected", "accepted", "superseded"}
POLARITIES = {"supports", "contradicts", "neutral"}
REPRODUCIBILITY = {"unreplicated", "replicated", "failed-replication", "not-applicable"}
RELATION_TYPES = {"supports", "contradicts", "refines", "depends-on", "duplicates", "related"}
ENTITY_TYPES = {"claim", "evidence", "request"}
SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{0,62}$")


def canonical(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def validate_uuid(value: Any, field: str) -> str:
    if not isinstance(value, str):
        fail("validation_error", f"{field} must be a UUID")
    try:
        return str(uuid.UUID(value))
    except ValueError as exc:
        raise ValueError(f"{field} must be a UUID") from exc


def required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        fail("validation_error", f"{field} must be nonblank text")
    if len(value.encode("utf-8")) > 65536:
        fail("validation_error", f"{field} exceeds 64 KiB")
    return value.strip()


def optional_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value.encode("utf-8")) > 65536:
        fail("validation_error", f"{field} must be text of at most 64 KiB")
    return value


def json_object(value: Any, field: str) -> str:
    if value is None:
        value = {}
    if not isinstance(value, dict):
        fail("validation_error", f"{field} must be a JSON object")
    try:
        encoded = canonical(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} is not valid JSON") from exc
    if len(encoded.encode("utf-8")) > 65536:
        fail("validation_error", f"{field} exceeds 64 KiB")
    return encoded


def int_range(value: Any, field: str, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        fail("validation_error", f"{field} must be an integer")
    if value < minimum or (maximum is not None and value > maximum):
        fail("validation_error", f"{field} is out of range")
    return value


def bounded_array(value: Any, field: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 200:
        fail("validation_error", f"{field} must be an array of at most 200 items")
    return value


def enum(value: Any, field: str, allowed: set[str]) -> str:
    if value not in allowed:
        fail("validation_error", f"{field} has an invalid value")
    return value


def validate_params(operation: str, params: Any) -> dict:
    if operation not in OPERATIONS:
        fail("not_found", "Unknown operation", operation=operation)
    if not isinstance(params, dict):
        fail("validation_error", "params must be an object")
    extra = set(params) - set(OPERATIONS[operation])
    if extra:
        fail("validation_error", "Unknown parameter", fields=sorted(extra))
    # The per-operation handler validates required fields and types.
    return params


def page_limit(params: dict, default: int = 50) -> int:
    return int_range(params.get("limit", default), "limit", 1, 200)


def encode_cursor(values: dict) -> str:
    return base64.urlsafe_b64encode(canonical(values).encode()).decode().rstrip("=")


def decode_cursor(value: Any, expected: dict) -> dict:
    if value is None:
        return {}
    if not isinstance(value, str) or len(value) > 4096:
        fail("validation_error", "Invalid cursor")
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        cursor = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid cursor") from exc
    if not isinstance(cursor, dict) or cursor.get("bind") != expected:
        fail("validation_error", "Cursor does not match this query")
    return cursor
