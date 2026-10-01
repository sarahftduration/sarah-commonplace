# v0.1 implementation verification

The [section 19 acceptance criteria](sarah-commonplace-SPEC.md#19-v01-acceptance-criteria)
were checked on 2026-10-01. The code and package are ready for an operator to
deploy; no host deployment or release publication was performed.

| Criterion | Result | Evidence |
| --- | --- | --- |
| 1. Fresh database, service, readiness | Passed | Clean wheel initialized a database; real loopback service answered `context` and readiness. |
| 2. Concurrent claims and search | Passed | Twelve-process suite created 60 claims per run and queried them; three consecutive runs passed. |
| 3. Supporting and contradicting evidence | Passed | Service behavior tests cover polarity and claim linkage. |
| 4. Request lease lifecycle | Passed | Expiry, renewal, release, completion, ownership, and terminal replay tests pass. |
| 5. Redundant replication | Passed | Twelve-process tests completed exactly three distinct contributors for a target of three; live Skill smoke completed two distinct contributors for a target of two. |
| 6. Activity and maintenance history | Passed | Ordered pagination, mutation events, and restore/initialization metadata are checked. |
| 7. FTS5 search | Passed | Literal punctuation, indexed candidates, filters, cursor binding, and rollback tests pass. |
| 8. References and tags | Passed | Core and search tests cover links, project isolation, and indexed search. |
| 9. Network-only CLI workflow | Passed | Clean installed CLI initialized and served a notebook; a Luna worker used CLI to start a session, lease, add evidence, and complete. |
| 10. MCP peer operations | Passed | Tool registry/schema tests, a real MCP/HTTP same-ID replay test, and a Luna worker's full MCP work sequence passed. |
| 11. Twelve-client races | Passed | Three consecutive process runs against one service passed, with no overbooking. |
| 12. Forced termination and retry | Passed | A committed response was lost, service was killed, and replay after restart returned the one stored result with integrity checks passing. The live smoke separately replayed a discarded response after restart. |
| 13. Quick start and multi-agent example | Passed | README and `examples/uw1-agent-config.md` include both. |
| 14. Skills delivered and live-verified | Passed | Both Skills validate, are packaged and discoverable, and were used by a supervisor and two distinct Luna worker sessions against one disposable service. |
| 15. Isolation, ownership, stale leases, activity pages | Passed | Focused tests and live smoke showed same-agent session isolation, expired renewal, full-request conflict, and cursor resume. |
| 16. Live backup and restore | Passed | Backup receipt/replay, crash between file publish and receipt, restore generation, integrity, and foreign keys are tested. |
| 17. Clean offline install and both Skill workflows | Passed | A clean wheel installed from cached dependencies with networking disabled, then ran the service and both live Skill workflows over loopback. |
| 18. Runtime/schema rejection | Passed | Unsupported SQLite, missing schema table, and migration failure rollback tests pass; startup checks schema and FTS5. |
| 19. Auth, protected transport, parity, continuity | Passed | Shared token/Host/Origin policy, finite retries, and session continuity tests pass. A real TLS client in a separate network and mount namespace read context with the database directory hidden. MCP worker identity later worked through CLI fallback after restart. |
| 20. Sole owner, shutdown, restart | Passed | A second service and offline migration are refused while the owner holds the lock; shutdown marks unready and drains a call; normal and forced restart checks pass. |

## Live supervisor and worker smoke run

The operator-approved disposable notebook used the clean installed wheel and
one service. A supervisor created project `skill-smoke`, claim 1, and a request
for two independent replications. The claim and worker evidence were explicitly
labeled **simulated**; no game was run and no gameplay observation is claimed.

- An MCP Luna worker kept session `44543afc-c0de-4608-b2e9-39fb440930c1`.
  Lease 1 expired, renewal returned `lease_expired`, and the worker ultimately
  completed lease 3 with evidence 1. A second session using that same agent ID
  was refused renewal of its lease with `lease_not_owned`.
- A CLI Luna worker kept session `d078570c-6628-4c31-96b6-17e90454102c` and
  completed lease 4 with evidence 2. Both evidence IDs were linked to their
  respective completions, and request 1 ended `completed` with a count of two
  distinct agents. While its last slot was reserved, another lease attempt
  returned `lease_conflict`.
- The supervisor inspected a snapshot and saved activity cursor 4. After a
  service restart, the same session resumed activity over six pages with no
  omitted matching event. An HTTP mutation sent without reading its response
  committed claim 2; replaying operation `cb615925-5416-40e0-88d3-455cd2779252`
  after restart returned that claim with one creation event. The MCP worker's
  original session also read the completed request through CLI fallback.
- The supervisor rejected the synthetic claim with a note explaining that the
  simulated evidence does not establish real game behavior.

The initial model-worker attempts were stopped by automatic approval review.
Sarah explicitly approved a new disposable run, which produced the evidence
above. No external notebook was changed.

## Checks, package, and limits

- 39 tests pass, including the real network concurrency and crash recovery
  suite. Its twelve-process race runs three consecutive times.
- Ruff lint and format checks pass on source, tests, and benchmark. Both Skill
  validators pass; local documentation links and contract operation names check.
- Python 3.14.7 and SQLite 3.53.1 with FTS5 were used. The tested Starlette
  and MCP SDK versions were 0.52.1 and 2.2.0.
- A wheel and source archive build. Both complete Skill directories are in the
  source archive and under `commonplace/skills` in the wheel. A second clean
  environment installed the wheel and locked dependencies from cache with
  `uv pip install --offline` after those dependencies were fetched.
- The [100,000-record loopback benchmark](benchmark-100k.json) contained 60,000
  claims, 30,000 evidence records, and 10,000 requests in a 230,723,584-byte
  database. Example searches returned in 24.5–143.2 ms; concurrent write p95
  was 21.0–21.2 ms across record kinds. This synthetic local run is
  informational, not a throughput guarantee. Remote latency, 50-worker load,
  and a million-record dataset were not measured. A remote mutation through
  the isolated TLS client and a long-running drain under a real signal were
  also not measured; the specified acceptance checks use the tests above.
