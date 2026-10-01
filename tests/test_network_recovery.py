import json

import httpx

from commonplace.client import CommonplaceClient


def _token_file(tmp_path):
    path = tmp_path / "token"
    path.write_text("t" * 40)
    path.chmod(0o600)
    return path


def test_mutation_retries_identical_request_and_marks_lost_result_unknown(tmp_path):
    calls = []

    def handler(request):
        calls.append((request.content, request.headers["authorization"]))
        raise httpx.ReadTimeout("response lost", request=request)

    transport_client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    client = CommonplaceClient(
        "http://localhost:8765",
        str(_token_file(tmp_path)),
        retries=2,
        backoff=0,
        client=transport_client,
    )
    context = {
        "notebook_id": "n",
        "generation": "g",
        "project": "p",
        "agent_id": "a",
        "session_id": "s",
    }
    result = client.call(
        "create_claim",
        context=context,
        params={"statement": "same"},
        operation_id="op-1",
        mutation=True,
    )
    client.close()
    assert len(calls) == 3
    assert len({body for body, _ in calls}) == 1
    assert len({auth for _, auth in calls}) == 1
    sent = json.loads(calls[0][0])
    assert sent["operation_id"] == "op-1"
    assert sent["context"] == context
    assert result["error"]["code"] == "outcome_unknown"
    assert result["meta"]["operation_id"] == "op-1"


def test_connect_failure_is_known_unavailable(tmp_path):
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    client = CommonplaceClient(
        "http://localhost:8765",
        str(_token_file(tmp_path)),
        retries=0,
        backoff=0,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        client.call("create_claim", params={}, operation_id="op-2", mutation=True)
    except Exception as exc:
        assert getattr(exc, "code", None) == "service_unavailable"
    else:
        raise AssertionError("expected unavailable error")
