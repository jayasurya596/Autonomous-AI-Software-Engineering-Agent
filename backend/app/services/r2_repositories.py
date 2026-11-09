"""Owner-scoped source snapshots stored in a bound Cloudflare R2 bucket."""

import asyncio
import io
import json
import re
import shutil
import stat
import threading
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

import httpx

from app.core.settings import ApiSettings

MAX_REPOSITORY_FILES = 2_000
MAX_REPOSITORY_BYTES = 50_000_000
R2_BINDING_URL = "http://repository-storage.internal"
SKIP_DIRECTORIES = {
    ".git",
    ".ssh",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "target",
    ".next",
    "coverage",
}
SKIP_BASENAMES = {".env", "id_rsa", "id_ed25519", "secrets", "credentials"}
SKIP_EXTENSIONS = {".pem", ".key", ".p12", ".pfx"}


class RepositoryUploadError(ValueError):
    """A source archive failed its size, format, or path checks."""


class RepositoryStorageError(RuntimeError):
    """Persistent repository storage is unavailable."""


class R2RepositoryStorage:
    """Store immutable source revisions in R2 and materialize them on demand."""

    def __init__(self, settings: ApiSettings) -> None:
        if not settings.r2_repository_storage_configured:
            raise RepositoryStorageError("Cloudflare R2 repository storage is not configured.")
        self.client = httpx.Client(timeout=60, trust_env=False)
        self.bucket_name = settings.r2_bucket_name or ""
        self.repositories_root = Path(settings.agent_repositories_root).expanduser().resolve()
        self.max_upload_bytes = settings.max_repository_upload_bytes
        self._lock = threading.RLock()
        self._cached_snapshots: set[tuple[str, str, str]] = set()

    async def upload_zip(self, owner_uid: str, project_id: str, archive_bytes: bytes) -> int:
        """Validate and atomically replace the owner's current source snapshot."""
        return await asyncio.to_thread(
            self._upload_zip_sync, owner_uid, project_id, archive_bytes
        )

    async def source_status(self, owner_uid: str, project_id: str) -> dict[str, Any]:
        """Return safe metadata for the currently uploaded repository snapshot."""
        return await asyncio.to_thread(self._source_status_sync, owner_uid, project_id)

    async def current_directory(self, owner_uid: str, project_id: str) -> str | None:
        """Download the current immutable revision into the container's workspace."""
        return await asyncio.to_thread(self._current_directory_sync, owner_uid, project_id)

    def _upload_zip_sync(self, owner_uid: str, project_id: str, archive_bytes: bytes) -> int:
        if not archive_bytes or len(archive_bytes) > self.max_upload_bytes:
            raise RepositoryUploadError("Upload a ZIP archive smaller than the configured limit.")
        files = self._read_archive(archive_bytes)
        base_prefix = self._base_prefix(owner_uid, project_id)
        revision = uuid.uuid4().hex
        revision_prefix = f"{base_prefix}/{revision}"

        with self._lock:
            previous = self._read_pointer(base_prefix)
            try:
                for path, contents in files:
                    self._put_object(f"{revision_prefix}/{path}", contents)
                uploaded_at = datetime.now(UTC).isoformat()
                pointer = json.dumps(
                    {
                        "revision": revision,
                        "file_count": len(files),
                        "files": [path for path, _contents in files],
                        "uploaded_at": uploaded_at,
                    },
                    separators=(",", ":"),
                ).encode("utf-8")
                self._put_object(f"{base_prefix}/current.json", pointer, "application/json")
            except Exception as exc:
                for path, _contents in files:
                    try:
                        self._delete_object(f"{revision_prefix}/{path}")
                    except RepositoryStorageError:
                        pass
                if isinstance(exc, RepositoryStorageError):
                    raise
                raise RepositoryStorageError(
                    "Cloudflare R2 could not save the repository snapshot."
                ) from exc

            if previous is not None:
                self._delete_revision_objects(base_prefix, previous)
                previous_revision = previous.get("revision")
                if isinstance(previous_revision, str):
                    old_directory = self._local_directory(
                        base_prefix, previous_revision
                    )
                    if old_directory.exists():
                        shutil.rmtree(old_directory, ignore_errors=True)
                    self._cached_snapshots.discard(
                        (owner_uid, project_id, previous_revision)
                    )
        return len(files)

    def _source_status_sync(self, owner_uid: str, project_id: str) -> dict[str, Any]:
        pointer = self._read_pointer(self._base_prefix(owner_uid, project_id))
        if pointer is None:
            return {"uploaded": False, "file_count": 0, "uploaded_at": None}
        return {
            "uploaded": True,
            "file_count": pointer["file_count"],
            "uploaded_at": pointer["uploaded_at"],
        }

    def _current_directory_sync(self, owner_uid: str, project_id: str) -> str | None:
        base_prefix = self._base_prefix(owner_uid, project_id)
        with self._lock:
            pointer = self._read_pointer(base_prefix)
            if pointer is None:
                return None
            revision = pointer["revision"]
            cache_key = (owner_uid, project_id, revision)
            destination = self._local_directory(base_prefix, revision)
            if cache_key in self._cached_snapshots and destination.is_dir():
                return str(destination)

            temporary = destination.with_name(f".{revision}.{uuid.uuid4().hex}.tmp")
            temporary.mkdir(parents=True, exist_ok=False)
            try:
                for filename in pointer["files"]:
                    safe_path = self._safe_archive_path(filename, False)
                    contents = self._get_object(f"{base_prefix}/{revision}/{safe_path}")
                    if contents is None:
                        raise RepositoryStorageError(
                            "The current repository snapshot is incomplete in R2."
                        )
                    target = (temporary / Path(*PurePosixPath(safe_path).parts)).resolve()
                    try:
                        target.relative_to(temporary.resolve())
                    except ValueError as exc:
                        raise RepositoryStorageError(
                            "The stored repository snapshot contains an unsafe path."
                        ) from exc
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(contents)

                destination.parent.mkdir(parents=True, exist_ok=True)
                if destination.exists():
                    shutil.rmtree(destination)
                temporary.replace(destination)
                self._cached_snapshots.add(cache_key)
            except Exception:
                shutil.rmtree(temporary, ignore_errors=True)
                raise
            return str(destination)

    def _read_pointer(self, base_prefix: str) -> dict[str, Any] | None:
        contents = self._get_object(f"{base_prefix}/current.json")
        if contents is None:
            return None
        try:
            pointer = json.loads(contents)
            revision = self._safe_revision(pointer.get("revision"))
            file_count = int(pointer.get("file_count", 0))
            uploaded_at = pointer.get("uploaded_at")
            filenames = pointer.get("files")
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RepositoryStorageError(
                "The stored repository snapshot metadata is invalid."
            ) from exc
        if (
            revision is None
            or not isinstance(filenames, list)
            or not filenames
            or len(filenames) != file_count
            or len(filenames) > MAX_REPOSITORY_FILES
            or not isinstance(uploaded_at, str)
        ):
            raise RepositoryStorageError("The stored repository snapshot metadata is invalid.")
        try:
            safe_filenames = [self._safe_archive_path(name, False) for name in filenames]
        except (RepositoryUploadError, TypeError) as exc:
            raise RepositoryStorageError(
                "The stored repository snapshot metadata is invalid."
            ) from exc
        if len(set(safe_filenames)) != len(safe_filenames):
            raise RepositoryStorageError("The stored repository snapshot metadata is invalid.")
        return {
            "revision": revision,
            "file_count": file_count,
            "files": safe_filenames,
            "uploaded_at": uploaded_at,
        }

    def _delete_revision_objects(self, base_prefix: str, pointer: dict[str, Any]) -> None:
        revision = pointer.get("revision")
        filenames = pointer.get("files")
        if not isinstance(revision, str) or not isinstance(filenames, list):
            return
        for filename in filenames:
            try:
                safe_path = self._safe_archive_path(filename, False)
                self._delete_object(f"{base_prefix}/{revision}/{safe_path}")
            except (RepositoryUploadError, RepositoryStorageError):
                continue

    def _local_directory(self, base_prefix: str, revision: str) -> Path:
        destination = self.repositories_root.joinpath(*base_prefix.split("/"), revision)
        try:
            destination.resolve().relative_to(self.repositories_root)
        except ValueError as exc:
            raise RepositoryStorageError("The repository workspace path is invalid.") from exc
        return destination

    def _get_object(self, key: str) -> bytes | None:
        try:
            response = self.client.get(self._object_url(key))
        except httpx.HTTPError as exc:
            raise RepositoryStorageError(
                "Cloudflare R2 repository storage is unavailable."
            ) from exc
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise RepositoryStorageError("Cloudflare R2 repository storage is unavailable.")
        return response.content

    def _put_object(
        self, key: str, contents: bytes, content_type: str = "application/octet-stream"
    ) -> None:
        try:
            response = self.client.put(
                self._object_url(key), content=contents, headers={"content-type": content_type}
            )
        except httpx.HTTPError as exc:
            raise RepositoryStorageError(
                "Cloudflare R2 repository storage is unavailable."
            ) from exc
        if response.status_code not in {200, 201, 204}:
            raise RepositoryStorageError("Cloudflare R2 repository storage is unavailable.")

    def _delete_object(self, key: str) -> None:
        try:
            response = self.client.delete(self._object_url(key))
        except httpx.HTTPError as exc:
            raise RepositoryStorageError(
                "Cloudflare R2 repository storage is unavailable."
            ) from exc
        if response.status_code not in {200, 202, 204, 404}:
            raise RepositoryStorageError("Cloudflare R2 repository storage is unavailable.")

    @staticmethod
    def _object_url(key: str) -> str:
        return f"{R2_BINDING_URL}/{quote(key, safe='/')}"

    def _read_archive(self, archive_bytes: bytes) -> list[tuple[str, bytes]]:
        try:
            archive = zipfile.ZipFile(io.BytesIO(archive_bytes))
        except (OSError, zipfile.BadZipFile) as exc:
            raise RepositoryUploadError("The uploaded file is not a valid ZIP archive.") from exc

        files: list[tuple[str, bytes]] = []
        total_bytes = 0
        try:
            entries = archive.infolist()
            if len(entries) > MAX_REPOSITORY_FILES * 2:
                raise RepositoryUploadError("The archive contains too many files.")
            for info in entries:
                path = self._safe_archive_path(info.filename, info.is_dir())
                if info.is_dir():
                    continue
                if info.flag_bits & 0x1:
                    raise RepositoryUploadError(
                        "Password-protected ZIP archives are not supported."
                    )
                mode = (info.external_attr >> 16) & 0o170000
                if mode == stat.S_IFLNK:
                    raise RepositoryUploadError(
                        "Symbolic links are not allowed in source archives."
                    )
                if self._should_skip(path):
                    continue
                if info.file_size > MAX_REPOSITORY_BYTES:
                    raise RepositoryUploadError("A file in the archive is too large.")
                total_bytes += info.file_size
                if total_bytes > MAX_REPOSITORY_BYTES:
                    raise RepositoryUploadError("The uncompressed archive exceeds the size limit.")
                with archive.open(info) as entry:
                    contents = entry.read(MAX_REPOSITORY_BYTES + 1)
                if len(contents) != info.file_size:
                    raise RepositoryUploadError("The ZIP archive contains an invalid file entry.")
                files.append((path, contents))
                if len(files) > MAX_REPOSITORY_FILES:
                    raise RepositoryUploadError("The archive contains too many source files.")
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise RepositoryUploadError("The ZIP archive could not be read safely.") from exc
        finally:
            archive.close()
        if not files:
            raise RepositoryUploadError("The ZIP archive contains no usable source files.")
        return files

    @staticmethod
    def _safe_archive_path(filename: str, is_directory: bool) -> str:
        if (
            not isinstance(filename, str)
            or not filename
            or "\\" in filename
            or "\x00" in filename
            or filename.startswith("/")
            or (is_directory and not filename.endswith("/"))
        ):
            raise RepositoryUploadError("The archive contains an unsafe file path.")
        parts = filename.rstrip("/").split("/")
        if (
            not parts
            or any(part in {"", ".", ".."} for part in parts)
            or ":" in parts[0]
        ):
            raise RepositoryUploadError("The archive contains an unsafe file path.")
        return PurePosixPath(*parts).as_posix()

    @staticmethod
    def _should_skip(path: str) -> bool:
        parts = PurePosixPath(path).parts
        if any(part.casefold() in SKIP_DIRECTORIES for part in parts[:-1]):
            return True
        name = PurePosixPath(path).name.casefold()
        return (
            name in SKIP_BASENAMES
            or name.startswith(".env.")
            or "secret" in name
            or "credential" in name
            or name.endswith(tuple(SKIP_EXTENSIONS))
        )

    @staticmethod
    def _base_prefix(owner_uid: str, project_id: str) -> str:
        for value in (owner_uid, project_id):
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
                raise RepositoryStorageError(
                    "The repository owner or project identifier is invalid."
                )
        return f"users/{owner_uid}/projects/{project_id}"

    @staticmethod
    def _safe_revision(value: Any) -> str | None:
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
            return None
        return value
