"""Real loopback process races against one authoritative service."""

from __future__ import annotations

import asyncio
import json
import multiprocessing as mp
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import httpx

from commonplace.client import CommonplaceClient


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def running_service(tmp_path: Path, database: Path, token_file: Path):
    port = _port()
    url = f"http://127.0.0.1:{port}"
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "commonplace.cli",
            "serve",
            "--db",
            str(database),
            "--token-file",
            str(token_file),
            "--backup-dir",
            str(tmp_path / "backups"),
            "--port",
            str(port),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise AssertionError(f"Service exited early: {process.stderr.read()}")
            try:
                if httpx.get(url + "/readyz", timeout=0.2, trust_env=False).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
        else:
            raise AssertionError("Service never became ready")
        yield url, process
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        process.stderr.close()


def _worker(url, token_file, base, request_id, index, barrier, queue):
    try:
        with CommonplaceClient(url, token_file) as client:
            session = str(uuid.uuid4())
            agent = f"worker-{index}"
            start = client.call(
                "start_session", context={**base, "agent_id": agent}, params={"session_id": session}
            )
            assert start["ok"], start
            context = {**base, "agent_id": agent, "session_id": session, "project": "race"}
            barrier.wait(timeout=30)
            lease = client.call(
                "lease_request",
                context=context,
                params={
                    "request_id": request_id,
                    "lease_seconds": 30,
                },
            )
            if lease["ok"]:
                completed = client.call(
                    "complete_request",
                    context=context,
                    params={
                        "lease_id": lease["data"]["id"],
                        "note": f"Result {index}",
                    },
                )
                assert completed["ok"], completed
                won = True
            else:
                assert lease["error"]["code"] in {"lease_conflict", "request_closed"}, lease
                won = False
            for number in range(5):
                result = client.call(
                    "create_claim",
                    context=context,
                    params={
                        "topic": "race",
                        "statement": f"Worker {index} statement {number}",
                    },
                )
                assert result["ok"], result
            queue.put((index, won, None))
    except BaseException as exc:
        queue.put((index, False, repr(exc)))


def test_twelve_processes_three_consecutive_runs(tmp_path):
    from commonplace.db import initialize

    token_file = tmp_path / "token"
    token_file.write_text("t" * 48)
    token_file.chmod(0o600)
    for run in range(3):
        database = tmp_path / f"run-{run}.db"
        info = initialize(database)
        base = {key: info[key] for key in ("notebook_id", "generation")}
        with running_service(tmp_path, database, token_file) as (url, _):
            with CommonplaceClient(url, str(token_file)) as client:
                session = str(uuid.uuid4())
                setup = {**base, "agent_id": "supervisor"}
                assert client.call(
                    "start_session",
                    context=setup,
                    params={
                        "session_id": session,
                    },
                )["ok"]
                setup["session_id"] = session
                assert client.call(
                    "create_project",
                    context=setup,
                    params={
                        "slug": "race",
                        "name": "Race",
                    },
                )["ok"]
                setup["project"] = "race"
                request = client.call(
                    "create_request",
                    context=setup,
                    params={
                        "type": "test",
                        "title": "Race",
                        "instructions": "One of three",
                        "desired_redundancy": 3,
                    },
                )
                assert request["ok"], request
                context = mp.get_context("spawn")
                barrier = context.Barrier(12)
                queue = context.Queue()
                processes = [
                    context.Process(
                        target=_worker,
                        args=(
                            url,
                            str(token_file),
                            base,
                            request["data"]["id"],
                            index,
                            barrier,
                            queue,
                        ),
                    )
                    for index in range(12)
                ]
                for process in processes:
                    process.start()
                results = [queue.get(timeout=60) for _ in processes]
                for process in processes:
                    process.join(timeout=10)
                    assert process.exitcode == 0
                assert not [error for _, _, error in results if error], results
                assert sum(won for _, won, _ in results) == 3
                final = client.call(
                    "get_request", context=setup, params={"id": request["data"]["id"]}
                )
                assert final["ok"] and final["data"]["status"] == "completed"
                assert final["data"]["completed_count"] == 3
                claims = client.call(
                    "search_claims", context=setup, params={"topic": "race", "limit": 100}
                )
                assert claims["ok"] and len(claims["data"]["items"]) == 60
        from commonplace.db import connect

        connection = connect(database)
        try:
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert connection.execute("PRAGMA foreign_key_check").fetchone() is None
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM leases WHERE completed_at IS NOT NULL"
                ).fetchone()[0]
                == 3
            )
            assert (
                connection.execute(
                    "SELECT COUNT(DISTINCT agent_id) FROM leases WHERE completed_at IS NOT NULL"
                ).fetchone()[0]
                == 3
            )
        finally:
            connection.close()


def test_lost_committed_response_and_forced_restart(tmp_path):
    from commonplace.db import connect, initialize

    database = tmp_path / "recovery.db"
    info = initialize(database)
    token_file = tmp_path / "token"
    token_file.write_text("r" * 48)
    token_file.chmod(0o600)
    base = {key: info[key] for key in ("notebook_id", "generation")}
    session = str(uuid.uuid4())
    operation_id = str(uuid.uuid4())
    params = {"topic": "recovery", "statement": "Committed before response loss"}
    with running_service(tmp_path, database, token_file) as (url, process):
        with CommonplaceClient(url, str(token_file)) as client:
            context = {**base, "agent_id": "recovery-worker"}
            assert client.call("start_session", context=context, params={"session_id": session})[
                "ok"
            ]
            context["session_id"] = session
            assert client.call(
                "create_project", context=context, params={"slug": "recovery", "name": "Recovery"}
            )["ok"]
            context["project"] = "recovery"
            wire = json.dumps(
                {"context": context, "params": params, "operation_id": operation_id}
            ).encode()
            host, port = "127.0.0.1", int(url.rsplit(":", 1)[1])
            request = (
                f"POST /api/v1/operations/create_claim HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                f"Authorization: Bearer {'r' * 48}\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(wire)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode() + wire
            with socket.create_connection((host, port), timeout=5) as sock:
                sock.sendall(request)
                assert sock.recv(1)  # response began; committed body is intentionally discarded
            found = client.call(
                "search_claims", context=context, params={"query": "Committed before response loss"}
            )
            assert found["ok"] and len(found["data"]["items"]) == 1
            claim_id = found["data"]["items"][0]["id"]
            process.kill()
            process.wait(timeout=5)
    with running_service(tmp_path, database, token_file) as (url, _):
        with CommonplaceClient(url, str(token_file)) as client:
            replay = client.call(
                "create_claim", context=context, params=params, operation_id=operation_id
            )
            assert replay["ok"] and replay["data"]["id"] == claim_id
            activity = client.call("recent_activity", context=context, params={"since_seq": 0})
            assert activity["ok"]
            assert (
                len(
                    [
                        event
                        for event in activity["data"]["events"]
                        if event["event_type"] == "claim.created"
                    ]
                )
                == 1
            )
    connection = connect(database)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchone() is None
    finally:
        connection.close()


def test_real_mcp_and_http_share_operation_replay(tmp_path):
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    from commonplace.db import initialize

    database = tmp_path / "peer.db"
    info = initialize(database)
    token_file = tmp_path / "token"
    token_file.write_text("p" * 48)
    token_file.chmod(0o600)
    base = {key: info[key] for key in ("notebook_id", "generation")}
    with running_service(tmp_path, database, token_file) as (url, _):
        with CommonplaceClient(url, str(token_file)) as client:
            session_id = str(uuid.uuid4())
            setup = {**base, "agent_id": "peer-worker"}
            assert client.call("start_session", context=setup, params={"session_id": session_id})[
                "ok"
            ]
            setup["session_id"] = session_id
            assert client.call(
                "create_project", context=setup, params={"slug": "peer", "name": "Peer"}
            )["ok"]
            setup["project"] = "peer"
            operation_id = str(uuid.uuid4())
            params = {"topic": "peer", "statement": "MCP and HTTP share a core"}

            async def mcp_round_trip():
                async with httpx2.AsyncClient(
                    headers={"Authorization": "Bearer " + "p" * 48}, timeout=10
                ) as transport:
                    async with streamable_http_client(
                        url + "/mcp", http_client=transport
                    ) as streams:
                        async with ClientSession(*streams) as mcp:
                            await mcp.initialize()
                            written = await mcp.call_tool(
                                "commonplace_create_claim",
                                {
                                    "context": setup,
                                    "params": params,
                                    "operation_id": operation_id,
                                },
                            )
                            assert not written.is_error, written
                            claim_id = written.structured_content["data"]["id"]
                            fetched = await mcp.call_tool(
                                "commonplace_get_claim",
                                {
                                    "context": setup,
                                    "params": {"id": claim_id},
                                },
                            )
                            assert not fetched.is_error
                            assert fetched.structured_content["data"]["id"] == claim_id
                            changed = await mcp.call_tool(
                                "commonplace_create_claim",
                                {
                                    "context": setup,
                                    "params": {"topic": "peer", "statement": "Changed payload"},
                                    "operation_id": operation_id,
                                },
                            )
                            assert changed.is_error
                            assert (
                                changed.structured_content["error"]["code"]
                                == "idempotency_conflict"
                            )
                            return claim_id

            claim_id = asyncio.run(mcp_round_trip())
            replay = client.call(
                "create_claim", context=setup, params=params, operation_id=operation_id
            )
            assert replay["ok"] and replay["data"]["id"] == claim_id
            activity = client.call("recent_activity", context=setup, params={"since_seq": 0})
            assert activity["ok"]
            assert (
                len(
                    [
                        event
                        for event in activity["data"]["events"]
                        if event["event_type"] == "claim.created"
                    ]
                )
                == 1
            )
