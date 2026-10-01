"""Command line interface for host maintenance and network operations."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from typing import Any

from .auth import AccessPolicy, load_token
from .client import ClientError, CommonplaceClient
from .errors import DomainError
from .models import OPERATIONS, READ_OPERATIONS

_FORMS = {
    "project": {
        "create": "create_project",
        "list": "list_projects",
        "get": "get_project",
        "archive": "set_project_archived",
        "unarchive": "set_project_archived",
    },
    "agent": {"register": "register_agent"},
    "session": {"start": "start_session", "end": "end_session"},
    "claim": {
        "create": "create_claim",
        "get": "get_claim",
        "search": "search_claims",
        "status": "set_claim_status",
        "supersede": "supersede_claim",
    },
    "evidence": {"add": "add_evidence", "list": "list_evidence"},
    "request": {
        "create": "create_request",
        "get": "get_request",
        "search": "search_requests",
        "lease": "lease_request",
        "next": "lease_next_request",
        "renew": "renew_lease",
        "release": "release_lease",
        "complete": "complete_request",
        "cancel": "cancel_request",
    },
    "lease": {"get": "get_lease", "list": "list_leases"},
    "relationship": {"relate": "relate_claims", "list": "list_relationships"},
    "reference": {"attach": "attach_reference"},
    "tags": {"add": "add_tags"},
    "activity": {"recent": "recent_activity"},
    "db": {"info": "database_info", "backup": "backup_database"},
}


def _env(name: str, flag_value: str | None) -> str | None:
    return flag_value if flag_value is not None else os.environ.get(name)


def _json_object(value: str) -> dict[str, Any]:
    try:
        result = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(result, dict):
        raise argparse.ArgumentTypeError("value must be a JSON object")
    return result


def _common_context(args: argparse.Namespace) -> dict[str, Any]:
    mappings = {
        "notebook_id": _env("COMMONPLACE_NOTEBOOK_ID", getattr(args, "notebook_id", None)),
        "generation": _env("COMMONPLACE_GENERATION", getattr(args, "generation", None)),
        "project": _env("COMMONPLACE_PROJECT", getattr(args, "project", None)),
        "agent_id": _env("COMMONPLACE_AGENT_ID", getattr(args, "agent", None)),
        "session_id": _env("COMMONPLACE_SESSION_ID", getattr(args, "session", None)),
    }
    return {key: value for key, value in mappings.items() if value is not None}


def _client(args: argparse.Namespace) -> CommonplaceClient:
    url = _env("COMMONPLACE_URL", getattr(args, "url", None))
    token_file = _env("COMMONPLACE_TOKEN_FILE", getattr(args, "token_file", None))
    if not url or not token_file:
        raise ClientError(
            "validation_error",
            "Set --url and --token-file or their COMMONPLACE_* environment variables",
        )
    try:
        return CommonplaceClient(url, token_file)
    except DomainError as exc:
        raise ClientError(exc.code, exc.message) from exc


def _render(envelope: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(envelope, ensure_ascii=False, sort_keys=True))
        return
    if envelope.get("ok"):
        print(json.dumps(envelope.get("data"), ensure_ascii=False, indent=2, sort_keys=True))
        return
    error = envelope.get("error") or {}
    print(
        f"{error.get('code', 'error')}: {error.get('message', 'Request failed')}", file=sys.stderr
    )
    if error.get("code") == "outcome_unknown":
        meta = envelope.get("meta") or {}
        print(
            f"Retry with operation ID {meta.get('operation_id')} and the same context.",
            file=sys.stderr,
        )


def _exit_code(envelope: dict[str, Any]) -> int:
    if envelope.get("ok"):
        return 0
    code = (envelope.get("error") or {}).get("code")
    if code in {"validation_error", "api_version_mismatch", "unauthenticated", "forbidden"}:
        return 2
    if code in {
        "session_mismatch",
        "session_closed",
        "lease_conflict",
        "lease_not_owned",
        "lease_expired",
        "request_closed",
        "conflict",
        "idempotency_conflict",
        "notebook_mismatch",
        "generation_mismatch",
        "project_archived",
    }:
        return 3
    return 1


def _add_network_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--url")
    parser.add_argument("--token-file")
    parser.add_argument("--project")
    parser.add_argument("--agent")
    parser.add_argument("--session")
    parser.add_argument("--notebook-id")
    parser.add_argument("--generation")
    parser.add_argument("--operation-id")
    parser.add_argument("--json", action="store_true", dest="as_json")
    parser.add_argument("--params", type=_json_object, default={})


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="commonplace", description="Shared research notebook client"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    operation = commands.add_parser("operation", help="call any named network operation")
    operation.add_argument("name", choices=sorted(OPERATIONS))
    _add_network_options(operation)

    for group, names in _FORMS.items():
        root = commands.add_parser(group)
        subs = root.add_subparsers(dest="action", required=True)
        for action in names:
            sub = subs.add_parser(action)
            _add_network_options(sub)
            if group == "project" and action in {"archive", "unarchive"}:
                sub.set_defaults(_archived=action == "archive")
            if group == "session" and action == "start":
                sub.add_argument("--new-session-id")
            if group == "claim" and action == "create":
                sub.add_argument("statement", nargs="?")
                sub.add_argument("--topic")
                sub.add_argument("--confidence")
                sub.add_argument("--rationale")
                sub.add_argument("--priority")
                sub.add_argument("--tag", action="append")
            if group == "evidence" and action == "add":
                sub.add_argument("claim_id", nargs="?")
                polarity = sub.add_mutually_exclusive_group()
                polarity.add_argument(
                    "--supports", action="store_const", const="supports", dest="polarity"
                )
                polarity.add_argument(
                    "--contradicts", action="store_const", const="contradicts", dest="polarity"
                )
                polarity.add_argument(
                    "--neutral", action="store_const", const="neutral", dest="polarity"
                )
                sub.add_argument("--method")
                sub.add_argument("summary", nargs="?")
                sub.add_argument("--procedure")
                sub.add_argument("--observation")
                sub.add_argument("--reproducibility")
            if group == "request" and action == "create":
                sub.add_argument("title", nargs="?")
                sub.add_argument("--type", dest="request_type")
                sub.add_argument("--claim", type=int, dest="claim_id")
                sub.add_argument("--redundancy", type=int, dest="desired_redundancy")
                sub.add_argument("--instructions")
                sub.add_argument("--priority")
            if group == "request" and action == "next":
                sub.add_argument("--lease", dest="lease_duration")
                sub.add_argument("--type", dest="request_type")
                sub.add_argument("--priority")
            if group == "db" and action == "backup":
                sub.add_argument("--name", dest="backup_name")
    recent = commands.add_parser("recent", help="show recent activity")
    _add_network_options(recent)
    recent.add_argument("--since", type=int, dest="since_seq")
    recent.add_argument("--limit", type=int)
    search = commands.add_parser("search", help="search claims, evidence and requests")
    _add_network_options(search)
    search.add_argument("query", nargs="?")
    snapshot_alias = commands.add_parser("snapshot", help="show the project snapshot")
    _add_network_options(snapshot_alias)

    context = commands.add_parser("context", help="discover service and notebook context")
    _add_network_options(context)
    # Host-only operations delegate storage access to the locked maintenance layer.
    db = commands.choices["db"]
    host = next(action for action in db._actions if isinstance(action, argparse._SubParsersAction))
    for action in ("init", "migrate"):
        sub = host.add_parser(action)
        sub.add_argument("--db", required=True)
    restore = host.add_parser("restore")
    restore.add_argument("--db", required=True)
    restore.add_argument("--from", dest="backup", required=True)
    serve = commands.add_parser("serve", help="run the local service")
    serve.add_argument("--db", required=True)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--token-file", required=True)
    serve.add_argument("--backup-dir")
    serve.add_argument("--allowed-origin", action="append", default=[])
    serve.add_argument("--allowed-host", action="append", default=[])
    serve.add_argument("--tls-cert")
    serve.add_argument("--tls-key")
    return parser


def _maintenance(args: argparse.Namespace) -> int:
    try:
        from . import maintenance
    except ImportError as exc:
        raise ClientError(
            "service_unavailable", "Host maintenance commands are not available"
        ) from exc
    function = {
        "init": "init_database",
        "migrate": "migrate_database",
        "restore": "restore_database",
    }[args.action]
    values = (args.db, args.backup) if args.action == "restore" else (args.db,)
    fn = getattr(maintenance, function, None)
    if fn is None:
        raise ClientError(
            "service_unavailable", f"Maintenance operation {function} is not available"
        )
    try:
        result = fn(*values)
    except DomainError as exc:
        raise ClientError(exc.code, exc.message) from exc
    if result is not None:
        print(json.dumps(result, default=str, sort_keys=True))
    return 0


def _serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn

        from .auth import is_loopback
        from .http_api import create_app
        from .service import Service

        token = load_token(args.token_file)
        if not is_loopback(args.host) and not (args.tls_cert and args.tls_key):
            raise ClientError(
                "validation_error", "Nonloopback service binds require --tls-cert and --tls-key"
            )
        service = Service(args.db, backup_dir=args.backup_dir)
        policy = AccessPolicy(
            token,
            allowed_hosts=set(args.allowed_host) | {args.host, "localhost", "127.0.0.1", "::1"},
            allowed_origins=set(args.allowed_origin),
        )
        app = create_app(service, policy)
        try:
            uvicorn.run(
                app,
                host=args.host,
                port=args.port,
                log_level="info",
                ssl_certfile=args.tls_cert,
                ssl_keyfile=args.tls_key,
            )
        finally:
            service.close()
        return 0
    except DomainError as exc:
        raise ClientError(exc.code, exc.message) from exc


def _operation(args: argparse.Namespace) -> int:
    name = getattr(args, "name", None)
    if args.command == "context":
        name = "context"
    elif args.command == "recent":
        name = "recent_activity"
    elif args.command == "search":
        name = "search"
    elif args.command == "snapshot":
        name = "project_snapshot"
    elif args.command != "operation":
        name = _FORMS[args.command][args.action]
    params = dict(args.params)
    for source, target in (
        ("statement", "statement"),
        ("topic", "topic"),
        ("confidence", "confidence"),
        ("rationale", "rationale"),
        ("priority", "priority"),
        ("claim_id", "claim_id"),
        ("polarity", "polarity"),
        ("method", "method"),
        ("summary", "summary"),
        ("procedure", "procedure"),
        ("observation", "observation"),
        ("reproducibility", "reproducibility"),
        ("title", "title"),
        ("request_type", "type"),
        ("desired_redundancy", "desired_redundancy"),
        ("instructions", "instructions"),
        ("since_seq", "since_seq"),
        ("limit", "limit"),
        ("query", "query"),
    ):
        value = getattr(args, source, None)
        if value is not None:
            params[target] = value
    backup_name = getattr(args, "backup_name", None)
    if backup_name is not None and name == "backup_database":
        params["name"] = backup_name
    if getattr(args, "tag", None):
        params["tags"] = list(params.get("tags", [])) + args.tag
    lease_duration = getattr(args, "lease_duration", None)
    if lease_duration:
        suffix = lease_duration[-1].lower()
        multiplier = {"s": 1, "m": 60, "h": 3600}.get(suffix, 1)
        number = lease_duration[:-1] if suffix in {"s", "m", "h"} else lease_duration
        try:
            params["lease_seconds"] = int(number) * multiplier
        except ValueError:
            raise ClientError(
                "validation_error", "--lease must be seconds, minutes (m), or hours (h)"
            ) from None
    if hasattr(args, "_archived"):
        params["archived"] = args._archived
    context = _common_context(args)
    operation_id = args.operation_id
    if name == "context":
        context = _common_context(args)
        operation_id = None
    if name == "start_session":
        new_id = (
            getattr(args, "new_session_id", None)
            or getattr(args, "session", None)
            or str(uuid.uuid4())
        )
        params["session_id"] = new_id
        context.pop("session_id", None)
        operation_id = operation_id or str(uuid.uuid4())
    if name not in READ_OPERATIONS:
        operation_id = operation_id or str(uuid.uuid4())
    try:
        with _client(args) as client:
            envelope = client.call(
                name,
                context=context,
                params=params,
                operation_id=operation_id,
                mutation=name not in READ_OPERATIONS,
                timeout=120 if name == "backup_database" else None,
            )
        if name == "start_session":
            envelope.setdefault("meta", {})["session_id"] = new_id
    except ClientError as exc:
        envelope = {
            "ok": False,
            "data": None,
            "error": {"code": exc.code, "message": str(exc), "retryable": False, "details": {}},
            "meta": {
                "operation_id": operation_id,
                **{
                    key: context.get(key)
                    for key in ("notebook_id", "generation", "project", "agent_id", "session_id")
                },
            },
        }
    _render(envelope, args.as_json)
    if name == "start_session" and not args.as_json and envelope.get("ok"):
        print(f"session_id: {new_id}")
    return _exit_code(envelope)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            return _serve(args)
        if args.command == "db" and args.action in {"init", "migrate", "restore"}:
            return _maintenance(args)
        return _operation(args)
    except ClientError as exc:
        as_json = getattr(args, "as_json", False)
        envelope = {
            "ok": False,
            "data": None,
            "error": {"code": exc.code, "message": str(exc), "retryable": False, "details": {}},
            "meta": {"operation_id": getattr(args, "operation_id", None)},
        }
        _render(envelope, as_json)
        return _exit_code(envelope)


if __name__ == "__main__":
    raise SystemExit(main())
