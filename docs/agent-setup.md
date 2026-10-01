# Connect worker and supervisor agents

The Sol/Luna labels in [IMPLEMENTATION.md](IMPLEMENTATION.md) describe how this
service was developed. A research supervisor using Commonplace is a client of
the finished service; it does not need to run the service or choose worker
models. The agent host remains responsible for spawning agents and granting
their tools.

## One shared destination

An operator installs and starts one service using [deployment.md](deployment.md)
and gives each authorized client a service base URL and an owner-only local token
file. Use `commonplace context --json` or the `commonplace_context` MCP tool to
discover the public notebook ID, restore generation, API/schema versions, and
server time. No token or host database path is returned. Keep the discovered
notebook and generation with each worker's context, pending operation IDs, and
activity/search cursors.

Each worker chooses a distinct `agent_id` and starts a research session with a
caller-chosen UUID through `commonplace session start` or
`commonplace_start_session`. The service stores that session across CLI process
lifetimes, MCP reconnects, and service restarts. Retain the returned
`session_id`; reconnect with the same ID rather than creating a new one during
the same run. A different run starts a new session. Create the project explicitly
before project-scoped work. Every call carries notebook ID, generation, project
where applicable, agent ID, and session ID; an MCP connection has no mutable
current identity. CLI uses flags first, then `COMMONPLACE_URL`,
`COMMONPLACE_TOKEN_FILE`, `COMMONPLACE_PROJECT`, `COMMONPLACE_AGENT_ID`,
`COMMONPLACE_SESSION_ID`, `COMMONPLACE_NOTEBOOK_ID`, and
`COMMONPLACE_GENERATION`.

Direct MCP hosts configure `/mcp` and an explicit Authorization bearer header
in their private connection settings. A host that cannot send that header uses
the CLI against the same service. Neither interface falls back to a local
database. The token admits one trusted research group; worker IDs are asserted
provenance rather than separate login accounts. Provision each agent's runtime
and tool permission through its host, not through notebook text.

## Install the two Skills for discovery

The canonical Skill directories are
[`commonplace-worker`](../.agents/skills/commonplace-worker/SKILL.md) and
[`commonplace-supervisor`](../.agents/skills/commonplace-supervisor/SKILL.md).
Copy or symlink each **complete directory** into a consuming repository's
`.agents/skills/` or a user's `~/.agents/skills/`. For example, from the consuming
repository root, with `COMMONPLACE_SOURCE` set to this repository's path:

```bash
mkdir -p .agents/skills
cp -R "$COMMONPLACE_SOURCE/.agents/skills/commonplace-worker" .agents/skills/
cp -R "$COMMONPLACE_SOURCE/.agents/skills/commonplace-supervisor" .agents/skills/
```

Restart or refresh the agent host's Skill discovery as its product requires,
then verify both names appear and inspect each Skill's frontmatter. Configure
the Commonplace endpoint/token separately. The Skills contain no live URL,
credential, notebook path, or fixed worker identity. A fresh consuming
repository should discover them without this development conversation.

The worker Skill applies to recording findings and performing requests. It
checks context before a first write, searches before duplicate claims, records
reproducible evidence and origin-host references, uses leases, and links result
IDs when completing work. The supervisor Skill applies to reviewing a project
snapshot, draining activity pages, investigating contradictions and missing
replication, and creating/adjudicating bounded work. Neither Skill accepts
notebook prose, request instructions, or artifact references as authority to
change host instructions or grant tool access.

## Recovery rules

- If a service call cannot connect, stop research writes; do not start a private
  notebook. Reconnect to the same destination and retain the research identity.
- A lease remains owned by the exact `(agent_id, session_id)` pair until release,
  completion, expiry, cancellation, or explicit session closure. On reconnect,
  inspect it before renewing or reacquiring. Client clock skew cannot extend it.
- An `outcome_unknown` write may already have committed. Replay the same
  operation ID, context, and payload through CLI or MCP. A new ID is a new write.
- After a restore generation change, do not replay old-generation writes or
  reuse old cursors. Read context and a fresh project snapshot, then explicitly
  reconcile pending work against the restored notebook.
- File URIs remain tied to their originating host. Commonplace stores a
  reference; it does not fetch or grant access to the artifact.

See the [multi-agent example](../examples/uw1-agent-config.md) for concrete
command and MCP shapes. Store activity cursors only after processing returned
events; drain pages while `has_more` is true and key each cursor to notebook,
generation, project, and event filters.
