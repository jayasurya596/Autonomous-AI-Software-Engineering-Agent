"""Encrypted credential handling and explicit, review-gated GitHub pull requests."""

import re
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from cryptography.fernet import Fernet, InvalidToken

GITHUB_API = "https://api.github.com"
API_VERSION = "2022-11-28"


class GithubIntegrationError(RuntimeError):
    """Safe-to-display GitHub integration failure without credential details."""


class GithubStaleRepositoryError(GithubIntegrationError):
    """The locally reviewed source differs from the target repository branch."""


def parse_github_repository(repository_url: str) -> tuple[str, str]:
    """Accept a canonical GitHub HTTPS repository URL only."""
    try:
        parsed = urlsplit(repository_url)
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
            or parsed.query
            or parsed.fragment
            or len(parts) != 2
        ):
            raise ValueError
        owner, repo = parts
        if repo.endswith(".git"):
            repo = repo[:-4]
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", owner) or not re.fullmatch(
            r"[A-Za-z0-9_.-]{1,100}", repo
        ):
            raise ValueError
        return owner, repo
    except (ValueError, TypeError) as exc:
        raise GithubIntegrationError("Project repository must be a GitHub HTTPS URL.") from exc


class GithubIntegration:
    """Validate and encrypt personal access tokens, then publish a reviewed patch."""

    def __init__(
        self,
        encryption_key: str | None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if not encryption_key:
            raise GithubIntegrationError(
                "GitHub token encryption is not configured. Set GITHUB_CREDENTIALS_ENCRYPTION_KEY."
            )
        try:
            self.cipher = Fernet(encryption_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise GithubIntegrationError(
                "GitHub token encryption key is invalid; generate a Fernet key."
            ) from exc
        self.transport = transport

    async def verify_token(self, token: str) -> tuple[str, str]:
        if token != token.strip() or any(char.isspace() for char in token):
            raise GithubIntegrationError("GitHub token format is invalid.")
        data, response = await self._request("GET", "/user", token)
        username = data.get("login")
        if not isinstance(username, str) or not username:
            raise GithubIntegrationError("GitHub did not return an account for this token.")
        scopes = response.headers.get("X-OAuth-Scopes", "")
        return username, scopes

    def encrypt_token(self, token: str) -> str:
        return self.cipher.encrypt(token.encode("utf-8")).decode("ascii")

    def decrypt_token(self, encrypted_token: str) -> str:
        try:
            return self.cipher.decrypt(encrypted_token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeEncodeError) as exc:
            raise GithubIntegrationError("Stored GitHub credentials cannot be decrypted.") from exc

    async def publish_pull_request(
        self,
        token: str,
        repository_url: str,
        approval_id: str,
        title: str,
        changes: list[dict[str, Any]],
        base_hashes: dict[str, str | None],
    ) -> tuple[str, str]:
        owner, repo = parse_github_repository(repository_url)
        prefix = f"/repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
        repository, _ = await self._request("GET", prefix, token)
        base_branch = repository.get("default_branch")
        if not isinstance(base_branch, str) or not base_branch:
            raise GithubIntegrationError("GitHub repository has no default branch.")
        ref_path = f"{prefix}/git/ref/heads/{quote(base_branch, safe='/')}"
        branch_ref, _ = await self._request("GET", ref_path, token)
        base_sha = branch_ref.get("object", {}).get("sha")
        if not isinstance(base_sha, str):
            raise GithubIntegrationError("GitHub default branch could not be resolved.")
        commit, _ = await self._request("GET", f"{prefix}/git/commits/{base_sha}", token)
        base_tree = commit.get("tree", {}).get("sha")
        if not isinstance(base_tree, str):
            raise GithubIntegrationError("GitHub default branch tree could not be read.")
        tree, _ = await self._request("GET", f"{prefix}/git/trees/{base_tree}?recursive=1", token)
        current_paths = {
            item.get("path"): item.get("sha")
            for item in tree.get("tree", [])
            if isinstance(item, dict) and item.get("type") == "blob"
        }

        for path, expected_sha in base_hashes.items():
            remote_sha = current_paths.get(path)
            if remote_sha != expected_sha:
                raise GithubStaleRepositoryError(
                    f"Remote file {path} changed after review. "
                    "Create and test a fresh proposal."
                )

        tree_entries: list[dict[str, str]] = []
        for change in changes:
            file_path = change.get("path")
            content = change.get("content")
            if not isinstance(file_path, str) or not isinstance(content, str):
                raise GithubIntegrationError("The approved change set is invalid.")
            blob, _ = await self._request(
                "POST",
                f"{prefix}/git/blobs",
                token,
                json={"content": content, "encoding": "utf-8"},
            )
            blob_sha = blob.get("sha")
            if not isinstance(blob_sha, str):
                raise GithubIntegrationError("GitHub did not accept an approved file.")
            tree_entries.append(
                {"path": file_path, "mode": "100644", "type": "blob", "sha": blob_sha}
            )

        new_tree, _ = await self._request(
            "POST",
            f"{prefix}/git/trees",
            token,
            json={"base_tree": base_tree, "tree": tree_entries},
        )
        new_tree_sha = new_tree.get("sha")
        if not isinstance(new_tree_sha, str):
            raise GithubIntegrationError("GitHub could not build the approved change tree.")
        new_commit, _ = await self._request(
            "POST",
            f"{prefix}/git/commits",
            token,
            json={
                "message": f"{title}\n\nReviewed with Autonomous AI Software Engineering Agent",
                "tree": new_tree_sha,
                "parents": [base_sha],
            },
        )
        commit_sha = new_commit.get("sha")
        if not isinstance(commit_sha, str):
            raise GithubIntegrationError("GitHub could not create the approved commit.")
        branch = f"codex/approval-{approval_id}"
        await self._request(
            "POST",
            f"{prefix}/git/refs",
            token,
            json={"ref": f"refs/heads/{branch}", "sha": commit_sha},
        )
        pull, _ = await self._request(
            "POST",
            f"{prefix}/pulls",
            token,
            json={
                "title": title,
                "head": branch,
                "base": base_branch,
                "body": (
                    "This pull request contains a change set explicitly reviewed and approved "
                    "in the Autonomous AI Software Engineering Agent.\n\n"
                    "The approved change passed the configured isolated sandbox test profile. "
                    "Review the code and CI results before merging."
                ),
            },
        )
        pull_url = pull.get("html_url")
        if not isinstance(pull_url, str) or not pull_url.startswith(
            f"https://github.com/{owner}/{repo}/pull/"
        ):
            raise GithubIntegrationError("GitHub did not return a valid pull request link.")
        return branch, pull_url

    async def _request(
        self,
        method: str,
        path: str,
        token: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], httpx.Response]:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "autonomous-ai-software-engineering-agent",
        }
        try:
            async with httpx.AsyncClient(
                base_url=GITHUB_API,
                headers=headers,
                timeout=20,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response = await client.request(method, path, json=json)
        except httpx.HTTPError as exc:
            raise GithubIntegrationError("GitHub could not be reached. Try again later.") from exc
        if response.status_code >= 400:
            if response.status_code == 409:
                raise GithubStaleRepositoryError(
                    "GitHub rejected the operation because the repository changed. "
                    "Refresh and review again."
                )
            if response.status_code in {401, 403}:
                raise GithubIntegrationError(
                    "GitHub denied the request. Check token access and repository permissions."
                )
            if response.status_code == 404:
                raise GithubIntegrationError(
                    "The GitHub repository or required resource was not found."
                )
            raise GithubIntegrationError(
                f"GitHub request failed with status {response.status_code}."
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise GithubIntegrationError("GitHub returned an unreadable response.") from exc
        if not isinstance(data, dict):
            raise GithubIntegrationError("GitHub returned an unexpected response.")
        return data, response
