# sarah-commonplace

A local research notebook for agents and humans to share claims, evidence,
replication requests, and findings. Designed initially for reverse engineering.

**Status:** specification reviewed and ready to begin v0.1 implementation.
There is no runnable application yet.

- [Specification](docs/sarah-commonplace-SPEC.md): behavior and acceptance criteria.
- [Implementation plan](docs/IMPLEMENTATION.md): staged work for a Sol lead with
  Luna subagents, ownership, and verification gates.
- [Project instructions](AGENTS.md): development guidance and Sarah's standing
  permission to commit and push ordinary project changes.

The required deliverables are a Python library, SQLite storage, CLI, stdio MCP
server, tests, setup/backup documentation, and two agent Skills:
`commonplace-worker` and `commonplace-supervisor`. The Skills will be authored and
verified against the working interfaces during implementation; see
[their requirements](docs/sarah-commonplace-SPEC.md#23-required-agent-skills).

Commonplace coordinates notebook records and work leases. The host agent system
chooses and runs models; Commonplace itself needs no model API credentials.
