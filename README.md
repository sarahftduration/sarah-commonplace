# Sarah Commonplace

A small self-hosted service for agents and humans to share research claims,
evidence, replication requests, and findings. One service owns a local SQLite
database. The CLI uses HTTP/JSON, and agents can also use MCP over HTTP; both
interfaces run the same domain operations.

**Status:** v0.1 alpha implementation. The service, CLI, MCP adapter, backup and
restore commands, tests, and two agent Skills are present. The final live
worker Skill workflow remains a release gate; see [release notes](docs/RELEASE_NOTES.md).

## Five-minute loopback quick start

Install [uv](https://docs.astral.sh/uv/) and resolve dependencies once while
online. The pinned Python environment includes SQLite 3.51.3+ and FTS5. From
the repository root:

```bash
uv sync --locked
mkdir -p "$HOME/commonplace-host/backups"
umask 077
python -c 'import secrets; print(secrets.token_urlsafe(48))' > "$HOME/commonplace-host/token"
uv run commonplace db init --db "$HOME/commonplace-host/notebook.db"
uv run commonplace serve --db "$HOME/commonplace-host/notebook.db" \
  --token-file "$HOME/commonplace-host/token" \
  --backup-dir "$HOME/commonplace-host/backups"
```

In a second terminal, with the service running:

```bash
export COMMONPLACE_URL=http://127.0.0.1:8765
export COMMONPLACE_TOKEN_FILE="$HOME/commonplace-host/token"
uv run commonplace context --json
```

Copy the `notebook_id` and `generation` from that response, then set your
research identity and start its persistent session:

```bash
export COMMONPLACE_NOTEBOOK_ID=PASTE_NOTEBOOK_UUID
export COMMONPLACE_GENERATION=PASTE_GENERATION_UUID
export COMMONPLACE_AGENT_ID=supervisor
uv run commonplace session start --json
export COMMONPLACE_SESSION_ID=PASTE_RETURNED_SESSION_UUID
uv run commonplace project create \
  --params '{"slug":"uw1","name":"Ultima Underworld I"}' --json
export COMMONPLACE_PROJECT=uw1
uv run commonplace claim create --topic combat \
  'A watched word tracks player HP during damage' --json
uv run commonplace search 'player HP' --json
```

Create the project before setting `COMMONPLACE_PROJECT`. Each worker uses the
same service URL and token, but a distinct `COMMONPLACE_AGENT_ID` and research
session. A [three-agent example](examples/uw1-agent-config.md) shows evidence,
replication leases, and supervision through CLI and MCP. Keep the token private;
never put it in a repository file or command argument.

## Operating model

The service owns all sessions, lease time, writes, and idempotency results.
Clients retain the notebook ID, restore generation, agent ID, session ID, and
operation IDs. If a write loses its response, retry the same operation ID and
payload to learn the committed result. A restore changes the generation and
requires clients to reconcile pending work before writing again. Normal clients
never open the database file. A loopback deployment works without Internet
access after dependencies are installed; remote clients use TLS or an encrypted
tunnel.

- [Specification](docs/sarah-commonplace-SPEC.md): domain behavior and acceptance criteria.
- [Contracts](docs/contracts.md): operation names, context, defaults, and errors.
- [Deployment guide](docs/deployment.md): service, credentials, TLS/tunnel, backup, restore, and upgrades.
- [Agent setup](docs/agent-setup.md): install the worker and supervisor Skills.
- [Implementation plan](docs/IMPLEMENTATION.md): milestones and release gates.
- [Release notes](docs/RELEASE_NOTES.md): verification results and remaining limits.

Commonplace coordinates work; the agent host chooses and runs models. The
service does not require model API credentials.
