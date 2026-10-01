# v0.1 alpha verification

The implementation is an alpha. These results were recorded on 2026-10-01 in
this repository. They are evidence for the [section 19 acceptance criteria](sarah-commonplace-SPEC.md#19-v01-acceptance-criteria),
not a claim that every release gate has passed.

| Criterion | Result | Evidence or remaining work |
| --- | --- | --- |
| 1. Fresh database, service, readiness | Passed | Clean wheel initialized a database; real loopback service answered `context` and readiness. |
| 2. Concurrent claims and search | Passed | Twelve-process suite created 60 claims per run and queried them; 3 consecutive runs passed. |
| 3. Supporting and contradicting evidence | Passed | Service behavior tests cover polarity and claim linkage. |
| 4. Request lease lifecycle | Passed | Expiry, renewal, release, completion, ownership, and terminal replay tests pass. |
| 5. Redundant replication | Passed | Twelve-process tests completed exactly 3 distinct contributors for a target of 3. |
| 6. Activity and maintenance history | Passed | Ordered pagination, mutation events, and restore/initialization metadata are checked. |
| 7. FTS5 search | Passed | Literal punctuation, indexed candidates, filters, cursor binding, and rollback tests pass. |
| 8. References and tags | Passed | Core and search tests cover links, project isolation, and indexed search. |
| 9. Network-only CLI workflow | Passed for exercised paths | CLI dispatch tests and clean-wheel database init, session start, project, claim, and request creation succeeded over loopback. The complete worker sequence remains under criterion 14. |
| 10. MCP peer operations | Passed for exercised paths | Tool registry/schema tests and a real MCP/HTTP same-ID replay test pass. |
| 11. Twelve-client races | Passed | Three consecutive process runs against one service passed, with no overbooking. |
| 12. Forced termination and retry | Passed | A committed response was lost, service was killed, and replay after restart returned the one stored result with integrity checks passing. |
| 13. Quick start and multi-agent example | Passed | README and `examples/uw1-agent-config.md` include both. |
| 14. Skills delivered and live-verified | **Partial** | Both Skills validate and are packaged; their examples name shipped operations. Two model workers inspected the live service. Runtime auto-review rejected the remaining multi-write live worker smoke run, so no evidence/lease completion by those workers is claimed. |
| 15. Isolation, ownership, stale leases, activity pages | Passed | Focused service, search, and concurrency tests pass. |
| 16. Live backup and restore | Passed | Backup receipt/replay, crash between file publish and receipt, restore generation, integrity, and foreign keys are tested. |
| 17. Clean offline install and both Skill workflows | **Partial** | A clean wheel installed from cache with networking disabled after dependency resolution, and the installed CLI initialized and served a fresh notebook. Both complete live Skill workflows remain unverified for the same auto-review reason as criterion 14. |
| 18. Runtime/schema rejection | Passed | Unsupported SQLite and migration failure rollback tests pass; startup checks schema and FTS5. |
| 19. Auth, protected transport, parity, continuity | Passed for exercised paths | Shared token/Host/Origin policy, finite retries, and session continuity tests pass. A real TLS client in a separate network and mount namespace read context with the database directory hidden. A remote mutation through that path has not been exercised. |
| 20. Sole owner, shutdown, restart | Passed for exercised paths | A second service and offline migration are refused while the owner holds the lock; restart after forced termination and normal shutdown pass. In-flight drain uses a 30-second default; long-running drain under a real signal has not been measured. |

## Checks and package

- Python 3.14.7, SQLite 3.53.1 with FTS5, Starlette 0.52.1, MCP SDK 2.2.0.
- `ruff check` and `ruff format --check` pass on source, tests, and benchmark.
- Focused tests and the real network concurrency suite pass. The twelve-process
  race is itself repeated three times per suite run.
- Hatch built a wheel and source archive. Both complete Skill directories are in
  the source archive and under `commonplace/skills` in the wheel.
- A second clean environment installed the wheel and locked dependencies from
  cache with `uv pip install --offline` after those dependencies were fetched.
- The 100,000-record loopback benchmark produced
  [machine-readable results](benchmark-100k.json): 60,000 claims, 30,000
  evidence records, and 10,000 requests in a 230,723,584-byte database.
  Example searches returned in 24.5–143.2 ms. Concurrent write p95 was
  21.0–21.2 ms across the three record kinds. This synthetic local run is
  informational, not a throughput guarantee. Remote latency, 50-worker load,
  and a million-record dataset were not measured.

## Remaining release gate

Run one supervisor and two worker agents through the full live Skill workflow
on a disposable or authorized service, including CLI and MCP writes, CLI
fallback, lease expiry/conflict, reconnect, and cursor resume after restart.
The attempted model-worker writes were blocked by the runtime's automatic
approval review because they would mutate a shared live service. The review
required explicit user approval to continue; no bypass was attempted. Until
that run passes, this remains v0.1 alpha rather than a completed v0.1 release.
