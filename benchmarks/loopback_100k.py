"""Informational 100,000-record benchmark through the real loopback API."""

from __future__ import annotations

import argparse
import json
import os
import platform
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from commonplace.client import CommonplaceClient
from commonplace.db import initialize


def port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def percentiles(values: list[float]) -> dict:
    ordered = sorted(values)
    return {
        key: round(ordered[min(len(ordered) - 1, int((len(ordered) - 1) * fraction))], 3)
        for key, fraction in (("p50_ms", 0.5), ("p95_ms", 0.95), ("p99_ms", 0.99))
    }


def benchmark(records: int, workers: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="commonplace-benchmark-") as directory:
        root = Path(directory)
        database = root / "benchmark.db"
        token_file = root / "token"
        token_file.write_text(secrets.token_urlsafe(48))
        token_file.chmod(0o600)
        info = initialize(database)
        selected_port = port()
        url = f"http://127.0.0.1:{selected_port}"
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
                "--port",
                str(selected_port),
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(200):
                try:
                    if httpx.get(url + "/readyz", timeout=0.2, trust_env=False).status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if process.poll() is not None:
                    raise RuntimeError("Service exited before readiness")
                time.sleep(0.05)
            else:
                raise RuntimeError("Service did not become ready")

            base = {key: info[key] for key in ("notebook_id", "generation")}
            contexts = []
            with CommonplaceClient(url, str(token_file)) as client:
                for index in range(workers):
                    session = str(uuid.uuid4())
                    context = {**base, "agent_id": f"benchmark-{index}"}
                    result = client.call(
                        "start_session", context=context, params={"session_id": session}
                    )
                    if not result["ok"]:
                        raise RuntimeError(result)
                    context["session_id"] = session
                    contexts.append(context)
                created = client.call(
                    "create_project",
                    context=contexts[0],
                    params={
                        "slug": "benchmark",
                        "name": "Synthetic benchmark",
                    },
                )
                if not created["ok"]:
                    raise RuntimeError(created)
            for context in contexts:
                context["project"] = "benchmark"

            claims = round(records * 0.6)
            evidence = round(records * 0.3)
            requests = records - claims - evidence

            def phase(kind: str, count: int, claim_ids: list[int] | None = None):
                def worker(index: int):
                    context = contexts[index]
                    latencies = []
                    ids = []
                    with CommonplaceClient(url, str(token_file)) as client:
                        for number in range(index, count, workers):
                            if kind == "claim":
                                params = {
                                    "topic": "benchmark",
                                    "statement": f"claim-index-{number} instruction trace "
                                    + ("needlemarker " if number % 1000 == 0 else ""),
                                }
                                operation = "create_claim"
                            elif kind == "evidence":
                                params = {
                                    "claim_id": claim_ids[number % len(claim_ids)],
                                    "polarity": "supports",
                                    "method": "synthetic-trace",
                                    "summary": f"trace observation {number}",
                                }
                                operation = "add_evidence"
                            else:
                                params = {
                                    "type": "inspect",
                                    "title": f"request-index-{number}",
                                    "instructions": "Inspect synthetic trace",
                                }
                                operation = "create_request"
                            start = time.perf_counter()
                            result = client.call(operation, context=context, params=params)
                            latencies.append((time.perf_counter() - start) * 1000)
                            if not result["ok"]:
                                raise RuntimeError(result)
                            if kind == "claim":
                                ids.append(result["data"]["id"])
                    return ids, latencies

                started = time.perf_counter()
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    outputs = list(pool.map(worker, range(workers)))
                return (
                    [id_ for ids, _ in outputs for id_ in ids],
                    [value for _, times in outputs for value in times],
                    round(time.perf_counter() - started, 3),
                )

            claim_ids, claim_times, claim_seconds = phase("claim", claims)
            _, evidence_times, evidence_seconds = phase("evidence", evidence, claim_ids)
            _, request_times, request_seconds = phase("request", requests)
            query_times = {}
            with CommonplaceClient(url, str(token_file)) as client:
                for query in ("needlemarker", "claim-index-50000", "trace observation 42"):
                    started = time.perf_counter()
                    result = client.call(
                        "search",
                        context=contexts[0],
                        params={
                            "query": query,
                            "limit": 50,
                        },
                    )
                    if not result["ok"]:
                        raise RuntimeError(result)
                    query_times[query] = {
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                        "returned": len(result["data"]["items"]),
                    }
            return {
                "host": platform.platform(),
                "python": platform.python_version(),
                "sqlite": sqlite3.sqlite_version,
                "records": {"claims": claims, "evidence": evidence, "requests": requests},
                "workers": workers,
                "write": {
                    "claims": {"elapsed_s": claim_seconds, **percentiles(claim_times)},
                    "evidence": {"elapsed_s": evidence_seconds, **percentiles(evidence_times)},
                    "requests": {"elapsed_s": request_seconds, **percentiles(request_times)},
                },
                "search": query_times,
                "database_bytes": database.stat().st_size,
                "limits": "Loopback only; synthetic text and no remote network latency",
            }
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--records", type=int, default=100_000)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.records < 10 or not 1 <= args.workers <= 16:
        parser.error("Use at least 10 records and 1–16 workers")
    result = benchmark(args.records, args.workers)
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n")
    print(rendered)
