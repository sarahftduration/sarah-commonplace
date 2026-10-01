"""HTTP/JSON peer adapter for the authoritative service."""

from __future__ import annotations

import json
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Match, Mount, Route, get_route_path

from .auth import MAX_BODY_BYTES, AccessPolicy
from .errors import DomainError
from .models import OPERATIONS

log = logging.getLogger(__name__)


class _BodyTooLarge(Exception):
    pass


class _ExactASGIMount(BaseRoute):
    """Forward one exact URL path into a mounted ASGI app without redirecting."""

    def __init__(self, path: str, app: Any):
        self.path = path
        self.app = app
        self.name = None

    def matches(self, scope: dict[str, Any]):
        if scope["type"] == "http" and get_route_path(scope) == self.path:
            return Match.FULL, {"endpoint": self.app}
        return Match.NONE, {}

    async def handle(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        child_scope = dict(scope)
        child_scope["root_path"] = scope.get("root_path", "") + self.path
        child_scope["path"] = "/"
        child_scope["raw_path"] = b"/"
        await self.app(child_scope, receive, send)


_STATUS = {
    "validation_error": 400,
    "api_version_mismatch": 400,
    "unauthenticated": 401,
    "forbidden": 403,
    "project_archived": 403,
    "not_found": 404,
    "session_mismatch": 409,
    "session_closed": 409,
    "lease_conflict": 409,
    "lease_not_owned": 409,
    "lease_expired": 409,
    "request_closed": 409,
    "conflict": 409,
    "idempotency_conflict": 409,
    "notebook_mismatch": 409,
    "generation_mismatch": 409,
    "database_busy": 503,
    "service_unavailable": 503,
    "schema_mismatch": 503,
    "unsupported_runtime": 503,
    "internal_error": 500,
}


def _error(
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
    operation_id: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "data": None,
        "error": {
            "code": code,
            "message": message,
            "retryable": code in {"database_busy", "service_unavailable"},
            "details": details or {},
        },
        "meta": {
            "operation_id": operation_id,
            "notebook_id": None,
            "generation": None,
            "project": None,
            "agent_id": None,
            "session_id": None,
        },
    }


def _status(envelope: dict[str, Any]) -> int:
    error = envelope.get("error")
    return 200 if not error else _STATUS.get(error.get("code"), 500)


async def _read_body(request: Request) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise _BodyTooLarge
        chunks.append(chunk)
    return b"".join(chunks)


def create_app(service: Any, policy: AccessPolicy) -> Starlette:
    """Create an ASGI app; all domain calls go through ``service.call``."""

    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "status": "alive"})

    async def readiness(_: Request) -> JSONResponse:
        ready = bool(service.ready)
        return JSONResponse(
            {"ok": ready, "status": "ready" if ready else "not_ready"},
            status_code=200 if ready else 503,
        )

    async def dispatch(request: Request) -> JSONResponse:
        operation = request.path_params["operation"]
        operation_id = None
        try:
            if request.path_params.get("version") != "v1":
                envelope = _error(
                    "api_version_mismatch",
                    "Unsupported application API version",
                    details={"supported_versions": ["v1"]},
                )
                return JSONResponse(envelope, status_code=400)
            # Reject unknown names before consuming or dispatching a body.
            if operation not in OPERATIONS:
                envelope = _error(
                    "not_found", "Unknown operation", details={"operation": operation}
                )
                return JSONResponse(envelope, status_code=404)
            raw = await _read_body(request)
            try:
                payload = json.loads(raw or b"{}")
            except (UnicodeDecodeError, json.JSONDecodeError):
                envelope = _error("validation_error", "Request body must be valid JSON")
                return JSONResponse(envelope, status_code=400)
            if not isinstance(payload, dict):
                envelope = _error("validation_error", "Request body must be a JSON object")
                return JSONResponse(envelope, status_code=400)
            extra = set(payload) - {"context", "params", "operation_id"}
            if extra:
                envelope = _error(
                    "validation_error", "Unknown request field", details={"fields": sorted(extra)}
                )
                return JSONResponse(envelope, status_code=400)
            context = payload.get("context", {})
            params = payload.get("params", {})
            operation_id = payload.get("operation_id")
            if not isinstance(context, dict) or not isinstance(params, dict):
                envelope = _error(
                    "validation_error",
                    "context and params must be JSON objects",
                    operation_id=operation_id if isinstance(operation_id, str) else None,
                )
                return JSONResponse(envelope, status_code=400)
            envelope = await run_in_threadpool(
                service.call, operation, context, params, operation_id
            )
            return JSONResponse(envelope, status_code=_status(envelope))
        except _BodyTooLarge:
            envelope = _error(
                "validation_error",
                "Request body exceeds size limit",
                details={"limit_bytes": MAX_BODY_BYTES},
                operation_id=operation_id if isinstance(operation_id, str) else None,
            )
            return JSONResponse(envelope, status_code=413)
        except DomainError as exc:
            envelope = _error(
                exc.code,
                exc.message,
                details=exc.details,
                operation_id=operation_id if isinstance(operation_id, str) else None,
            )
            return JSONResponse(envelope, status_code=_STATUS.get(exc.code, 500))
        except Exception:
            log.exception("Unexpected HTTP adapter failure")
            envelope = _error(
                "internal_error",
                "Internal service error",
                operation_id=operation_id if isinstance(operation_id, str) else None,
            )
            return JSONResponse(envelope, status_code=500)

    routes = [
        Route("/healthz", health, methods=["GET"]),
        Route("/readyz", readiness, methods=["GET"]),
        Route("/api/{version:str}/operations/{operation:str}", dispatch, methods=["POST"]),
    ]
    try:
        from .mcp_server import create_mcp_app
    except ImportError:
        create_mcp_app = None
    mcp_app = create_mcp_app(service, policy) if create_mcp_app else None
    if mcp_app is not None:
        routes.append(_ExactASGIMount("/mcp", mcp_app))
        routes.append(Mount("/mcp", app=mcp_app))

    @asynccontextmanager
    async def lifespan(_: Starlette):
        async with AsyncExitStack() as stack:
            if mcp_app is not None:
                await stack.enter_async_context(mcp_app.router.lifespan_context(mcp_app))
            yield

    async def admission(request: Request, call_next: Any):
        path = request.url.path
        is_probe = request.method == "GET" and path in {"/healthz", "/readyz"}
        is_mcp = mcp_app is not None and (path == "/mcp" or path.startswith("/mcp/"))
        if not is_probe and not is_mcp:
            try:
                policy.check(dict(request.headers))
            except DomainError as exc:
                envelope = _error(exc.code, exc.message, details=exc.details)
                return JSONResponse(envelope, status_code=_STATUS.get(exc.code, 500))
        return await call_next(request)

    return Starlette(
        routes=routes,
        lifespan=lifespan if mcp_app is not None else None,
        middleware=[Middleware(BaseHTTPMiddleware, dispatch=admission)],
    )
