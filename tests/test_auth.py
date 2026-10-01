from __future__ import annotations

import pytest

from commonplace.auth import AccessPolicy, load_token, validate_client_url
from commonplace.errors import DomainError


def test_token_file_and_shared_policy(tmp_path):
    path = tmp_path / "token"
    path.write_text("x" * 48 + "\n")
    path.chmod(0o600)
    token = load_token(path)
    policy = AccessPolicy(token)
    policy.check({"authorization": f"Bearer {token}", "host": "127.0.0.1:8765"})
    with pytest.raises(DomainError) as wrong_token:
        policy.check({"authorization": "Bearer bad", "host": "127.0.0.1"})
    assert wrong_token.value.code == "unauthenticated"
    with pytest.raises(DomainError) as wrong_origin:
        policy.check(
            {
                "authorization": f"Bearer {token}",
                "host": "localhost",
                "origin": "https://example.org",
            }
        )
    assert wrong_origin.value.code == "forbidden"
    with pytest.raises(DomainError) as wrong_host:
        policy.check({"authorization": f"Bearer {token}", "host": "evil.example"})
    assert wrong_host.value.code == "forbidden"
    path.chmod(0o644)
    with pytest.raises(DomainError):
        load_token(path)


def test_remote_plaintext_is_rejected():
    assert validate_client_url("http://localhost:8765") == "http://localhost:8765"
    assert validate_client_url("https://lab.example") == "https://lab.example"
    with pytest.raises(DomainError):
        validate_client_url("http://lab.example")
    with pytest.raises(DomainError):
        validate_client_url("https://token@lab.example")
