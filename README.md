# sarah-commonplace

A small, self-hosted network service for agents and humans to share claims,
evidence, replication requests, and findings. Designed initially for reverse
engineering. CLI and MCP are two interfaces to the same service.

**Status:** specification reviewed and ready to begin v0.1 implementation.
There is no runnable application yet.

- [Specification](docs/sarah-commonplace-SPEC.md): behavior and acceptance criteria.
- [Implementation plan](docs/IMPLEMENTATION.md): staged work for a Sol lead with
  Luna subagents, ownership, and verification gates.
- [Project instructions](AGENTS.md): development guidance and Sarah's standing
  permission to commit and push ordinary project changes.

One service process owns a SQLite database on its host's local disk. A CLI client
uses the HTTP/JSON API; agents use the service's MCP over HTTP interface. Both
interfaces invoke the same domain logic. Clients may run on other machines and
never need the database file. A loopback deployment works without Internet access.

The required deliverables are the Python domain library, service executable,
SQLite storage, HTTP/JSON and MCP interfaces, CLI client, tests, deployment/backup
documentation, and two agent Skills:
`commonplace-worker` and `commonplace-supervisor`. The Skills will be authored and
verified against the working interfaces during implementation; see
[their requirements](docs/sarah-commonplace-SPEC.md#23-required-agent-skills).

Commonplace coordinates notebook records and work leases. The host agent system
chooses and runs models; Commonplace itself needs no model API credentials.
The service requires its own access token; remote connections use TLS or an
authenticated encrypted tunnel. See the
[service contract](docs/sarah-commonplace-SPEC.md#24-network-service-contract).
