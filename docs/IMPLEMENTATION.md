# v0.1 implementation plan

Status: ready to begin implementation after the 2026-10-01 network-service revision.
No application code or usable runtime Skills exist yet. This is a staged build
plan; the [specification](sarah-commonplace-SPEC.md) defines required behavior.

## Architecture and ownership

Commonplace is one small service with two peer interfaces: HTTP/JSON and MCP over
HTTP. Each adapter calls the same application service in-process; neither calls
the other public interface or duplicates domain rules. The CLI is a client of
HTTP/JSON. One service process owns SQLite on its host's local disk. Normal
clients carry an endpoint, access credential, project, agent, research session,
notebook ID, and restore generation; they never open the database.

Sol owns the service boundary, common operation schemas, lifecycle, storage,
leases, identity rules, and integration. Establish those contracts before Luna
builds either interface. Offline host maintenance is the only production path
outside the running daemon that may open storage, under its exclusive lock.

## Implementation team

Use Sol (`gpt-6-sol`, high reasoning) as the lead and Luna (`gpt-6-luna`, high
reasoning) for bounded subagent tasks. These exact models were available in the
reviewing Codex host. Select Sol when starting the implementation task and
explicitly select Luna for delegated work; repository prose does not switch the
running model. If a requested model is unavailable, report that limitation.

Start with at most two concurrent Luna tasks, increasing only for independent
work within the host's limits. Give each task its input contracts, owned files,
expected outputs, and acceptance checks. Wait for prerequisites before dispatch.
One agent owns each file at a time. The lead reviews and integrates the results
and commits/pushes under [AGENTS.md](../AGENTS.md). Subagents report changed files,
checks run, and unresolved issues.

Commonplace itself does not choose or invoke models. The
[official subagent guidance](https://learn.chatgpt.com/docs/agent-configuration/subagents)
describes explicit model selection and focused subagent roles.

## Milestones and gates

### 1. Establish service contracts and a runnable foundation — Sol

- Create packaging, locked dependencies, test harness, and the delivery layout.
  Use Python 3.11+, SQLite 3.51.3+ with FTS5, stdlib `sqlite3`, a compatible small
  ASGI framework/server, HTTP client, official MCP SDK, pytest, and a linter.
  Check the SQLite library loaded by the service's Python runtime. Client-only
  installation must work without that SQLite requirement or database access.
- Write `docs/contracts.md`: the shared operation registry, models, defaults,
  HTTP routes and status codes, corresponding MCP tools, CLI flags, error
  envelopes, context/bootstrap rules, limits, cursors, and event vocabulary.
  HTTP/JSON and MCP expose the same service operations, excluding offline host
  maintenance. Keep their distinct protocol framing explicit.
- Implement database initialization and versioning, notebook/generation metadata,
  project/agent/session setup, constraints, activity, and operation-ID storage.
  Explicit session start uses a caller-chosen UUID; no per-call implicit session.
- Establish `service.py`, shared dispatch/access policy, an exclusive instance
  lock, connection ownership, and `serve` startup/readiness/shutdown. Expose
  context/setup through the initial HTTP adapter as a runnable vertical slice.
- Implement explicit URL/credential handling, shared-token admission, and request
  size/Host/Origin checks. The trust model is one research group; asserted agent
  IDs are provenance, not separate authenticated accounts. Bind loopback by
  default; document/configure TLS or an encrypted tunnel for remote access.

Gate: a clean install initializes a database and starts one authenticated service;
context/session/project setup works over HTTP. Unsupported runtimes/schemas,
missing credentials/database, invalid contexts, and a second database owner fail
clearly. Health/readiness expose no notebook data. Publish the shared contracts
before delegating adapter work.

### 2. Implement notebook records and history — Sol with bounded Luna work

- Build claims, status/supersession, evidence/replication links, relationships,
  references/tags, project archival, activity pagination, and snapshot queries.
- Keep mutation, index changes, activity, and stored retry result transactional.
  Preserve the same operation name and canonical payload across adapters.
- Add endpoint/generation context validation. Reads and disconnects do not mutate
  research sessions. Artifact URIs retain their origin-host meaning; the service
  does not fetch or expose their contents.
- Delegate record API tests to Luna in assigned files after fixtures/contracts
  stabilize. Keep schema and shared dispatch changes under Sol's ownership.

Gate: lifecycle, immutable content, project isolation, provenance, duplicate retry,
bounded bundles, and activity pagination tests pass. Snapshot/polling cursors
agree under concurrent writes. No public operation needs direct client SQL.

### 3. Implement coordination and search — independent tracks after milestone 2

| Owner | Scope | Gate |
| --- | --- | --- |
| Sol | Request/lease transactions, result joins, cancellation, expiry, session closure | No overbooking; redundancy two needs two distinct completions; stale/foreign contexts fail; duplicate completion counts once. |
| Luna | Search and search-specific tests within the agreed schema | Records and attachments are discoverable; exact identifiers survive; filters and pagination are deterministic. |
| Luna, if useful | Independent concurrency/recovery tests using stable core/service interfaces | Exercise partial completion, reacquisition, cancellation, concurrent retries, disconnects, and crash recovery. |

Gate: Sol reviews integrated schema/index/transaction behavior. Use the injected
server clock for expiry tests, skewed client clocks to verify server authority,
and synchronized processes for races. A client disconnect or service restart
never closes a research session or implicitly renews/releases a lease. Retain
separate-connection storage tests, while service concurrency uses network clients.

### 4. Complete peer interfaces, CLI, and host operations — Luna tasks, Sol integration

- Assign `http_api.py`, `client.py`, CLI, and their tests to one bounded Luna task;
  assign `mcp_server.py` and MCP tests to another. Sol owns shared `service.py`,
  schemas, access checks, lifecycle, and transaction logic. Adapt scope or split
  sequential tasks if one assignment becomes too broad.
- Both adapters call the shared operation registry. MCP uses Streamable HTTP at
  `/mcp`; CLI uses `/api/v1/operations/...`. Test cross-interface parity for
  successes, errors, research context, and replay of the same operation ID.
- Implement finite client timeouts and bounded retries. Preserve operation ID,
  payload, and notebook/generation/session across attempts. On an unresolved
  response loss, return `outcome_unknown` with the original context for recovery.
  Never create a local notebook when the service is unavailable.
- Sol delivers live service backups, durable backup receipts/reconciliation, and
  offline init/migration/restore under the instance lock. Backup requests accept
  names within the configured directory, never arbitrary server paths. Restoring
  to a new destination validates data and generates a new restore generation.
- Keep authentication and network protocol sessions separate from research
  provenance. Each MCP call carries its own context even on a shared connection.
  CLI is the required fallback for hosts that cannot use the private MCP access
  profile; an optional stdio bridge may forward to the same service.

Gate: section 11 runs with CLI and real MCP clients against one service. A CLI
write is visible through MCP and vice versa. A lost committed response can be
reconciled through the other interface without another mutation. Test auth,
limits, version errors, service unavailability, live backup crash windows,
restore-generation rejection, and lock-protected maintenance. Client code has no
research storage path or database fallback.

### 5. Deliver deployment guidance and agent Skills — Luna authoring, Sol verification

- Use the Skill creation workflow for `.agents/skills/commonplace-worker/` and
  `.agents/skills/commonplace-supervisor/`, with `SKILL.md` and needed references.
  Follow section 23's workflows and recovery requirements.
- Write `docs/agent-setup.md`, `docs/deployment.md`, the README quick start, and
  the multi-agent example against real installed commands and tool schemas.
  Cover one service installation, token provisioning, URL/identity setup, explicit
  session start, retaining notebook/generation context, and Skill discovery in
  a separate consuming repository. No secret values belong in Skills/examples.
- Document loopback startup without Internet access and one supported remote
  TLS/tunnel deployment. Explain health/readiness, shutdown/restart, host backup
  locations, offline restore/migration, cursor reset, and origin-host artifacts.
- Validate metadata, relative links, triggers, and command/tool examples; include
  the complete Skill directories in release artifacts.
- Run one supervisor and two workers with distinct research identities against
  one service, mixing CLI/MCP and then testing CLI fallback. Include lease
  conflict/expiry, lost-response replay, reconnect, and cursor resume after a
  service restart. Test generation changes as a separate restore scenario.

Gate: a fresh consuming repository discovers and uses both Skills without this
conversation. The local quick start needs no Internet after installation. Record
actual agent smoke-run evidence; prose lint alone is insufficient. At least one
client operates outside the service's network namespace or on another host, over
an approved protected connection and without the database file mounted locally.

### 6. Release verification — Sol

- Run tests, lint/format checks, package build, clean server/client installation,
  HTTP/MCP/CLI contract checks, and Skill checks.
- Run the twelve-client process concurrency and recovery suite against one
  service at least three consecutive times. Cover interrupted clients, service
  termination before/after commit, and concurrent same-ID retries across both
  interfaces. Verify integrity/foreign keys after recovery; there must be no
  overbooking, duplicate committed mutations, or stray owners.
- Verify credential/Origin/Host/TLS checks, missing-service errors, explicit
  research-session continuity, correct readiness/shutdown, and sole database
  ownership. Test migration failure rollback and every released upgrade path;
  for v0.1, fresh creation and fault injection are the applicable migration gates.
- Run an informational 100,000-record search/write benchmark via loopback. Record
  host/runtime, dataset mix, timings, and limitations. Timings are not a v0.1
  release threshold; the million-record scale target remains pre-v1. Remote
  latency and 50-worker throughput require separate measurements.
- Record all section 19 acceptance results and limitations in release notes.
  Update the README status only after corresponding milestones pass.

Gate: required acceptance checks pass and artifacts/Skills are packaged. The lead
reviews the final diff and commits/pushes under standing permission. Deploying to
an actual host or publishing a release is separate from pushing project changes.

## Scope kept small

The existing domain rules remain: completed contributions plus reservations
satisfy redundancy; lease ownership includes research session; expiry derives
status without a sweeper; evidence stays append-only; polling never skips a page.
The service now owns those rules, storage, clock, and retry records.

There is one database owner, two peer protocol adapters, a CLI client, and host
maintenance. Keep distributed replication, multiple service replicas, offline
client replicas, push notifications, web UI, artifact hosting, per-user accounts,
and OAuth discovery out of v0.1. A static service credential and protected remote
transport are required; a model provider or cloud service is not.

The team may choose internal helper/table names. Observable behavior changes
must update the specification and contracts before parallel interface work proceeds.
