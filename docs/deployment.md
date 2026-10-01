# Deployment and host maintenance

Commonplace is one service process for one local SQLite database. Run it on a
local disk, not NFS, SMB, or a cloud-sync folder. CLI and MCP clients connect to
the service; they do not mount or open the database. The service needs Python
3.11+ whose loaded `sqlite3` library is SQLite 3.51.3+ with FTS5. It checks this
before opening a notebook for research. This repository's `.python-version` and
`uv.lock` are the tested development combination; check the loaded library with
`python -c 'import sqlite3; print(sqlite3.sqlite_version)'` in the environment
used to run `commonplace`.

## Install and start on loopback

Resolve dependencies while online, then the same environment can run without
Internet access. From the repository root:

```bash
uv sync --locked
mkdir -p "$HOME/commonplace-host/backups"
umask 077
python -c 'import secrets; print(secrets.token_urlsafe(48))' > "$HOME/commonplace-host/token"
chmod 600 "$HOME/commonplace-host/token"
uv run commonplace db init --db "$HOME/commonplace-host/notebook.db"
uv run commonplace serve --db "$HOME/commonplace-host/notebook.db" \
  --token-file "$HOME/commonplace-host/token" \
  --backup-dir "$HOME/commonplace-host/backups"
```

`db init` and `serve` fail if the target database is absent, already initialized,
incompatible, or owned by another process, as appropriate. The service binds
`127.0.0.1:8765` by default. In another shell, `curl -f
http://127.0.0.1:8765/readyz` checks readiness without revealing notebook data.
`/healthz` reports only liveness. All domain routes require the token.

Keep the token out of command arguments, URLs, logs, research records, and
repository files. The service reads the credential from a regular file with
owner-only permissions. Replacing/revoking the token is an operator action:
replace the file securely and restart the service. Workers retain their research
session identities across that restart.

## Client setup

Clients first set `COMMONPLACE_URL` and `COMMONPLACE_TOKEN_FILE`. Run
`commonplace context --json` without an agent or session ID to discover the
notebook ID and generation, then retain them as `COMMONPLACE_NOTEBOOK_ID` and
`COMMONPLACE_GENERATION`. Set a distinct `COMMONPLACE_AGENT_ID` and start a research session explicitly with
`commonplace session start --json`; retain the returned session UUID as
`COMMONPLACE_SESSION_ID`. Create/select a project through the network interface
and set `COMMONPLACE_PROJECT` for project-scoped research. Every worker has its
own agent and session; the service URL and shared token alone are not research
provenance. See [contracts.md](contracts.md) for the full operation registry.

A client unable to reach the service reports unavailability. It does not start a
private notebook. Preserve the operation ID and complete non-secret context from
an `outcome_unknown` result and replay exactly that write to reconcile it.
Notebook generation mismatch after restore requires a fresh context/snapshot and
explicit reconciliation of any pending work. Ordinary restart preserves
sessions, leases, idempotency results, and activity cursors.

## Protected remote access

One supported deployment keeps the service on loopback and uses an authenticated
SSH tunnel from each remote client:

```bash
ssh -L 8765:127.0.0.1:8765 operator@service-host
```

The remote client uses `COMMONPLACE_URL=http://127.0.0.1:8765`; the tunnel
encrypts traffic to the service host. The credential file stays on each
authorized client host and is transmitted only as an Authorization header.

For direct remote listening, supply `--host`, a trusted `--tls-cert` and
`--tls-key`, and explicit `--allowed-host` values. Non-loopback startup refuses
to run without TLS. Clients use an `https://` URL and normal certificate
verification. A reverse proxy can instead terminate TLS and forward only to
the loopback listener. The default denies browser Origin headers; add exact
`--allowed-origin` values only for intended origins. There is no OAuth discovery
profile in v0.1; MCP hosts must configure the private bearer header explicitly
or use the CLI fallback.

## Shutdown, backup, restore, and upgrades

Send SIGTERM for normal shutdown. Readiness becomes false, the service drains
in-flight calls for its grace period, then releases its database lock. A forced
termination leaves SQLite to recover committed WAL transactions at restart;
research sessions and leases remain stored. Expiry is evaluated from the service
host clock after a new write lock is acquired.

With the service running, an authenticated actor may call `commonplace db backup
--name NAME` (or `commonplace_backup_database`) to create a consistent SQLite
backup in the configured backup directory. NAME is a simple basename. The
response is a receipt, not a file download. Backups include the notebook but
do not copy the external artifacts named by references. Copy a completed backup
to archival storage through normal operator procedures. Keep its receipt with
that copy. An interrupted backup with the same operation ID is reconciled on
retry; a published file is checked before its receipt is finalized.

To restore, stop the old service and choose a **new** database destination:

```bash
commonplace db restore --db "$HOME/commonplace-host/restored.db" \
  --from "$HOME/commonplace-host/backups/NAME"
commonplace db migrate --db "$HOME/commonplace-host/restored.db"
```

Restore verifies integrity and foreign keys, preserves the notebook ID, and
creates a new generation. It never overwrites the source or an existing
destination. Start the service with the restored path. Clients must rediscover
context, inspect a fresh snapshot, reset activity/search cursors, and reconcile
uncertain writes instead of replaying old-generation IDs. `db migrate` is
offline, uses the same instance lock, and is a no-op for the initial version 1
schema. Future releases will add explicit versioned migrations. Maintenance
events live in database metadata, separate from research activity.

Service operational logs go to the process stderr or the host service manager's
captured logs. They should contain IDs and failures, not notebook prose or
credentials. A `file:///...` artifact URI remains tied to its originating host;
service access never grants access to that file.
