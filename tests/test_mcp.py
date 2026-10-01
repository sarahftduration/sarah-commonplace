"""Protocol-level coverage for the MCP Streamable HTTP peer adapter."""

from __future__ import annotations

import unittest

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from commonplace.auth import AccessPolicy
from commonplace.http_api import create_app
from commonplace.models import OPERATIONS


class StubService:
    ready = True

    def __init__(self):
        self.calls = []

    def call(self, operation, context=None, params=None, operation_id=None):
        self.calls.append((operation, context, params, operation_id))
        if operation == "context":
            return {
                "ok": True,
                "data": {"service": "commonplace"},
                "error": None,
                "meta": {"operation_id": None},
            }
        return {
            "ok": False,
            "data": None,
            "error": {
                "code": "validation_error",
                "message": "bad input",
                "retryable": False,
                "details": {},
            },
            "meta": {"operation_id": operation_id},
        }


class MCPHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.service = StubService()
        self.app = create_app(self.service, AccessPolicy("test-token"))
        self.client = httpx2.AsyncClient(
            headers={"Authorization": "Bearer test-token"},
            base_url="http://localhost",
            transport=httpx2.ASGITransport(app=self.app),
        )

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_parent_mount_lists_contract_tools_at_exact_mcp_path(self):
        async with self.app.router.lifespan_context(self.app):
            async with streamable_http_client("http://localhost/mcp", http_client=self.client) as (
                read_stream,
                write_stream,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    names = {tool.name for tool in listed.tools}
                    expected = {
                        f"commonplace_{'lease_next' if op == 'lease_next_request' else op}"
                        for op in OPERATIONS
                    }
                    self.assertEqual(names, expected)
                    create_claim = next(
                        tool for tool in listed.tools if tool.name == "commonplace_create_claim"
                    )
                    self.assertEqual(
                        create_claim.input_schema["properties"]["params"]["required"],
                        ["statement", "topic"],
                    )
                    self.assertEqual(
                        create_claim.input_schema["properties"]["params"]["properties"]["priority"][
                            "default"
                        ],
                        "normal",
                    )

    async def test_body_limit_is_enforced_before_sdk_dispatch(self):
        async with self.app.router.lifespan_context(self.app):
            response = await self.client.post("/mcp", content=b"x" * (4 * 1024 * 1024 + 1))
        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["error"]["details"]["limit_bytes"], 4 * 1024 * 1024)
        self.assertEqual(self.service.calls, [])

    async def test_missing_credential_is_rejected_before_protocol_dispatch(self):
        client = httpx2.AsyncClient(
            base_url="http://localhost", transport=httpx2.ASGITransport(app=self.app)
        )
        async with self.app.router.lifespan_context(self.app):
            response = await client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "unauthenticated")
        self.assertEqual(self.service.calls, [])
        await client.aclose()


if __name__ == "__main__":
    unittest.main()
