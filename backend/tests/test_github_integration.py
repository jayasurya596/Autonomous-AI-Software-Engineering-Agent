"""GitHub token handling, target validation, stale checks and PR construction."""

import asyncio
import json

import httpx
import pytest
from app.services.github import (
    GithubIntegration,
    GithubIntegrationError,
    GithubStaleRepositoryError,
    parse_github_repository,
)
from app.services.repositories import git_blob_sha
from cryptography.fernet import Fernet


def test_repository_url_must_be_a_canonical_github_https_url() -> None:
    assert parse_github_repository("https://github.com/octocat/hello-world.git") == (
        "octocat",
        "hello-world",
    )
    for value in (
        "http://github.com/octocat/hello-world",
        "https://github.com.evil.example/octocat/hello-world",
        "https://user:password@github.com/octocat/hello-world",
        "https://github.com/octocat/hello-world?redirect=elsewhere",
        "https://github.com/octocat/hello-world/issues",
    ):
        with pytest.raises(GithubIntegrationError):
            parse_github_repository(value)


def test_github_tokens_are_encrypted_and_invalid_keys_are_rejected() -> None:
    key = Fernet.generate_key().decode()
    integration = GithubIntegration(key)
    encrypted = integration.encrypt_token("github-token-that-is-long-enough")
    assert "github-token" not in encrypted
    assert integration.decrypt_token(encrypted) == "github-token-that-is-long-enough"
    with pytest.raises(GithubIntegrationError):
        GithubIntegration("not-a-valid-fernet-key")


def test_publish_creates_branch_and_pull_request_only_for_current_files() -> None:
    original = "def value():\n    return 1\n"
    observed: list[tuple[str, str, dict[str, object] | None]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = request.read()
        payload = json.loads(body) if body else None
        observed.append((request.method, request.url.path, payload))
        path = request.url.path
        if path.endswith("/user"):
            return httpx.Response(
                200, json={"login": "octocat"}, headers={"X-OAuth-Scopes": "repo"}
            )
        if path.endswith("/repos/octocat/demo"):
            return httpx.Response(200, json={"default_branch": "main"})
        if path.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "base-commit"}})
        if path.endswith("/git/commits/base-commit"):
            return httpx.Response(200, json={"tree": {"sha": "base-tree"}})
        if path.endswith("/git/trees/base-tree"):
            return httpx.Response(
                200,
                json={
                    "tree": [
                        {"path": "app.py", "type": "blob", "sha": git_blob_sha(original.encode())}
                    ]
                },
            )
        if path.endswith("/git/blobs"):
            return httpx.Response(201, json={"sha": "new-blob"})
        if path.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "new-tree"})
        if path.endswith("/git/commits"):
            return httpx.Response(201, json={"sha": "new-commit"})
        if path.endswith("/git/refs"):
            return httpx.Response(201, json={"ref": "refs/heads/codex/approval-test"})
        if path.endswith("/pulls"):
            return httpx.Response(201, json={"html_url": "https://github.com/octocat/demo/pull/12"})
        return httpx.Response(404, json={"message": "not found"})

    async def verify() -> None:
        integration = GithubIntegration(
            Fernet.generate_key().decode(), transport=httpx.MockTransport(handler)
        )
        account, scopes = await integration.verify_token("github-token-that-is-long-enough")
        assert account == "octocat" and scopes == "repo"
        branch, url = await integration.publish_pull_request(
            "github-token-that-is-long-enough",
            "https://github.com/octocat/demo",
            "test",
            "Improve value",
            [{"path": "app.py", "content": "def value():\n    return 2\n"}],
            {"app.py": git_blob_sha(original.encode())},
        )
        assert branch == "codex/approval-test"
        assert url == "https://github.com/octocat/demo/pull/12"
        assert any(path.endswith("/git/refs") for _, path, _ in observed)
        pull = next(payload for _, path, payload in observed if path.endswith("/pulls"))
        assert pull is not None and pull["base"] == "main" and pull["head"] == branch

    asyncio.run(verify())


def test_publish_stops_before_writing_if_remote_content_is_stale() -> None:
    writes: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            writes.append(request.url.path)
        path = request.url.path
        if path.endswith("/repos/octocat/demo"):
            return httpx.Response(200, json={"default_branch": "main"})
        if path.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "base-commit"}})
        if path.endswith("/git/commits/base-commit"):
            return httpx.Response(200, json={"tree": {"sha": "base-tree"}})
        if path.endswith("/git/trees/base-tree"):
            return httpx.Response(
                200,
                json={
                    "tree": [{"path": "app.py", "type": "blob", "sha": "different-remote-content"}]
                },
            )
        return httpx.Response(404, json={})

    async def verify() -> None:
        integration = GithubIntegration(
            Fernet.generate_key().decode(), transport=httpx.MockTransport(handler)
        )
        with pytest.raises(GithubStaleRepositoryError):
            await integration.publish_pull_request(
                "github-token-that-is-long-enough",
                "https://github.com/octocat/demo",
                "test",
                "Improve value",
                [{"path": "app.py", "content": "new source"}],
                {"app.py": git_blob_sha(b"old source")},
            )
        assert not writes

    asyncio.run(verify())
