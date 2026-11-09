"""Restricted Docker execution for fixed local test profiles."""

import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Literal, TypedDict

from app.agents.contracts import FileChangeProposal
from app.services.repositories import RepositoryPathError, RepositoryReader

SandboxRuntime = Literal["python", "node"]
SandboxStatus = Literal["passed", "failed", "timed_out", "output_limited"]
MAX_OUTPUT_BYTES = 20_000


class SandboxUnavailableError(RuntimeError):
    """Docker or the configured sandbox image is not ready."""


class SandboxResult(TypedDict):
    status: SandboxStatus
    exit_code: int | None
    output: str
    duration_ms: int
    image: str
    command: list[str]


class SandboxService:
    """Run allowlisted checks in an isolated, ephemeral, networkless container."""

    def __init__(
        self,
        *,
        docker_binary: str = "docker",
        python_image: str = "agent-sandbox-python:local",
        node_image: str = "agent-sandbox-node:local",
        timeout_seconds: int = 120,
        memory_limit: str = "512m",
        cpu_limit: str = "1",
        pids_limit: int = 64,
    ) -> None:
        self.docker_binary = docker_binary
        self.images: dict[SandboxRuntime, str] = {
            "python": python_image,
            "node": node_image,
        }
        self.timeout_seconds = timeout_seconds
        self.memory_limit = memory_limit
        self.cpu_limit = cpu_limit
        self.pids_limit = pids_limit

    def run_repository(
        self,
        reader: RepositoryReader,
        repository_directory: str,
        changes: list[FileChangeProposal],
        runtime: SandboxRuntime,
    ) -> SandboxResult:
        """Copy a safe tree to temporary scratch, apply the proposal there, then run tests."""
        with tempfile.TemporaryDirectory(prefix="agent-sandbox-") as temporary:
            workspace = Path(temporary)
            # The sandbox runs as an unprivileged UID, so let it traverse the
            # disposable workspace root while keeping the source read-only.
            workspace.chmod(0o755)
            reader.copy_to_sandbox(repository_directory, workspace)
            self._apply_changes(workspace, changes)
            return self.run_workspace(workspace, runtime)

    def run_workspace(self, workspace: Path, runtime: SandboxRuntime) -> SandboxResult:
        """Run one fixed test command with no network, privileges, or writable source mount."""
        if runtime not in self.images:
            raise ValueError("Unsupported sandbox runtime.")
        image = self.images[runtime]
        command = self._test_command(runtime)
        self._check_image(image)
        container_name = f"agent-sandbox-{uuid.uuid4().hex[:16]}"
        docker_command = self._docker_command(workspace, image, container_name, command)
        return self._execute(docker_command, container_name, image, command)

    def _docker_command(
        self,
        workspace: Path,
        image: str,
        container_name: str,
        test_command: list[str],
    ) -> list[str]:
        return [
            self.docker_binary,
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--read-only",
            "--memory",
            self.memory_limit,
            "--cpus",
            self.cpu_limit,
            "--pids-limit",
            str(self.pids_limit),
            "--user",
            "65532:65532",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--workdir",
            "/workspace",
            "--mount",
            f"type=bind,source={workspace.resolve()},target=/workspace,readonly",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "TMPDIR=/tmp",
            image,
            *test_command,
        ]

    @staticmethod
    def _test_command(runtime: SandboxRuntime) -> list[str]:
        if runtime == "python":
            return ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
        return ["node", "--test"]

    def _check_image(self, image: str) -> None:
        try:
            result = subprocess.run(
                [self.docker_binary, "image", "inspect", image],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SandboxUnavailableError("Docker is not available to the API process.") from exc
        if result.returncode != 0:
            raise SandboxUnavailableError("The configured sandbox image has not been built.")

    def _execute(
        self,
        command: list[str],
        container_name: str,
        image: str,
        test_command: list[str],
    ) -> SandboxResult:
        started = time.monotonic()
        output: list[bytes] = []
        output_size = 0
        output_exceeded = threading.Event()
        process: subprocess.Popen[bytes] | None = None
        status: SandboxStatus | None = None
        exit_code: int | None = None

        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
        except OSError as exc:
            raise SandboxUnavailableError("Docker could not start a sandbox container.") from exc

        def read_output() -> None:
            nonlocal output_size
            assert process is not None and process.stdout is not None
            while chunk := process.stdout.read(4096):
                available = MAX_OUTPUT_BYTES - output_size
                if available > 0:
                    output.append(chunk[:available])
                    output_size += min(len(chunk), available)
                if len(chunk) > available:
                    output_exceeded.set()
                    return

        reader_thread = threading.Thread(target=read_output, daemon=True)
        reader_thread.start()
        deadline = started + self.timeout_seconds
        try:
            while process.poll() is None:
                if output_exceeded.is_set():
                    status = "output_limited"
                    self._stop_container(container_name)
                    process.terminate()
                    break
                if time.monotonic() >= deadline:
                    status = "timed_out"
                    self._stop_container(container_name)
                    process.terminate()
                    break
                time.sleep(0.05)
            try:
                exit_code = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                exit_code = process.wait(timeout=5)
        finally:
            reader_thread.join(timeout=5)
            if process.stdout is not None:
                process.stdout.close()
            self._stop_container(container_name)

        if status is None:
            status = "passed" if exit_code == 0 else "failed"
        text_output = b"".join(output).decode("utf-8", errors="replace")
        if output_exceeded.is_set():
            text_output += "\n[Output limit reached; container stopped.]"
        if status == "timed_out":
            text_output += "\n[Time limit reached; container stopped.]"
        return {
            "status": status,
            "exit_code": exit_code,
            "output": text_output,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "image": image,
            "command": test_command,
        }

    def _stop_container(self, container_name: str) -> None:
        try:
            subprocess.run(
                [self.docker_binary, "rm", "--force", container_name],
                capture_output=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return

    @staticmethod
    def _apply_changes(workspace: Path, changes: list[FileChangeProposal]) -> None:
        for change in changes:
            relative = PurePosixPath(change.path)
            if (
                relative.is_absolute()
                or not relative.parts
                or any(part in {"", ".", ".."} for part in relative.parts)
                or "\\" in change.path
                or ":" in relative.parts[0]
            ):
                raise RepositoryPathError("Generated change path is unsafe.")
            output = workspace.joinpath(*relative.parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            if not output.resolve().is_relative_to(workspace.resolve()):
                raise RepositoryPathError("Generated change path is outside the sandbox workspace.")
            output.write_text(change.content, encoding="utf-8", newline="")
