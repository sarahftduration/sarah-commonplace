"""Network-only HTTP client for a Sarah Commonplace service."""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx

from .auth import load_token, validate_client_url


class ClientError(Exception):
    def __init__(self, code: str, message: str, *, envelope: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.envelope = envelope


class CommonplaceClient:
    """Sends stable operation requests to the service; it never opens SQLite."""

    def __init__(
        self,
        url: str,
        token_file: str,
        *,
        connect_timeout: float = 5,
        response_timeout: float = 30,
        retries: int = 2,
        client: httpx.Client | None = None,
        backoff: float = 0.1,
    ):
        self.url = validate_client_url(url)
        self.token = load_token(token_file)
        self.connect_timeout = float(connect_timeout)
        self.retries = max(0, min(int(retries), 2))
        self.backoff = max(0.0, float(backoff))
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(response_timeout, connect=connect_timeout),
            follow_redirects=False,
        )
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> CommonplaceClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    @staticmethod
    def _unknown(operation_id: str | None, context: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": False,
            "data": None,
            "error": {
                "code": "outcome_unknown",
                "message": (
                    "The service may have completed this operation; retry with the same "
                    "operation ID and context."
                ),
                "retryable": True,
                "details": {},
            },
            "meta": {
                "operation_id": operation_id,
                "notebook_id": context.get("notebook_id"),
                "generation": context.get("generation"),
                "project": context.get("project"),
                "agent_id": context.get("agent_id"),
                "session_id": context.get("session_id"),
            },
        }

    def call(
        self,
        operation: str,
        *,
        context: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        operation_id: str | None = None,
        mutation: bool | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        context = dict(context or {})
        params = dict(params or {})
        if mutation is None:
            # Keep the retry semantics independent of server internals.
            from .models import READ_OPERATIONS

            mutation = operation not in READ_OPERATIONS
        if mutation and operation_id is None:
            operation_id = str(uuid.uuid4())
        payload = {"context": context, "params": params, "operation_id": operation_id}
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        endpoint = f"{self.url}/api/v1/operations/{operation}"
        maybe_sent = False
        for attempt in range(self.retries + 1):
            try:
                kwargs: dict[str, Any] = {"headers": headers, "json": payload}
                if timeout is not None:
                    kwargs["timeout"] = httpx.Timeout(float(timeout), connect=self.connect_timeout)
                # Mark before post: transport errors after this point can be ambiguous.
                maybe_sent = True
                response = self._client.post(endpoint, **kwargs)
                if response.is_redirect:
                    if mutation:
                        return self._unknown(operation_id, context)
                    raise ClientError(
                        "service_unavailable",
                        "Service redirected the request; credentials were not forwarded",
                    )
                try:
                    envelope = response.json()
                except (ValueError, TypeError):
                    envelope = None
                if response.status_code == 503 and attempt < self.retries:
                    time.sleep(self.backoff * (attempt + 1))
                    continue
                if (
                    isinstance(envelope, dict)
                    and {"ok", "data", "error", "meta"} <= envelope.keys()
                ):
                    if response.status_code >= 500 and not envelope.get("ok"):
                        if attempt < self.retries:
                            time.sleep(self.backoff * (attempt + 1))
                            continue
                        if mutation:
                            return self._unknown(operation_id, context)
                    return envelope
                if attempt < self.retries and response.status_code >= 500:
                    time.sleep(self.backoff * (attempt + 1))
                    continue
                if mutation:
                    return self._unknown(operation_id, context)
                raise ClientError(
                    "internal_error",
                    f"Service returned an invalid response (HTTP {response.status_code})",
                )
            except ClientError:
                raise
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
                if attempt < self.retries:
                    time.sleep(self.backoff * (attempt + 1))
                    continue
                raise ClientError(
                    "service_unavailable", "Could not connect to the Commonplace service"
                ) from exc
            except (
                httpx.ReadTimeout,
                httpx.WriteTimeout,
                httpx.ReadError,
                httpx.WriteError,
                httpx.RemoteProtocolError,
                httpx.NetworkError,
                httpx.TimeoutException,
            ) as exc:
                if attempt < self.retries:
                    time.sleep(self.backoff * (attempt + 1))
                    continue
                if mutation and maybe_sent:
                    return self._unknown(operation_id, context)
                raise ClientError("service_unavailable", "Network request failed") from exc
            except httpx.HTTPError as exc:
                if attempt < self.retries:
                    time.sleep(self.backoff * (attempt + 1))
                    continue
                if mutation and maybe_sent:
                    return self._unknown(operation_id, context)
                raise ClientError("service_unavailable", "Network request failed") from exc
        return (
            self._unknown(operation_id, context)
            if mutation
            else {
                "ok": False,
                "data": None,
                "error": {
                    "code": "service_unavailable",
                    "message": "Service unavailable",
                    "retryable": True,
                    "details": {},
                },
                "meta": {"operation_id": operation_id},
            }
        )
