"""Source archive boundary checks for persistent Cloudflare repository storage."""

import io
import json
import stat
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest
from app.core.settings import ApiSettings
from app.services.r2_repositories import R2RepositoryStorage, RepositoryUploadError


def archive_with(entries: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in entries:
            archive.writestr(path, content)
    return buffer.getvalue()


def reader() -> R2RepositoryStorage:
    return object.__new__(R2RepositoryStorage)


def test_archive_validation_keeps_source_and_skips_secrets_and_build_outputs() -> None:
    files = reader()._read_archive(
        archive_with(
            [
                ("project/README.md", b"# Demo"),
                ("project/.env", b"PRIVATE=1"),
                ("project/src/.env.local", b"PRIVATE=2"),
                ("project/.git/config", b"git data"),
                ("project/node_modules/pkg/index.js", b"built dependency"),
            ]
        )
    )
    assert files == [("project/README.md", b"# Demo")]


@pytest.mark.parametrize("path", ["../escape.py", "/absolute.py", "a//b.py"])
def test_archive_validation_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(RepositoryUploadError):
        reader()._read_archive(archive_with([(path, b"unsafe")]))


def test_archive_path_validation_rejects_backslashes() -> None:
    with pytest.raises(RepositoryUploadError):
        reader()._safe_archive_path(r"folder\escape.py", False)


def test_archive_validation_rejects_symbolic_links() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        item = zipfile.ZipInfo("project/link.py")
        item.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(item, "../../outside.py")
    with pytest.raises(RepositoryUploadError, match="Symbolic links"):
        reader()._read_archive(buffer.getvalue())


def test_r2_binding_upload_download_and_replacement() -> None:
    objects: dict[str, bytes] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        key = unquote(request.url.path.lstrip("/"))
        if request.method == "GET":
            contents = objects.get(key)
            if contents is None:
                return httpx.Response(404)
            return httpx.Response(200, content=contents)
        if request.method == "PUT":
            objects[key] = request.content
            return httpx.Response(204)
        if request.method == "DELETE":
            objects.pop(key, None)
            return httpx.Response(204)
        raise AssertionError(f"Unexpected method: {request.method}")

    with tempfile.TemporaryDirectory() as repository_root:
        settings = ApiSettings(
            r2_bucket_name="agent-swe-repositories",
            agent_repositories_root=repository_root,
        )
        storage = R2RepositoryStorage(settings)
        storage.client.close()
        storage.client = httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)

        first_count = storage._upload_zip_sync(
            "owner_1", "project_1", archive_with([("src/main.py", b"print('one')")])
        )
        first_status = storage._source_status_sync("owner_1", "project_1")
        first_directory = storage._current_directory_sync("owner_1", "project_1")
        assert first_count == 1
        assert first_status["uploaded"] is True
        assert first_directory is not None
        assert (Path(first_directory) / "src" / "main.py").read_bytes() == b"print('one')"

        first_pointer_key = "users/owner_1/projects/project_1/current.json"
        first_revision = json.loads(objects[first_pointer_key])["revision"]
        second_count = storage._upload_zip_sync(
            "owner_1", "project_1", archive_with([("src/main.py", b"print('two')")])
        )
        second_directory = storage._current_directory_sync("owner_1", "project_1")

        assert second_count == 1
        assert second_directory is not None
        assert (Path(second_directory) / "src" / "main.py").read_bytes() == b"print('two')"
        old_directory = (
            Path(repository_root)
            / "users"
            / "owner_1"
            / "projects"
            / "project_1"
            / first_revision
        )
        assert not old_directory.exists()
        assert not any(
            key.startswith(f"users/owner_1/projects/project_1/{first_revision}/")
            for key in objects
        )
        storage.client.close()
