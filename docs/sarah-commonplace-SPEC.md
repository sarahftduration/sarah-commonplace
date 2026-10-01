# Sarah Commonplace

**Status:** Draft specification for v0.1  
**Purpose:** Shared research notebook and coordination layer for cooperating AI agents and humans.

## 1. Summary

Sarah Commonplace is a small, local-first shared notebook for teams of autonomous or semi-autonomous agents. It is designed for investigative work where agents form hypotheses, collect evidence, request replication, contradict one another, and coordinate experiments without forcing all collaboration through a single chat transcript.

The initial motivating use case is reverse engineering an old game with many agents operating independent debugger/emulator instances. The design must remain project-agnostic enough to support other research tasks such as binary analysis, software archaeology, debugging, experimental OS work, hardware experiments, or codebase investigations.

Commonplace is not an agent framework. It does not schedule models, decide what agents should think, or provide long-term autobiographical memory. It is a shared evidence and work-coordination substrate.

The core design is intentionally boring:

- SQLite database
- WAL mode for concurrent readers/writers
- append-oriented evidence and activity history
- a small domain model: claims, evidence, requests, references, relationships, agents, activity
- CLI for humans and scripts
- MCP server for agents
- pull-based activity cursors instead of a complex message broker

The intended scale for v0.1 is roughly 1-50 cooperating agents and up to millions of notebook records, not high-frequency telemetry. Large traces, screenshots, save states, binaries, and other bulky artifacts stay outside the database and are referenced from it.

---

## 2. Goals

### 2.1 Primary goals

Commonplace SHALL:

1. Let an agent publish a concise hypothesis or observation as a **claim**.
2. Let any agent attach **supporting, contradicting, or neutral evidence** to a claim.
3. Let agents create **requests** for replication, falsification, tracing, inspection, comparison, or other work.
4. Let agents atomically claim requests so several workers do not accidentally perform the same task unless duplication is desired.
5. Preserve provenance: who wrote what, when, and in which agent/session.
6. Preserve history rather than silently overwriting prior conclusions.
7. Make recent changes cheap to poll so agents can coordinate asynchronously.
8. Provide full-text search plus structured filtering.
9. Permit references to external artifacts such as trace files, screenshots, commits, debugger locations, save states, URLs, or source files.
10. Work without Internet access or cloud services.
11. Be straightforward enough that a human can inspect the SQLite database directly when necessary.
12. Remain useful when only one human and one agent are using it.

### 2.2 Secondary goals

Commonplace SHOULD:

- be installable with minimal dependencies;
- have deterministic, machine-readable output modes;
- make it easy for a supervising agent to identify disputed claims, unreplicated findings, idle work, and newly important discoveries;
- support multiple projects in one database, although one-database-per-project is acceptable operationally;
- make backups trivial: copy one database plus any referenced artifact directory;
- remain transport-independent internally so MCP is an adapter, not the core architecture.

---

## 3. Non-goals

For v0.1, Commonplace is NOT:

- a chat system;
- a general vector-memory product;
- a replacement for Git, Ghidra, issue trackers, or trace databases;
- a telemetry store for instruction-level traces;
- an orchestration engine that spawns or kills agents;
- a consensus algorithm;
- an authority that decides whether a claim is true;
- a distributed database across untrusted hosts;
- a binary artifact store.

A 700-million-row instruction trace belongs in the recorder/analysis system. Commonplace should contain a reference such as “trace X, rows Y-Z support claim 184,” not the trace itself.

---

## 4. Design principles

### 4.1 Evidence over chatter

The durable unit of collaboration is a claim, evidence item, request, or relationship, not an unstructured conversation.

### 4.2 Append rather than erase

Evidence is immutable after creation except for narrowly defined metadata corrections. If an observation proves wrong, add contradicting evidence or supersede the claim. Do not rewrite history.

### 4.3 Provisional knowledge is normal

The system must make it cheap to say “probably,” “needs replication,” and “this was wrong.” Reverse engineering progresses through useful guesses, not only finalized proofs.

### 4.4 Humans and agents are peers in provenance

A human researcher and an AI worker are both authors. The data model should not privilege either.

### 4.5 Local-first and inspectable

The complete logical state should be comprehensible with `sqlite3 commonplace.db`.

### 4.6 Coordination without a broker

v0.1 should use database transactions, leases, and a monotonically increasing activity sequence. A message queue can be added later if experience proves it necessary.

---

## 5. Core domain model

## 5.1 Project

A project is a namespace for research.

Fields:

- `id` integer primary key
- `slug` unique text, e.g. `uw1`
- `name` text
- `description` text nullable
- `created_at`
- `archived_at` nullable

A database MAY contain multiple projects. Every substantive record belongs to exactly one project.

## 5.2 Agent

Represents a human, model worker, supervisor, or tool identity.

Fields:

- `id` text primary key, e.g. `luna-07`, `sarah`, `sol-supervisor`
- `display_name`
- `kind`: `human | agent | tool | unknown`
- `role` free text nullable
- `metadata_json`
- `created_at`
- `last_seen_at`

Agent identity SHOULD be configured when starting the CLI/MCP process, preferably through `--agent` or `COMMONPLACE_AGENT_ID`.

## 5.3 Session

A session represents one run of an agent and prevents different incarnations of `luna-07` from becoming indistinguishable.

Fields:

- `id` UUID/text primary key
- `agent_id`
- `started_at`
- `ended_at` nullable
- `metadata_json`

Typical metadata may contain model name, host, emulator instance, git commit, or task label.

## 5.4 Claim

A claim is a concise proposition worth preserving and testing.

Examples:

- “The word at `19A4:02D6` is current player HP.”
- “Routine `1C82:047A` applies melee damage.”
- “Overlay 7 is loaded after entering the automap.”
- “This function is a memcpy wrapper, not object lookup.”

Fields:

- `id` integer primary key
- `project_id`
- `author_agent_id`
- `author_session_id` nullable
- `topic` short text, e.g. `inventory`, `combat`, `overlay`
- `statement` text
- `rationale` text nullable
- `confidence`: `tentative | likely | strong` nullable
- `status`: `open | supported | disputed | rejected | accepted | superseded`
- `priority`: `low | normal | high | critical`
- `created_at`
- `updated_at`
- `superseded_by_claim_id` nullable

Rules:

- `statement` is immutable after creation in normal operation.
- Meaningful correction creates a new claim and marks the old claim `superseded`.
- `status=accepted` means “accepted by this research team/workflow,” not mathematical truth.
- Confidence is explicitly subjective and must not substitute for evidence.

## 5.5 Evidence

Evidence records an observation relevant to a claim.

Fields:

- `id` integer primary key
- `project_id`
- `claim_id`
- `author_agent_id`
- `author_session_id` nullable
- `polarity`: `supports | contradicts | neutral`
- `method`: short text, e.g. `watchpoint`, `single-step`, `static-analysis`, `trace-query`, `memory-edit`, `replication`
- `summary` text
- `procedure` text nullable
- `observation` text nullable
- `reproducibility`: `unreplicated | replicated | failed-replication | not-applicable`
- `created_at`

Evidence is append-only.

Examples:

- supports: “Write watchpoint fired at `1C82:047A` immediately after rat attack; value changed 23 -> 20.”
- contradicts: “Changing `19A4:02D6` modifies mana display, not HP.”
- neutral: “Address changes during both damage and healing; semantic meaning remains uncertain.”

## 5.6 Request

A request is a unit of research work another agent can perform.

Fields:

- `id` integer primary key
- `project_id`
- `created_by_agent_id`
- `created_by_session_id` nullable
- `claim_id` nullable
- `type`: free but conventionally one of `replicate | falsify | trace | inspect | compare | locate | explain | test | other`
- `title` short text
- `instructions` text
- `priority`: `low | normal | high | critical`
- `status`: `open | leased | completed | cancelled`
- `desired_redundancy` integer default 1
- `created_at`
- `completed_at` nullable

Requests should support intentional independent replication. `desired_redundancy=3` means up to three different workers may lease the request concurrently.

## 5.7 Request lease

A lease is an atomic, expiring assignment of a request to an agent.

Fields:

- `id` integer primary key
- `request_id`
- `agent_id`
- `session_id` nullable
- `leased_at`
- `expires_at`
- `released_at` nullable
- `completion_note` nullable

Rules:

- A worker obtains a lease in a single SQLite transaction.
- Active lease count may not exceed `desired_redundancy`.
- Expired leases do not block new workers.
- An agent may renew a lease.
- Completion may optionally attach evidence IDs or create a new claim.

## 5.8 Relationship

Relationships connect claims to other claims.

Fields:

- `id`
- `project_id`
- `from_claim_id`
- `to_claim_id`
- `type`: `supports | contradicts | refines | depends-on | duplicates | related | supersedes`
- `author_agent_id`
- `created_at`
- `note` nullable

This allows the notebook to become a lightweight knowledge graph without forcing graph-database infrastructure.

## 5.9 Reference

References point to artifacts or locations outside Commonplace.

Fields:

- `id`
- `project_id`
- `kind`
- `label` nullable
- `uri` nullable
- `value` nullable
- `metadata_json`
- `created_by_agent_id`
- `created_at`

References may be attached to claims, evidence, and requests through join tables.

Recommended `kind` values include:

- `memory-address`
- `code-address`
- `symbol`
- `file`
- `source-location`
- `trace`
- `screenshot`
- `save-state`
- `git-commit`
- `url`
- `debugger-session`
- `other`

Examples:

```json
{
  "kind": "memory-address",
  "value": "19A4:02D6",
  "metadata": {
    "address_space": "x86-real-mode",
    "segment": 6564,
    "offset": 726,
    "linear": 105766
  }
}
```

```json
{
  "kind": "trace",
  "uri": "file:///lab/traces/run-042/",
  "metadata": {
    "instruction_start": 18342001,
    "instruction_end": 18342177
  }
}
```

Commonplace MUST NOT require understanding every reference kind. Unknown kinds remain valid opaque references.

## 5.10 Tag

Claims, evidence, and requests MAY have tags. Tags are normalized lowercase text and support loose organization beyond a single topic.

Examples: `inventory`, `player-state`, `needs-watchpoint`, `overlay-7`, `high-value`.

## 5.11 Activity

Every mutation produces an activity record with a monotonically increasing integer sequence.

Fields:

- `seq` integer primary key
- `project_id`
- `timestamp`
- `actor_agent_id`
- `actor_session_id` nullable
- `event_type`
- `entity_type`
- `entity_id`
- `summary`
- `payload_json` nullable

Examples:

- `claim.created`
- `evidence.added`
- `claim.status_changed`
- `request.created`
- `request.leased`
- `request.completed`
- `relationship.created`

`seq` is the synchronization cursor for polling clients.

---

## 6. Core operations

The core library SHALL expose operations equivalent to the following. MCP and CLI names may differ cosmetically but should preserve semantics.

### Claims

```text
create_claim(project, topic, statement, rationale?, confidence?, priority?, tags?, refs?) -> claim
get_claim(id, include_evidence=true, include_relationships=true) -> claim_bundle
search_claims(query?, topic?, status?, tags?, author?, ref_kind?, limit?, cursor?) -> results
set_claim_status(id, status, note?) -> claim
supersede_claim(old_id, new_claim_fields...) -> new_claim
```

### Evidence

```text
add_evidence(claim_id, polarity, method, summary, procedure?, observation?, reproducibility?, refs?, tags?) -> evidence
list_evidence(claim_id, polarity?, method?) -> evidence[]
```

### Requests

```text
create_request(type, title, instructions, claim_id?, priority?, desired_redundancy?, tags?, refs?) -> request
search_requests(status?, type?, priority?, tags?, unleased_only?, limit?) -> request[]
lease_request(request_id, lease_seconds=900) -> lease | conflict
lease_next_request(filters..., lease_seconds=900) -> lease | none
renew_lease(lease_id, lease_seconds=900) -> lease
release_lease(lease_id, note?)
complete_request(lease_id, note?, evidence_ids?, resulting_claim_ids?)
```

`lease_next_request` is important for autonomous workers. Selection SHOULD prioritize priority first, then oldest request, while respecting redundancy and excluding requests already actively leased by the same agent.

### Relationships

```text
relate_claims(from_claim_id, to_claim_id, type, note?) -> relationship
```

### Activity

```text
recent_activity(project, since_seq=0, limit=100, types?) -> {events, next_seq}
```

Clients store `next_seq` and poll periodically.

### Summary / supervisor views

```text
project_snapshot(project) -> summary
```

The snapshot SHOULD include:

- counts of open/supported/disputed claims;
- high-priority open requests;
- requests with expired leases;
- recently active agents;
- recently created claims;
- claims with contradicting evidence;
- accepted claims lacking independent replication;
- high-priority claims with no evidence;
- newest activity sequence.

This is designed for a supervising agent that periodically redirects a swarm.

---

## 7. Search

SQLite FTS5 SHALL index at least:

- claim statement and rationale;
- evidence summary/procedure/observation;
- request title/instructions;
- reference label/value;
- tags.

Search results SHOULD rank exact identifiers and structured filters above fuzzy textual relevance when both are present.

Address-like strings such as `19A4:02D6`, `0x0277FF`, symbol names, and commit hashes must remain searchable verbatim.

Structured reference metadata may later support address-range queries. v0.1 does not need a universal address algebra.

---

## 8. Concurrency and transactions

SQLite SHALL be configured with:

```sql
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;
```

Recommended additional settings may be chosen after testing.

Rules:

- individual notebook mutations should use short transactions;
- no network or model call may occur while holding a write transaction;
- request leasing must use an atomic transaction;
- activity insertion occurs in the same transaction as the mutation it records;
- schema migrations must be explicit and versioned;
- the system should tolerate a worker process dying while holding no database-level lock beyond SQLite's normal transaction lifetime.

With ~12 agents and human-rate notebook traffic, SQLite WAL should provide ample concurrency.

---

## 9. MCP interface

The initial MCP server is the primary agent-facing interface.

Suggested tool names:

```text
commonplace_create_claim
commonplace_get_claim
commonplace_search_claims
commonplace_set_claim_status
commonplace_add_evidence
commonplace_create_request
commonplace_search_requests
commonplace_lease_request
commonplace_lease_next
commonplace_renew_lease
commonplace_release_lease
commonplace_complete_request
commonplace_relate_claims
commonplace_recent_activity
commonplace_project_snapshot
```

The MCP process should be configured with:

```text
--db PATH
--project PROJECT_SLUG
--agent AGENT_ID
--session SESSION_ID   # optional; autogenerated if absent
```

Environment equivalents:

```text
COMMONPLACE_DB
COMMONPLACE_PROJECT
COMMONPLACE_AGENT_ID
COMMONPLACE_SESSION_ID
```

Every MCP tool result SHOULD be concise JSON with stable field names. Human prose can be generated by the calling model.

The MCP layer MUST NOT hide database errors behind vague messages. Conflicts such as “request already fully leased” should be explicit machine-readable outcomes.

---

## 10. CLI

A human-facing CLI should mirror the core API.

Examples:

```bash
commonplace claim create \
  --topic combat \
  --confidence likely \
  "Word at 19A4:02D6 is player HP"

commonplace evidence add 184 \
  --supports \
  --method watchpoint \
  "Rat attack changed value 23 -> 20; writer 1C82:047A"

commonplace request create \
  --type replicate \
  --claim 184 \
  --redundancy 2 \
  "Independently verify HP address using damage and healing"

commonplace request next --lease 15m
commonplace recent --since 920
commonplace search "19A4:02D6"
commonplace snapshot
```

CLI output modes:

- default human-readable
- `--json`
- optionally `--jsonl` for stream-like commands

---

## 11. Example multi-agent workflow

1. `luna-03` searches memory while changing player HP.
2. It creates claim 184: “Word at `19A4:02D6` is player HP,” confidence `tentative`.
3. It adds evidence from observed value changes.
4. It creates a replication request with `desired_redundancy=2`.
5. `luna-07` and `luna-09` independently lease the request.
6. `luna-07` sets a write watchpoint, takes damage, and adds supporting evidence identifying a writer routine.
7. `luna-09` edits the value directly, observes the status panel, then heals and adds supporting evidence.
8. A supervisor sees claim 184 has independent support and changes status to `supported`.
9. Another agent later discovers the word is actually “current vitality” with semantics slightly different from HP. It creates claim 233 and supersedes claim 184 rather than rewriting it.
10. The full research trail remains queryable.

---

## 12. Repository layout

Suggested initial layout:

```text
sarah-commonplace/
  README.md
  SPEC.md
  LICENSE
  pyproject.toml
  src/
    commonplace/
      __init__.py
      db.py
      schema.py
      migrations/
      models.py
      core.py
      search.py
      cli.py
      mcp_server.py
  tests/
    test_claims.py
    test_evidence.py
    test_requests.py
    test_leases.py
    test_activity.py
    test_search.py
    test_concurrency.py
  examples/
    uw1-agent-config.md
```

A Python implementation is recommended for v0.1 because SQLite, MCP integration, testing, and small-tool iteration are all straightforward. The storage format and semantics should remain language-neutral.

---

## 13. Schema management

Use a small integer schema version stored in a metadata table.

Requirements:

- every release that changes schema provides an upgrade migration;
- migrations run transactionally when SQLite permits;
- migrations are tested against at least the previous released schema;
- the CLI provides `commonplace db info` and `commonplace db migrate`;
- automatic migration MAY be offered but should not silently modify a shared database without an explicit configuration choice.

---

## 14. Reliability and auditability

Commonplace should favor durable, understandable failure modes.

Requirements:

- write operations either commit completely or not at all;
- all foreign keys are enforced;
- every mutation has activity provenance;
- timestamps are stored in UTC ISO-8601 or integer Unix time consistently;
- malformed JSON metadata is rejected before insertion;
- lease expiry is evaluated from timestamps, not background timers;
- a crashed agent leaves, at worst, an expiring lease;
- backups can be produced using SQLite's online backup API or `VACUUM INTO` rather than copying a live WAL database naively.

Optional later feature: a hash chain over activity rows for tamper evidence. Not required for v0.1.

---

## 15. Security model

v0.1 assumes a trusted local research environment.

- No authentication is required for local stdio MCP operation.
- If a future HTTP transport listens beyond loopback, authentication becomes mandatory.
- Artifact references are data, not instructions; the server must not automatically fetch arbitrary URLs or execute referenced files.
- File references should not imply permission to read those files.
- SQL is never accepted directly through MCP tools.
- Agent-supplied JSON metadata is size-limited.

---

## 16. Performance targets

These are sanity targets, not hard real-time guarantees.

On a normal desktop with a local SSD:

- create claim/evidence/request: typically < 50 ms
- lease request: typically < 50 ms
- recent activity query for 100 rows: typically < 50 ms
- full-text search over 1 million notebook records: interactive, ideally < 500 ms for normal queries
- 12 simultaneous agents performing human/model-rate writes without persistent `SQLITE_BUSY` failures

The database should be tested with at least 1 million synthetic records before calling v1 stable.

---

## 17. Testing requirements

### Unit tests

- claim lifecycle
- immutable evidence behavior
- status transitions
- relationships
- reference attachment
- FTS indexing
- activity generation

### Concurrency tests

- 12+ processes repeatedly creating records
- multiple workers racing to lease one request
- `desired_redundancy > 1`
- lease expiry and reacquisition
- worker crash simulation
- WAL readers while writers are active

### Migration tests

- fresh database creation
- upgrade from every released schema version
- migration failure leaves database recoverable

### MCP contract tests

- every tool validates inputs
- stable JSON result shape
- conflicts represented explicitly
- configured agent/session provenance appears correctly

### End-to-end scenario

Automate the example claim -> replication request -> two leases -> evidence -> supported status workflow.

---

## 18. Logging

Operational logging should be separate from notebook activity.

The server/CLI may log:

- startup configuration excluding secrets
- schema version
- database busy/retry events
- failed transactions
- MCP validation errors

It should not duplicate every notebook record into logs by default.

---

## 19. v0.1 acceptance criteria

v0.1 is complete when:

1. A fresh database can be initialized.
2. Multiple agents can create/search claims concurrently.
3. Evidence can support or contradict claims.
4. Requests can be created, atomically leased, renewed, released, and completed.
5. Redundant replication requests work correctly.
6. Every mutation appears in ordered activity history.
7. FTS5 search works across claims, evidence, and requests.
8. Claims/evidence/requests can carry external references and tags.
9. CLI supports the complete core workflow.
10. MCP server supports the complete core workflow.
11. Twelve-process concurrency tests pass repeatedly.
12. The database survives forced worker termination without logical corruption.
13. README includes a five-minute quick start and one multi-agent example.

---

## 20. Deferred features

Do not block v0.1 on these:

- push notifications / WebSockets
- semantic/vector search
- embeddings
- automatic claim merging
- model-generated summaries stored as canonical truth
- web UI
- graph visualization
- remote multi-host replication
- user authentication/authorization
- binary artifact storage
- debugger integration
- orchestration/scheduling
- automatic confidence scoring
- automatic consensus

The first likely post-v0.1 additions are:

1. lightweight web dashboard;
2. structured debugger/address reference helpers;
3. optional push notification transport;
4. supervisor queries such as “important unreplicated claims” and “contradictions needing adjudication”;
5. artifact-directory conventions;
6. integrations with DOSBox/debugger tooling.

---

## 21. Guiding philosophy

Commonplace should make this interaction cheap:

```text
Agent A: I think X.
Agent B: I reproduced X.
Agent C: I found a counterexample.
Agent D: Here is the routine responsible.
Supervisor: Good. Two of you test the counterexample; one trace the caller.
```

It should not require the agents to maintain a giant shared prose document, reconstruct one another's chat history, or pretend every observation is certain before anyone can use it.

The tool succeeds when a swarm can accumulate a body of provisional but increasingly well-supported knowledge while preserving exactly how that knowledge was obtained.
