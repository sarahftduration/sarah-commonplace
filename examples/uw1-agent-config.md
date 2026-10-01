# Example: three UW1 research identities

This example uses one running Commonplace service and one `uw1` project. The
paths, IDs, and endpoint come from the host operator; replace the uppercase
placeholders. No worker starts a private database. The service stores only
references to traces, screenshots, and save states; those artifacts remain on
their origin hosts or shared storage.

Each worker is provisioned with the same authenticated service destination and
a distinct provenance identity:

```bash
export COMMONPLACE_URL="HTTP_OR_HTTPS_SERVICE_BASE_URL"
export COMMONPLACE_TOKEN_FILE="PATH_TO_OWNER_ONLY_TOKEN_FILE"

commonplace context --json
export COMMONPLACE_NOTEBOOK_ID="DISCOVERED_NOTEBOOK_UUID"
export COMMONPLACE_GENERATION="DISCOVERED_GENERATION_UUID"
export COMMONPLACE_AGENT_ID="luna-03"
commonplace session start --json
export COMMONPLACE_SESSION_ID="RETURNED_SESSION_UUID"
export COMMONPLACE_PROJECT="uw1"
```

The supervisor creates the project under its own started session, before setting
`COMMONPLACE_PROJECT`, with
`commonplace project create --params '{"slug":"uw1","name":"Ultima Underworld I"}'`.
`luna-07` and `luna-09` repeat the setup above with
their own `COMMONPLACE_AGENT_ID` and session UUIDs. Their MCP hosts configure the
same private service endpoint and bearer header, with the same per-worker
context fields on **every** tool call. An MCP transport session does not select a
research identity.

## Claim, replication, and supervision

The first worker searches before creating a possibly duplicate claim:

```bash
commonplace search '19A4:02D6' --json
commonplace claim create --topic combat --confidence tentative \
  'Word at 19A4:02D6 is player HP' --json
commonplace evidence add CLAIM_ID --supports --method watchpoint \
  'Damage changed the watched word from 23 to 20' \
  --params '{"procedure":"Set a write watchpoint, then take one rat hit","refs":[{"kind":"trace","uri":"file:///ORIGIN_HOST/trace/run-042","metadata":{"origin_host":"WORKER_HOST"}}]}' --json
commonplace request create --type replicate --claim CLAIM_ID --redundancy 2 \
  --instructions 'Test damage and healing independently; record negative findings too.' \
  'Independently test the HP address' --json
```

The two other workers lease separate slots. For an MCP worker, the tool call
shape is:

```json
{
  "tool": "commonplace_lease_next",
  "arguments": {
    "context": {
      "notebook_id": "DISCOVERED_NOTEBOOK_UUID",
      "generation": "DISCOVERED_GENERATION_UUID",
      "project": "uw1",
      "agent_id": "luna-07",
      "session_id": "LUNA_07_SESSION_UUID"
    },
    "params": {"type": "replicate", "lease_seconds": 900},
    "operation_id": "NEW_OPERATION_UUID"
  }
}
```

The other worker can use CLI fallback against the same service:

```bash
commonplace request next --type replicate --lease 15m --json
commonplace evidence add CLAIM_ID --supports --method memory-edit \
  'Direct edit and healing both changed the panel value' \
  --params '{"reproducibility":"replicated","replicates_evidence_id":ORIGINAL_EVIDENCE_ID}' --json
commonplace request complete --params \
  '{"lease_id":LEASE_ID,"note":"Replicated with damage and healing","evidence_ids":[NEW_EVIDENCE_ID]}' --json
```

`luna-07` does the same with its own lease and evidence. Completion records work
performed, including contradictions; two distinct completed agents satisfy the
request's redundancy target. The supervisor calls `commonplace snapshot --json`,
drains `commonplace recent --since SEQ --json` while `has_more` is true, inspects
both procedures, and may run:

```bash
commonplace claim status --params \
  '{"id":CLAIM_ID,"status":"supported","note":"Two distinct workers reported linked replication evidence"}' --json
```

A later correction creates a new claim with `commonplace claim supersede
--params '{"old_id":CLAIM_ID,"topic":"combat","statement":"..."}'`. The old
claim and evidence remain in history. If an outcome is uncertain after a network
failure, retry the **same** operation ID, context, and payload; do not infer that
the write rolled back. After a restore generation change, inspect a fresh
snapshot and reconcile before submitting new work.
