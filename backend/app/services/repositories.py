"""Bounded, read-only access to explicitly configured local repositories."""

import difflib
import hashlib
import os
import shutil
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from app.agents.contracts import FileChangeProposal, RepositoryFileContext, RepositorySnapshot

MAX_FILES = 40
MAX_FILE_CHARS = 8_000
MAX_TOTAL_CHARS = 80_000
MAX_WALK_ENTRIES = 5_000
MAX_WORKSPACE_FILES = 2_000
MAX_WORKSPACE_BYTES = 50_000_000
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


class RepositoryPathError(ValueError):
    """The selected repository is outside the configured safe local root."""


class RepositoryReader:
    """Read a small, sanitized repository snapshot without persisting source files."""

    def __init__(self, root: str) -> None:
        self.root = Path(root).expanduser().resolve()

    def snapshot(
        self,
        repository_directory: str,
        project_summary: str,
        *,
        revision: str | None = None,
    ) -> RepositorySnapshot:
        """Return bounded text excerpts from one directory below the configured root."""
        target = self._resolve_repository(repository_directory)
        files: list[RepositoryFileContext] = []
        total_chars = 0
        omitted = 0
        visited = 0
        exhausted = False

        for current, directories, filenames in os.walk(target, followlinks=False):
            current_path = Path(current)
            directories[:] = sorted(
                name
                for name in directories
                if name.casefold() not in SKIP_DIRECTORIES
                and (current_path / name).is_dir()
                and not (current_path / name).is_symlink()
            )
            for filename in sorted(filenames):
                visited += 1
                if visited > MAX_WALK_ENTRIES:
                    exhausted = True
                    break
                path = current_path / filename
                relative = path.relative_to(target).as_posix()
                if self._sensitive(path) or path.is_symlink() or not path.is_file():
                    continue
                if len(files) >= MAX_FILES or total_chars >= MAX_TOTAL_CHARS:
                    omitted += 1
                    continue
                try:
                    raw = path.read_bytes()
                    if b"\x00" in raw:
                        continue
                    decoded = raw.decode("utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                excerpt = decoded[:MAX_FILE_CHARS]
                remaining = MAX_TOTAL_CHARS - total_chars
                excerpt = excerpt[:remaining]
                if len(decoded) > len(excerpt):
                    omitted += 1
                if not excerpt:
                    omitted += 1
                    continue
                files.append(
                    RepositoryFileContext(
                        path=relative,
                        language=path.suffix.lstrip(".") or None,
                        excerpt=excerpt,
                    )
                )
                total_chars += len(excerpt)
            if exhausted:
                break

        if exhausted:
            omitted += 1
        if not files:
            raise RepositoryPathError("The selected repository contains no readable text files.")
        return RepositorySnapshot(
            project_summary=project_summary[:1000],
            revision=revision,
            files=files,
            omitted_file_count=omitted,
        )

    def copy_to_sandbox(self, repository_directory: str, destination: Path) -> int:
        """Copy safe project files into temporary scratch, rejecting symlinks and oversize trees."""
        target = self._resolve_repository(repository_directory)
        destination.mkdir(parents=True, exist_ok=True)
        count = 0
        total_bytes = 0
        for current, directories, filenames in os.walk(target, followlinks=False):
            current_path = Path(current)
            directories[:] = sorted(
                name
                for name in directories
                if name.casefold() not in SKIP_DIRECTORIES
                and (current_path / name).is_dir()
                and not (current_path / name).is_symlink()
            )
            for filename in sorted(filenames):
                source = current_path / filename
                if source.is_symlink() or not source.is_file() or self._sensitive(source):
                    continue
                count += 1
                if count > MAX_WORKSPACE_FILES:
                    raise RepositoryPathError("The repository exceeds the sandbox file limit.")
                total_bytes += source.stat().st_size
                if total_bytes > MAX_WORKSPACE_BYTES:
                    raise RepositoryPathError("The repository exceeds the sandbox size limit.")
                output = destination / source.relative_to(target)
                output.parent.mkdir(parents=True, exist_ok=True)
                if not output.resolve().is_relative_to(destination.resolve()):
                    raise RepositoryPathError("The sandbox copy path is outside its workspace.")
                shutil.copyfile(source, output)
        if count == 0:
            raise RepositoryPathError("The selected repository contains no readable files.")
        return count

    def capture_change_bases(
        self, repository_directory: str, changes: Sequence[FileChangeProposal]
    ) -> tuple[dict[str, str | None], str]:
        """Capture safe Git blob hashes and a review diff without changing the checkout."""
        target = self._resolve_repository(repository_directory)
        base_hashes: dict[str, str | None] = {}
        diff_parts: list[str] = []
        total_bytes = 0
        for change in changes:
            path_value = change.path
            content = change.content
            relative = PurePosixPath(path_value)
            if (
                path_value != path_value.strip()
                or "\x00" in path_value
                or "\\" in path_value
                or relative.is_absolute()
                or not relative.parts
                or any(part in {"", ".", ".."} for part in relative.parts)
                or ":" in relative.parts[0]
                or any(self._sensitive(Path(part)) for part in relative.parts)
                or any(part.casefold() in {".git", ".ssh"} for part in relative.parts)
            ):
                raise RepositoryPathError("A proposed change targets an unsafe file path.")
            source = target
            for part in relative.parts:
                source = source / part
                if source.is_symlink():
                    raise RepositoryPathError("A proposed change cannot target symbolic links.")
            if not source.resolve().is_relative_to(target):
                raise RepositoryPathError("A proposed change path is outside the repository.")
            before = ""
            base_hash: str | None = None
            if source.exists():
                if not source.is_file():
                    raise RepositoryPathError("A proposed change must target a regular text file.")
                raw = source.read_bytes()
                total_bytes += len(raw)
                if len(raw) > 1_000_000 or total_bytes > 5_000_000:
                    raise RepositoryPathError("The changed source files exceed the approval limit.")
                if b"\x00" in raw:
                    raise RepositoryPathError("Binary files cannot be included in an approval.")
                try:
                    before = raw.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise RepositoryPathError("Changed source files must be UTF-8 text.") from exc
                base_hash = git_blob_sha(raw)
            base_hashes[path_value] = base_hash
            diff_parts.extend(
                difflib.unified_diff(
                    before.splitlines(keepends=True),
                    content.splitlines(keepends=True),
                    fromfile=f"a/{path_value}" if base_hash else "/dev/null",
                    tofile=f"b/{path_value}",
                )
            )
        diff_text = "".join(diff_parts)
        if len(diff_text) > 100_000:
            raise RepositoryPathError("The proposed review diff exceeds the approval limit.")
        return base_hashes, diff_text

    def _resolve_repository(self, repository_directory: str) -> Path:
        if (
            not repository_directory
            or repository_directory != repository_directory.strip()
            or "\x00" in repository_directory
            or "\\" in repository_directory
        ):
            raise RepositoryPathError("Repository directory must be a safe relative path.")
        relative = PurePosixPath(repository_directory)
        if (
            relative.is_absolute()
            or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
            or ":" in relative.parts[0]
        ):
            raise RepositoryPathError("Repository directory must be a safe relative path.")
        if not self.root.exists() or not self.root.is_dir():
            raise RepositoryPathError("The configured repository root is unavailable.")

        target = self.root
        for part in relative.parts:
            target = target / part
            if target.is_symlink():
                raise RepositoryPathError("Repository directory cannot include symbolic links.")
        try:
            resolved = target.resolve(strict=True)
        except OSError as exc:
            raise RepositoryPathError("Repository directory was not found.") from exc
        if not resolved.is_relative_to(self.root) or not resolved.is_dir():
            raise RepositoryPathError("Repository directory is outside the configured root.")
        return resolved

    @staticmethod
    def _sensitive(path: Path) -> bool:
        name = path.name.casefold()
        return (
            name in SKIP_BASENAMES
            or name.startswith(".env.")
            or "secret" in name
            or "credential" in name
            or name.endswith(tuple(SKIP_EXTENSIONS))
        )


def git_blob_sha(content: bytes) -> str:
    """Return Git's SHA-1 object identifier for an exact file byte sequence."""
    return hashlib.sha1(f"blob {len(content)}\0".encode() + content).hexdigest()
