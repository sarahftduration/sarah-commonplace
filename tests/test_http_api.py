import sys
from types import ModuleType

import anyio
import httpx
import pytest

from commonplace.auth import AccessPolicy
from commonplace.http_api import create_app

TOKEN = "x" * 40


@pytest.fixture(autouse=True)
def without_mcp_lifespan(monkeypatch):
    module = ModuleType("commonplace.mcp_server")
    module.create_mcp_app = lambda *_args: None
    monkeypatch.setitem(sys.modules, "commonplace.mcp_server", module)
    import commonplace.http_api as api

    async def direct_dispatch(function, *args):
        return function(*args)

    monkeypatch.setattr(api, "run_in_threadpool", direct_dispatch)


class FakeService:
    ready = True

    def __init__(self):
        self.calls = []

    def call(self, operation, context=None, params=None, operation_id=None):
        self.calls.append((operation, context, params, operation_id))
        return {
            "ok": True,
            "data": {"operation": operation},
            "error": None,
            "meta": {"operation_id": operation_id},
        }


def _requests(app, specs):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost"
        ) as client:
            return [await client.request(method, path, **kwargs) for method, path, kwargs in specs]

    return anyio.run(run)


def test_authenticated_dispatch_and_health_readiness():
    service = FakeService()
    app = create_app(service, AccessPolicy(TOKEN))
    health, ready, unauth, response = _requests(
        app,
        [
            ("GET", "/healthz", {}),
            ("GET", "/readyz", {}),
            ("POST", "/api/v1/operations/context", {"json": {}}),
            (
                "POST",
                "/api/v1/operations/context",
                {
                    "headers": {"Authorization": f"Bearer {TOKEN}"},
                    "json": {"context": {}, "params": {}, "operation_id": None},
                },
            ),
        ],
    )
    assert health.json()["status"] == "alive"
    assert ready.status_code == 200
    assert unauth.status_code == 401
    assert response.status_code == 200
    assert service.calls == [("context", {}, {}, None)]


def test_unsupported_api_version_is_explicit():
    app = create_app(FakeService(), AccessPolicy(TOKEN))
    (response,) = _requests(
        app,
        [
            (
                "POST",
                "/api/v2/operations/context",
                {"headers": {"Authorization": f"Bearer {TOKEN}"}, "json": {}},
            )
        ],
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "api_version_mismatch"


def test_host_origin_and_unknown_operation_errors():
    app = create_app(FakeService(), AccessPolicy(TOKEN))
    headers = {"Authorization": f"Bearer {TOKEN}"}
    denied, unknown = _requests(
        app,
        [
            (
                "POST",
                "/api/v1/operations/context",
                {"headers": {**headers, "Origin": "https://evil.test"}, "json": {}},
            ),
            ("POST", "/api/v1/operations/no_such_operation", {"headers": headers, "json": {}}),
        ],
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"
    assert unknown.status_code == 404


def test_malformed_and_oversized_body(monkeypatch):
    import commonplace.http_api as api

    monkeypatch.setattr(api, "MAX_BODY_BYTES", 8)
    app = create_app(FakeService(), AccessPolicy(TOKEN))
    headers = {"Authorization": f"Bearer {TOKEN}"}
    invalid, large = _requests(
        app,
        [
            ("POST", "/api/v1/operations/context", {"headers": headers, "content": b"{"}),
            ("POST", "/api/v1/operations/context", {"headers": headers, "content": b" " * 9}),
        ],
    )
    assert invalid.status_code == 400
    assert large.status_code == 413
    assert large.json()["error"]["details"] == {"limit_bytes": 8}
