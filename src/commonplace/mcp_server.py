"""Official MCP SDK Streamable HTTP peer adapter."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from mcp.server import lowlevel
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.types import CallToolResult, TextContent, Tool
from starlette.applications import Starlette
from starlette.routing import Mount

from .auth import MAX_BODY_BYTES, AccessPolicy
from .errors import DomainError
from .models import DATABASE_WIDE, OPERATIONS, READ_OPERATIONS

_HTTP_STATUS = {
    "validation_error": 400,
    "api_version_mismatch": 400,
    "unauthenticated": 401,
    "forbidden": 403,
    "not_found": 404,
    "database_busy": 503,
    "service_unavailable": 503,
}


def _tool_name(operation: str) -> str:
    suffix = "lease_next" if operation == "lease_next_request" else operation
    return f"commonplace_{suffix}"


_PARAM_REQUIRED: dict[str, frozenset[str]] = {
    "start_session": frozenset({"session_id"}),
    "register_agent": frozenset({"id"}),
    "create_project": frozenset({"slug", "name"}),
    "get_project": frozenset({"slug"}),
    "set_project_archived": frozenset({"slug", "archived", "note"}),
    "create_claim": frozenset({"topic", "statement"}),
    "get_claim": frozenset({"id"}),
    "set_claim_status": frozenset({"id", "status", "note"}),
    "supersede_claim": frozenset({"old_id", "topic", "statement"}),
    "add_evidence": frozenset({"claim_id", "polarity", "method", "summary"}),
    "list_evidence": frozenset({"claim_id"}),
    "create_request": frozenset({"type", "title", "instructions"}),
    "get_request": frozenset({"id"}),
    "lease_request": frozenset({"request_id"}),
    "renew_lease": frozenset({"lease_id"}),
    "release_lease": frozenset({"lease_id"}),
    "complete_request": frozenset({"lease_id"}),
    "cancel_request": frozenset({"request_id", "note"}),
    "get_lease": frozenset({"lease_id"}),
    "relate_claims": frozenset({"from_claim_id", "to_claim_id", "type"}),
    "list_relationships": frozenset({"claim_id"}),
    "attach_reference": frozenset({"entity_type", "entity_id", "reference"}),
    "add_tags": frozenset({"entity_type", "entity_id", "tags"}),
}

_PARAM_TYPES: dict[str, dict[str, dict[str, Any]]] = {
    "context": {},
    "database_info": {},
    "start_session": {
        "session_id": {"type": "string", "format": "uuid"},
        "metadata": {"type": "object"},
    },
    "end_session": {},
    "register_agent": {
        "id": {"type": "string"},
        "display_name": {"type": ["string", "null"]},
        "kind": {"type": "string", "enum": ["human", "agent", "tool", "unknown"]},
        "role": {"type": ["string", "null"]},
        "metadata": {"type": "object"},
    },
    "create_project": {
        "slug": {"type": "string"},
        "name": {"type": "string"},
        "description": {"type": ["string", "null"]},
    },
    "list_projects": {
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        "cursor": {"type": ["string", "null"]},
    },
    "get_project": {"slug": {"type": "string"}},
    "set_project_archived": {
        "slug": {"type": "string"},
        "archived": {"type": "boolean"},
        "note": {"type": "string"},
    },
    "create_claim": {
        "topic": {"type": "string"},
        "statement": {"type": "string"},
        "rationale": {"type": ["string", "null"]},
        "confidence": {"type": ["string", "null"], "enum": ["tentative", "likely", "strong", None]},
        "priority": {"type": "string", "enum": ["low", "normal", "high", "critical"]},
        "tags": {"type": "array", "items": {"type": "string"}},
        "refs": {"type": "array", "items": {"type": "object"}},
    },
    "get_claim": {
        "id": {"type": "integer", "minimum": 1},
        "include_evidence": {"type": "boolean"},
        "include_relationships": {"type": "boolean"},
    },
    "search_claims": {
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        "cursor": {"type": ["string", "null"]},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "set_claim_status": {
        "id": {"type": "integer", "minimum": 1},
        "status": {
            "type": "string",
            "enum": ["open", "supported", "disputed", "rejected", "accepted", "superseded"],
        },
        "note": {"type": "string"},
    },
    "supersede_claim": {
        "old_id": {"type": "integer", "minimum": 1},
        "topic": {"type": "string"},
        "statement": {"type": "string"},
    },
    "add_evidence": {
        "claim_id": {"type": "integer", "minimum": 1},
        "polarity": {"type": "string", "enum": ["supports", "contradicts", "neutral"]},
        "method": {"type": "string"},
        "summary": {"type": "string"},
        "reproducibility": {
            "type": "string",
            "enum": ["unreplicated", "replicated", "failed-replication", "not-applicable"],
        },
        "refs": {"type": "array", "items": {"type": "object"}},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "list_evidence": {
        "claim_id": {"type": "integer", "minimum": 1},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        "cursor": {"type": ["string", "null"]},
    },
    "create_request": {
        "type": {"type": "string"},
        "title": {"type": "string"},
        "instructions": {"type": "string"},
        "claim_id": {"type": ["integer", "null"]},
        "priority": {"type": "string", "enum": ["low", "normal", "high", "critical"]},
        "desired_redundancy": {"type": "integer", "minimum": 1, "maximum": 50},
        "tags": {"type": "array", "items": {"type": "string"}},
        "refs": {"type": "array", "items": {"type": "object"}},
    },
    "get_request": {"id": {"type": "integer", "minimum": 1}},
    "search_requests": {
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        "cursor": {"type": ["string", "null"]},
        "tags": {"type": "array", "items": {"type": "string"}},
        "available_only": {"type": "boolean"},
    },
    "lease_request": {
        "request_id": {"type": "integer", "minimum": 1},
        "lease_seconds": {"type": "integer", "minimum": 1, "maximum": 86400},
    },
    "lease_next_request": {
        "lease_seconds": {"type": "integer", "minimum": 1, "maximum": 86400},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "renew_lease": {
        "lease_id": {"type": "integer", "minimum": 1},
        "lease_seconds": {"type": "integer", "minimum": 1, "maximum": 86400},
    },
    "release_lease": {
        "lease_id": {"type": "integer", "minimum": 1},
        "note": {"type": ["string", "null"]},
    },
    "complete_request": {
        "lease_id": {"type": "integer", "minimum": 1},
        "note": {"type": ["string", "null"]},
        "evidence_ids": {"type": "array", "items": {"type": "integer", "minimum": 1}},
        "resulting_claim_ids": {"type": "array", "items": {"type": "integer", "minimum": 1}},
    },
    "cancel_request": {"request_id": {"type": "integer", "minimum": 1}, "note": {"type": "string"}},
    "get_lease": {"lease_id": {"type": "integer", "minimum": 1}},
    "list_leases": {
        "request_id": {"type": ["integer", "null"]},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        "cursor": {"type": ["string", "null"]},
    },
    "relate_claims": {
        "from_claim_id": {"type": "integer", "minimum": 1},
        "to_claim_id": {"type": "integer", "minimum": 1},
        "type": {
            "type": "string",
            "enum": ["supports", "contradicts", "refines", "depends-on", "duplicates", "related"],
        },
    },
    "list_relationships": {
        "claim_id": {"type": "integer", "minimum": 1},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        "cursor": {"type": ["string", "null"]},
    },
    "search": {
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
        "cursor": {"type": ["string", "null"]},
        "tags": {"type": "array", "items": {"type": "string"}},
        "entity_types": {
            "type": "array",
            "items": {"type": "string", "enum": ["claim", "evidence", "request"]},
        },
    },
    "attach_reference": {
        "entity_id": {"type": "integer", "minimum": 1},
        "reference": {"type": "object"},
    },
    "add_tags": {
        "entity_id": {"type": "integer", "minimum": 1},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "recent_activity": {
        "since_seq": {"type": "integer", "minimum": 0},
        "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
        "types": {"type": "array", "items": {"type": "string"}},
    },
    "project_snapshot": {},
    "backup_database": {"name": {"type": ["string", "null"]}},
}

_PARAM_DEFAULTS: dict[str, dict[str, Any]] = {
    "list_projects": {"limit": 50},
    "search_claims": {"limit": 50},
    "list_evidence": {"limit": 50},
    "search_requests": {"limit": 50},
    "lease_request": {"lease_seconds": 900},
    "lease_next_request": {"lease_seconds": 900},
    "renew_lease": {"lease_seconds": 900},
    "list_leases": {"limit": 50},
    "list_relationships": {"limit": 50},
    "search": {"limit": 50},
    "recent_activity": {"since_seq": 0, "limit": 100},
    "create_claim": {"priority": "normal", "confidence": None, "tags": [], "refs": []},
    "create_request": {"priority": "normal", "desired_redundancy": 1, "tags": [], "refs": []},
    "add_evidence": {"reproducibility": "unreplicated", "tags": [], "refs": []},
    "get_claim": {"include_evidence": True, "include_relationships": True},
    "complete_request": {"evidence_ids": [], "resulting_claim_ids": []},
}


def _inferred_param_schema(name: str) -> dict[str, Any]:
    if name in {"tags", "types", "entity_types", "refs", "evidence_ids", "resulting_claim_ids"}:
        item: dict[str, Any] = {"type": "string"}
        if name in {"evidence_ids", "resulting_claim_ids"}:
            item = {"type": "integer", "minimum": 1}
        elif name == "entity_types":
            item = {"type": "string", "enum": ["claim", "evidence", "request"]}
        return {"type": "array", "items": item}
    if name in {"archived", "include_evidence", "include_relationships", "available_only"}:
        return {"type": "boolean"}
    if name in {"limit", "since_seq", "lease_seconds", "desired_redundancy"}:
        limits = {
            "limit": {"minimum": 1, "maximum": 200},
            "since_seq": {"minimum": 0},
            "lease_seconds": {"minimum": 1, "maximum": 86400},
            "desired_redundancy": {"minimum": 1, "maximum": 50},
        }
        return {"type": "integer", **limits[name]}
    if name in {
        "id",
        "old_id",
        "claim_id",
        "request_id",
        "lease_id",
        "from_claim_id",
        "to_claim_id",
        "entity_id",
        "replicates_evidence_id",
    }:
        if name == "id":
            return {"type": "string"}  # register_agent uses the shared field name
        return {"type": ["integer", "null"], "minimum": 1}
    if name in {"metadata", "reference"}:
        return {"type": "object"}
    if name == "entity_type":
        return {"type": "string", "enum": ["claim", "evidence", "request"]}
    if name == "priority":
        return {"type": ["string", "null"], "enum": ["low", "normal", "high", "critical", None]}
    if name == "status":
        return {
            "type": ["string", "null"],
            "enum": [
                "open",
                "supported",
                "disputed",
                "rejected",
                "accepted",
                "superseded",
                "leased",
                "completed",
                "cancelled",
                None,
            ],
        }
    if name == "confidence":
        return {"type": ["string", "null"], "enum": ["tentative", "likely", "strong", None]}
    if name == "polarity":
        return {"type": ["string", "null"], "enum": ["supports", "contradicts", "neutral", None]}
    if name in {
        "query",
        "topic",
        "status",
        "author",
        "ref_kind",
        "method",
        "polarity",
        "type",
        "priority",
        "note",
        "cursor",
        "slug",
        "name",
        "title",
        "instructions",
        "statement",
        "rationale",
        "procedure",
        "observation",
        "confidence",
        "role",
        "display_name",
        "agent_id",
        "kind",
    }:
        return {"type": ["string", "null"]}
    return {}


def _input_schema(operation: str) -> dict[str, Any]:
    params = OPERATIONS[operation]
    param_schemas = _PARAM_TYPES.get(operation, {})
    param_properties = {
        name: param_schemas.get(name, _inferred_param_schema(name)) for name in params
    }
    for name, default in _PARAM_DEFAULTS.get(operation, {}).items():
        param_properties[name] = {**param_properties[name], "default": default}
    required_params = sorted(_PARAM_REQUIRED.get(operation, frozenset()))
    for name in {"tags", "refs", "evidence_ids", "resulting_claim_ids"} & set(params):
        param_properties[name] = {**param_properties[name], "default": []}
    if operation == "context":
        required_context: list[str] = []
    elif operation == "start_session":
        required_context = ["notebook_id", "generation", "agent_id"]
    else:
        required_context = ["notebook_id", "generation", "agent_id", "session_id"]
        if operation not in DATABASE_WIDE:
            required_context.append("project")
    return {
        "type": "object",
        "properties": {
            "context": {
                "type": "object",
                "properties": {
                    "notebook_id": {"type": "string", "format": "uuid"},
                    "generation": {"type": "string", "format": "uuid"},
                    "project": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "session_id": {"type": "string", "format": "uuid"},
                },
                "required": required_context,
                "additionalProperties": False,
            },
            "params": {
                "type": "object",
                "properties": param_properties,
                "required": required_params,
                "additionalProperties": False,
            },
            "operation_id": {
                "type": "string" if operation not in READ_OPERATIONS else ["string", "null"],
                **({"format": "uuid"} if operation not in READ_OPERATIONS else {}),
            },
        },
        "required": ["context", "params"]
        + ([] if operation in READ_OPERATIONS else ["operation_id"]),
        "additionalProperties": False,
    }


def _error_status(exc: DomainError) -> int:
    return _HTTP_STATUS.get(exc.code, 403 if exc.code in {"project_archived"} else 500)


async def _protected_dispatch(
    manager: StreamableHTTPSessionManager,
    policy: AccessPolicy,
    scope: dict[str, Any],
    receive: Callable[[], Awaitable[dict[str, Any]]],
    send: Callable[[dict[str, Any]], Awaitable[None]],
) -> None:
    """Apply peer admission policy before handing a request to the MCP SDK."""
    try:
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        policy.check(headers)
    except DomainError as exc:
        body = json.dumps(
            {
                "ok": False,
                "data": None,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": False,
                    "details": exc.details or {},
                },
                "meta": {
                    "operation_id": None,
                    "notebook_id": None,
                    "generation": None,
                    "project": None,
                    "agent_id": None,
                    "session_id": None,
                },
            }
        ).encode()
        status = _error_status(exc)
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
        return

    # Buffer a bounded request body before SDK parsing. This also enforces the
    # same cap for chunked requests that omit Content-Length.
    body = bytearray()
    more = True
    while more:
        message = await receive()
        if message["type"] == "http.disconnect":
            return
        if message["type"] != "http.request":
            continue
        chunk = message.get("body", b"")
        more = bool(message.get("more_body", False))
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            details = {"limit_bytes": MAX_BODY_BYTES}
            payload = json.dumps(
                {
                    "ok": False,
                    "data": None,
                    "error": {
                        "code": "validation_error",
                        "message": "Request body exceeds size limit",
                        "retryable": False,
                        "details": details,
                    },
                    "meta": {
                        "operation_id": None,
                        "notebook_id": None,
                        "generation": None,
                        "project": None,
                        "agent_id": None,
                        "session_id": None,
                    },
                }
            ).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 413,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(payload)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": payload})
            return
        body.extend(chunk)

    sent = False

    async def replay_body() -> dict[str, Any]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": bytes(body), "more_body": False}

    await manager.handle_request(scope, replay_body, send)


def create_mcp_app(service: Any, policy: AccessPolicy) -> Starlette:
    """Return an MCP ASGI app suitable for ``Mount('/mcp', app=...)``.

    The returned Starlette app owns the SDK manager lifecycle. A parent ASGI
    application that mounts it must enter its ``router.lifespan_context`` as
    part of the root lifespan; Starlette does not propagate mounted lifespans.
    """

    async def list_tools(_: Any, __: Any) -> Any:
        from mcp.types import ListToolsResult

        return ListToolsResult(
            tools=[
                Tool(
                    name=_tool_name(operation),
                    description=f"Commonplace {operation} operation",
                    inputSchema=_input_schema(operation),
                )
                for operation in OPERATIONS
            ]
        )

    dispatch_slots = asyncio.Semaphore(16)

    async def call_tool(_: Any, request: Any) -> CallToolResult:
        name = request.name
        if not name.startswith("commonplace_"):
            raise ValueError(f"Unknown tool: {name}")
        suffix = name[len("commonplace_") :]
        operation = "lease_next_request" if suffix == "lease_next" else suffix
        if operation not in OPERATIONS or _tool_name(operation) != name:
            raise ValueError(f"Unknown tool: {name}")
        arguments = request.arguments or {}
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be an object")
        extra = set(arguments) - {"context", "params", "operation_id"}
        if extra:
            raise ValueError(f"Unknown tool argument: {sorted(extra)[0]}")
        context = arguments.get("context", {})
        params = arguments.get("params", {})
        operation_id = arguments.get("operation_id")
        if not isinstance(context, dict) or not isinstance(params, dict):
            raise ValueError("context and params must be objects")
        async with dispatch_slots:
            envelope = await asyncio.to_thread(
                service.call, operation, context, params, operation_id
            )
        result = CallToolResult(
            content=[TextContent(type="text", text=json.dumps(envelope, ensure_ascii=False))],
            structuredContent=envelope,
            isError=not bool(envelope.get("ok")),
        )
        return result

    server = lowlevel.Server(
        "sarah-commonplace",
        version="0.1",
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
    manager = StreamableHTTPSessionManager(
        app=server,
        json_response=True,
        stateless=True,
        max_request_body_size=MAX_BODY_BYTES,
    )

    async def dispatch(
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        await _protected_dispatch(manager, policy, scope, receive, send)

    @asynccontextmanager
    async def lifespan(_: Starlette):
        async with manager.run():
            yield

    return Starlette(routes=[Mount("/", app=dispatch)], lifespan=lifespan)
