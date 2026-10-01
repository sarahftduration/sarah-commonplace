# Project instructions

Sarah Commonplace is a small self-hosted network service with a Python core,
SQLite storage, HTTP/JSON and MCP interfaces, a CLI client, and worker/supervisor
Skills. The repository currently contains the implementation specification and
plan; do not describe planned features as working software.

Use `docs/sarah-commonplace-SPEC.md` for domain behavior and
`docs/IMPLEMENTATION.md` for milestones. Read the portions relevant to the task.
Keep the specification, public contracts, examples, and Skills consistent when
changing behavior.

One service process owns the local SQLite database. CLI and MCP requests pass
through that service's shared domain logic; normal clients never open SQLite.
Only offline host maintenance commands may access storage outside the daemon,
under the same exclusive instance lock. The service owns sessions and lease time.

## Models and delegation

Sarah requests Sol as the implementation lead and Luna subagents for bounded work.
Use `gpt-6-sol` for the lead and explicitly select `gpt-6-luna` when delegating;
do not silently substitute another model. If the current task uses another lead
model, say so rather than claiming a model change. Follow later explicit model
instructions from Sarah.

The lead owns domain decisions, service/API boundaries, shared schema/contracts,
lease/concurrency logic, integration, and release verification. Delegate
independent, clearly bounded implementation, tests, documentation, or review tasks
to Luna after their inputs
and interfaces exist. Assign file ownership and concrete acceptance checks. Do
not let multiple agents edit the same files concurrently or commit over each
other's work. The lead reviews and integrates their results.

Commonplace itself does not spawn models or depend on a model provider. The
Sol/Luna choice describes the development workflow, not a runtime requirement.

## Standing commit and push permission

Sarah grants standing permission to create commits and push ordinary project
changes to this repository's configured `origin`
(`git@github.com:sarahftduration/sarah-commonplace.git`) without asking again.
This includes the current working branch, including `main`, when appropriate to
the task. Use `codex/` for new branches unless Sarah specifies a name.

Review the diff and run checks appropriate to the change before committing.
Stage only files belonging to the task; preserve unrelated user changes. Never
commit credentials, live notebooks/WAL files, or private research artifacts.
Coordinate Git changes through the lead when subagents share a checkout.

This permission covers routine commits and non-force pushes. It does not authorize
force pushes, rewriting published history, deleting remote branches, bypassing
branch protections, or publishing releases/deployments. Honor the runtime's
sandbox and approval controls; this file does not change them. Report the commit,
branch, push outcome, and checks performed.

## Verification

Test observable behavior and data integrity, especially lease races, expiry,
multi-project links, provenance, activity pagination, lost network responses,
and service crash recovery. Keep transactions short and adapters thin. Run focused
checks while developing and the complete required release checks before declaring
v0.1 complete.

Documentation-only changes require link/contract consistency and diff checks;
do not invent passing application tests before the implementation exists.
