# v0.1 implementation plan

Status: ready to begin implementation after the 2026-10-01 specification review.
No application code or usable runtime Skills exist yet. This is a staged build
plan, not a claim of release readiness. The
[specification](sarah-commonplace-SPEC.md) is the behavior contract.

## Implementation team

Use Sol (`gpt-6-sol`, high reasoning) as the lead and Luna (`gpt-6-luna`, high
reasoning) for bounded subagent tasks. These exact models are available in the
reviewing Codex host. Select Sol when starting the implementation task and
explicitly select Luna for delegated work; repository prose does not switch the
running model. If a requested model is unavailable, report that limitation.

Sol owns schema and public interfaces, transactional invariants, migrations,
integration, and final verification. Luna can implement adapters, isolated search
work, focused tests, and Skills after the relevant contracts exist. Start with at
most two concurrent Luna tasks, increasing only for independent work and within
the host's limits. Give each task its input contracts, exact owned files, expected
outputs, and acceptance checks; wait for prerequisites before dispatching.

In a shared checkout, only one agent owns a file at a time. The lead integrates
and performs commits/pushes under [AGENTS.md](../AGENTS.md). Subagents report the
files changed, checks run, and unresolved issues. Commonplace remains independent
of the agents/model provider used to build or operate it. The
[official subagent guidance](https://learn.chatgpt.com/docs/agent-configuration/subagents)
describes explicit model selection and focused subagent roles.

## Milestones and gates

### 1. Establish the executable contracts — Sol

- Create packaging, locked dependencies, the test harness, and the specified
  source layout. Use Python 3.11+, a compatible SQLite runtime (3.51.3+ with FTS5),
  stdlib `sqlite3`, a small CLI, the official MCP SDK, pytest, and a formatter/linter.
- Write `docs/contracts.md`: exact model fields/defaults, core signatures, CLI
  flags, MCP input/output schemas, validation limits, error-to-exit mapping,
  ordering/cursors, and the event vocabulary. Implement shared models/errors
  against section 22 before separate agents build adapters.
- Implement explicit database init/info, migrations, agent/session/project
  setup, context resolution, schema constraints, activity, and operation-ID
  deduplication. Flag/environment precedence is explicit flag, then environment;
  there is no implicit database/project for research commands.
- Enforce same-project links, session ownership, and runtime capability checks.
  Runtime provisioning and checking Python's loaded SQLite library are part of
  this milestone; a recent Python version alone is insufficient.

Gate: a clean environment installs the package; database initialization and
rollback work; missing/unsupported database, schema, or runtime fails clearly;
project/session integrity tests pass. Publish the contracts for later tasks.

### 2. Implement notebook records and history — Sol with bounded Luna work

- Build claims, status/supersession, evidence/replication links, relationships,
  references/tags, project archival, activity pagination, and snapshot queries.
- Keep mutation, index changes, activity, and retry result in one transaction.
- Delegate record API tests to Luna in explicitly assigned test files once the
  shared fixtures and contracts are stable.

Gate: claim/evidence lifecycles, immutable content, cross-project rejection,
session provenance, duplicate retry, bounded bundles, and filtered activity
pagination are verified. Snapshot and polling cursors agree under concurrent
writes. No API depends on direct SQL edits by a caller.

### 3. Implement coordination and search — independent tracks after milestone 2

| Owner | Scope | Gate |
| --- | --- | --- |
| Sol | Request/lease transactions, completion result joins, cancellation, expiry, session closure | Races cannot overbook; two distinct completions are required for redundancy two; stale/foreign owners fail; duplicate completion counts once. |
| Luna | Search implementation and search-specific tests within the agreed schema; propose any schema change to Sol | Claims/evidence/requests and attached tags/references are discoverable; exact identifier punctuation survives; filters and pagination are deterministic. |
| Luna, if useful | Independent race/crash tests, using stable core interfaces and separate files | Reproduce expiry, partial completion, reacquisition, cancellation and retry races with separate connections/processes; check integrity after worker termination. |

Gate: both tracks pass and Sol reviews the integrated schema/index/transaction
behavior before the adapters are finalized. Use an injected clock for expiry
unit tests and process synchronization for concurrency tests instead of fragile
timing-only sleeps.

### 4. Implement CLI and MCP — Luna tasks, Sol integration

- Assign CLI and its tests to one Luna; assign stdio MCP and its tests to another.
  Both consume the same core functions and result/error contracts.
- Deliver all section 6 operations via the documented CLI; expose the required
  section 9 research tools and context inspection through MCP.
- Include explicit project/session setup, database info/migration/backup, safe
  JSON output, bounded responses, and clear failures. Keep MCP stdout clean.
- Sol checks semantic parity and actual stdio server behavior, including context
  provenance. CLI fallback must preserve distinct subagent identities when the
  host shares a parent's MCP connection.

Gate: the section 11 scenario passes through CLI and through real MCP client/server
calls. Validate malformed input, conflicts, unavailable work, and pagination.
Test backup creation during writes, restore it, and verify integrity/foreign keys.

### 5. Deliver and exercise agent Skills — Luna authoring, Sol verification

- Use the Skill creation workflow to author `.agents/skills/commonplace-worker/`
  and `.agents/skills/commonplace-supervisor/` with `SKILL.md` files and any needed
  references. Follow section 23's scope and recovery behavior.
- Write `docs/agent-setup.md`, the README quick start, and the multi-agent example
  against real installed commands and tool schemas. Explain discovery in a
  separate research project, release-archive contents, and MCP/CLI configuration.
- Validate skill metadata, relative links, triggers, and command/tool examples;
  bundle the complete skill directories with the release artifact.
- Run an actual supervisor/two-worker smoke scenario with separate identities,
  explicit replication, lease renewal/expiry/conflict, and activity resume. Verify
  both MCP setup and the shared-connection CLI fallback. Record outcomes.

Gate: a fresh consuming repository can discover and use both Skills without
knowledge from the development conversation. A user can complete the five-minute
quick start. The tool works offline after installation. Do not call a prose-only
skill review a successful runtime smoke test.

### 6. Release verification — Sol

- Run the full tests, lint/format checks, package build, clean installation,
  CLI/MCP contract checks, and Skill checks.
- Run the twelve-process concurrency and crash-recovery suite at least three
  times. Inspect database integrity and foreign keys afterward. For the initial
  release test creation and migration failure rollback; for later releases also
  test every released upgrade path.
- Run an informational 100,000-record search/write smoke benchmark and record the
  machine, loaded SQLite version, timings, dataset mix, and limitations. The
  million-record scale target in section 16 remains a pre-v1 validation, not a
  claim already proved by v0.1. Benchmark timings are not a v0.1 release threshold.
  Neither 50-worker operation nor performance targets are guaranteed without
  measurements. Repeated concurrency runs check for intermittent integrity and
  overbooking failures, rather than imposing a latency target.
- Record all section 19 acceptance results and remaining limitations in release
  notes. Update the README status only after the corresponding milestone passes.

Gate: all required v0.1 acceptance checks pass, runtime artifacts and Skills are
packaged, and the lead reviews the final diff. Commit and push the completed work
using standing permission. Creating a public release or deploying a service is
a separate action from pushing commits.

## Decisions settled by the readiness review

The draft's main implementation ambiguities are now resolved in section 22:
redundancy counts completed contributions plus reservations; lease ownership
includes session; expiry derives status without a sweeper; project boundaries
are enforced in storage; content corrections append/supersede; polling cannot
skip a limited page; retries have operation IDs; search includes evidence and
literal identifiers; initialization and backup are explicit; each worker has its
own provenance. Section 23 makes two operational Skills mandatory deliverables.

The implementation team may choose internal table/helper names and revise
nonessential organization. Changes to observable semantics must update the
specification and contracts before parallel adapter work proceeds.
