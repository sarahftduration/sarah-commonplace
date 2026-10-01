# v0.1 wire and domain contracts

This document fixes the public names and defaults used by both adapters. The
[specification](sarah-commonplace-SPEC.md) defines behavior when a detail here is
not repeated. `POST /api/v1/operations/{name}` accepts JSON
`{"context": object, "params": object, "operation_id": UUID|null}`. The matching
MCP tool is `commonplace_{name}`, except `lease_next_request` maps to
`commonplace_lease_next`. MCP tools take those same three top-level fields. Both
return `{ok, data, error, meta}` as defined in section 22.4 of the specification.

## Context and bootstrap

Every regular call carries `notebook_id`, `generation`, `project`, `agent_id`, and
`session_id` in `context`. Database-wide calls omit `project`. `context` may omit
all fields; any supplied identifiers are validated. `start_session` carries
`notebook_id`, `generation`, and `agent_id`, and supplies its proposed UUID in
`params.session_id`. It may omit `project` and `context.session_id`. All mutations
require `operation_id`; reads set it to null or omit it. Every non-bootstrap
operation requires an existing matching session, including database-wide setup
mutations and reads. `end_session` may replay its terminal result after closure.

No adapter holds a current research context. A credential authenticates the
group, while the asserted agent/session pair supplies provenance. The service
rejects notebook or generation mismatches before idempotency replay. Success and
error `meta` include `operation_id`, `notebook_id`, `generation`, `project`,
`agent_id`, and `session_id` when known.

## Operation registry

`?` means optional; other fields are required. Unknown parameters are rejected.
All IDs of notebook entities are positive integers. Text and arrays obey section
15 limits. `refs` accepts reference IDs or objects with `kind`, `uri?`, `value?`,
`label?`, and `metadata?`. At least one of `uri` and `value` is required for a new
reference. `tags` is an array of strings. Metadata is a JSON object.

| Operation | Parameters | Result |
| --- | --- | --- |
| `context` | none | public service and resolved context |
| `database_info` | none | public notebook, schema, and SQLite runtime |
| `start_session` | `session_id`, `metadata?` | session |
| `end_session` | none | session |
| `register_agent` | `id`, `display_name?`, `kind?`, `role?`, `metadata?` | agent |
| `create_project` | `slug`, `name`, `description?` | project |
| `list_projects` | `limit?`, `cursor?` | page |
| `get_project` | `slug` | project |
| `set_project_archived` | `slug`, `archived`, `note` | project |
| `create_claim` | `topic`, `statement`, `rationale?`, `confidence?`, `priority?`, `tags?`, `refs?` | claim |
| `get_claim` | `id`, `include_evidence?`, `include_relationships?` | claim bundle |
| `search_claims` | `query?`, `topic?`, `status?`, `tags?`, `author?`, `ref_kind?`, `limit?`, `cursor?` | page |
| `set_claim_status` | `id`, `status`, `note` | claim |
| `supersede_claim` | `old_id`, claim creation fields | replacement claim |
| `add_evidence` | `claim_id`, `polarity`, `method`, `summary`, `procedure?`, `observation?`, `reproducibility?`, `replicates_evidence_id?`, `refs?`, `tags?` | evidence |
| `list_evidence` | `claim_id`, `polarity?`, `method?`, `limit?`, `cursor?` | page |
| `create_request` | `type`, `title`, `instructions`, `claim_id?`, `priority?`, `desired_redundancy?`, `tags?`, `refs?` | request |
| `get_request` | `id` | request bundle |
| `search_requests` | `query?`, `status?`, `type?`, `priority?`, `tags?`, `available_only?`, `limit?`, `cursor?` | page |
| `lease_request` | `request_id`, `lease_seconds?` | lease |
| `lease_next_request` | `type?`, `priority?`, `tags?`, `lease_seconds?` | lease or null |
| `renew_lease` | `lease_id`, `lease_seconds?` | lease |
| `release_lease` | `lease_id`, `note?` | lease |
| `complete_request` | `lease_id`, `note?`, `evidence_ids?`, `resulting_claim_ids?` | lease with result IDs |
| `cancel_request` | `request_id`, `note` | request |
| `get_lease` | `lease_id` | lease |
| `list_leases` | `request_id?`, `agent_id?`, `limit?`, `cursor?` | page |
| `relate_claims` | `from_claim_id`, `to_claim_id`, `type`, `note?` | relationship |
| `list_relationships` | `claim_id`, `limit?`, `cursor?` | page |
| `search` | `query?`, `entity_types?`, `topic?`, `status?`, `tags?`, `author?`, `ref_kind?`, `limit?`, `cursor?` | page |
| `attach_reference` | `entity_type`, `entity_id`, `reference` | reference |
| `add_tags` | `entity_type`, `entity_id`, `tags` | normalized tags |
| `recent_activity` | `since_seq?`, `limit?`, `types?` | `{events,next_seq,has_more}` |
| `project_snapshot` | none | bounded supervisor summary |
| `backup_database` | `name?` | backup receipt |

The service allowlist is precisely this table. Offline `db init`, `db migrate`,
and `db restore` are host commands and have no HTTP or MCP counterpart.

Defaults: `priority=normal`, `confidence=null`, `reproducibility=unreplicated`,
`desired_redundancy=1`, `lease_seconds=900`, list/search `limit=50`, activity
`limit=100`, activity `since_seq=0`, `get_claim` include flags true, and empty
arrays for optional tags/refs/result IDs. List limits are 1–200; lease duration
is 1–86400 seconds; desired redundancy is 1–50. A final page has
`next_cursor=null`; cursors are opaque, bind query/filter/project/generation, and
may be rejected as `validation_error` if reused with different inputs. `search`
hits contain `entity_type`, `id`, `snippet`, `matched_fields`, and `claim_id` for
evidence. Empty-query browsing orders by entity type and ID. Bundles embed at
most 50 items per collection with corresponding cursors.

## Transport and errors

HTTP accepts a bearer credential in `Authorization`, sends and receives JSON,
and never redirects with credentials. `GET /healthz` and `GET /readyz` expose
only liveness/readiness without a credential. All other paths require it. The
default body cap is 4 MiB. The service checks Host and supplied Origin before
dispatch; browser origins are denied unless configured. Remote clients require
HTTPS or an encrypted tunnel to a loopback listener.

| HTTP status | Domain error codes |
| --- | --- |
| 400 | `validation_error`, `api_version_mismatch` |
| 401 | `unauthenticated` |
| 403 | `forbidden`, `project_archived` |
| 404 | `not_found` |
| 409 | `session_mismatch`, `session_closed`, `lease_conflict`, `lease_not_owned`, `lease_expired`, `request_closed`, `conflict`, `idempotency_conflict`, `notebook_mismatch`, `generation_mismatch` |
| 413 | `validation_error` with `details.limit_bytes` |
| 503 | `database_busy`, `service_unavailable` |
| 500 | `internal_error` |

`schema_mismatch` and `unsupported_runtime` fail startup/readiness; they use
503 if discovered while serving. MCP uses standard protocol errors for malformed
MCP messages and sets its tool error flag for a domain error, carrying the same
envelope in structured content. CLI `--json` prints one envelope and uses exit
0 on success, 2 on configuration/validation/authentication errors, 3 on domain
conflicts, and 1 on transport/unknown outcome/internal failures.

Mutation operation IDs are UUIDs scoped to generation, agent, and research
session. Canonical JSON of operation name, context, and normalized parameters is
stored with the result in the same transaction. Reusing an ID for different
inputs returns `idempotency_conflict`; exact replay returns the stored envelope
without new activity. The CLI generates one UUID per invocation and preserves
it across at most two automatic retries. A possible lost response after retries
returns `outcome_unknown` with the original ID and non-secret context.

Activity is ordered by global `seq`. `recent_activity` captures a high-water
sequence in one read transaction, returns at most `limit` matching events, and
advances `next_seq` only to the last returned event if `has_more` is true.
Project/type filters and notebook generation belong with a saved cursor.

Event names are `agent.registered`, `session.started`, `session.ended`,
`project.created`, `project.archived`, `project.unarchived`, `claim.created`,
`claim.status_changed`, `claim.superseded`, `evidence.added`,
`request.created`, `request.leased`, `request.lease_renewed`,
`request.lease_released`, `request.completed`, `request.cancelled`,
`relationship.created`, `reference.attached`, `tags.added`, and
`database.backup_completed`. Expiry is derived and produces no event.
