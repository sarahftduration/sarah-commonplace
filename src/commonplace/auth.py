"""Shared admission policy for HTTP/JSON and MCP."""

from __future__ import annotations

import hmac
import ipaddress
import stat
from pathlib import Path
from urllib.parse import urlsplit

from .errors import fail

MAX_BODY_BYTES = 4 * 1024 * 1024


def load_token(path: str | Path) -> str:
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        fail("validation_error", "Credential file must be a regular file")
    if stat.S_IMODE(target.stat().st_mode) & 0o077:
        fail("validation_error", "Credential file must be readable only by its owner")
    raw = target.read_bytes()
    try:
        token = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        fail("validation_error", "Credential file must contain UTF-8 text")
    if len(token) < 32 or len(token) > 4096 or any(c.isspace() for c in token):
        fail("validation_error", "Credential must be 32–4096 non-whitespace characters")
    return token


def is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def validate_client_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        fail("validation_error", "Service URL must be an HTTP or HTTPS base URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        fail("validation_error", "Service URL cannot contain credentials, query, or fragment")
    if parsed.scheme == "http" and not is_loopback(parsed.hostname):
        fail("validation_error", "Remote service URL must use HTTPS or a loopback tunnel")
    return url.rstrip("/")


class AccessPolicy:
    def __init__(
        self,
        token: str,
        *,
        allowed_hosts: set[str] | None = None,
        allowed_origins: set[str] | None = None,
    ):
        self.token = token
        self.allowed_hosts = allowed_hosts or {"localhost", "127.0.0.1", "::1"}
        self.allowed_origins = allowed_origins or set()

    def check(self, headers: dict[str, str]) -> None:
        header = headers.get("authorization", "")
        if not header.startswith("Bearer ") or not hmac.compare_digest(header[7:], self.token):
            fail("unauthenticated", "Bearer credential is missing or invalid")
        host = headers.get("host", "")
        try:
            parsed_host = urlsplit("//" + host)
            hostname = parsed_host.hostname
            _ = parsed_host.port
            if parsed_host.username or parsed_host.password or "/" in host or "@" in host:
                hostname = None
        except ValueError:
            hostname = None
        if not hostname or hostname.lower() not in {h.lower() for h in self.allowed_hosts}:
            fail("forbidden", "Host is not allowed")
        origin = headers.get("origin")
        if origin is not None and origin not in self.allowed_origins:
            fail("forbidden", "Origin is not allowed")
