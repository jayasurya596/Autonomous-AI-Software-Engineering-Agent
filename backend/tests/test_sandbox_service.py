"""Sandbox command profiles and temporary workspace boundaries."""

import os
from pathlib import Path

import pytest
from app.agents.contracts import FileChangeProposal
from app.services.repositories import RepositoryReader
from app.services.sandbox import SandboxService


def test_test_commands_are_fixed_allowlisted_profiles() -> None:
    assert SandboxService._test_command("python") == [
        "python",
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
    ]
    assert SandboxService._test_command("node") == ["node", "--test"]


def test_docker_invocation_enforces_container_boundaries(tmp_path: Path) -> None:
    command = SandboxService()._docker_command(
        tmp_path,
        "agent-sandbox-python:local",
        "agent-sandbox-test",
        ["python", "-m", "pytest"],
    )
    assert "--network" in command and command[command.index("--network") + 1] == "none"
    assert "--cap-drop" in command and command[command.index("--cap-drop") + 1] == "ALL"
    assert "--read-only" in command
    assert command[command.index("--user") + 1] == "65532:65532"
    assert command[command.index("--memory") + 1] == "512m"
    assert command[command.index("--cpus") + 1] == "1"
    assert command[command.index("--pids-limit") + 1] == "64"
    mount = command[command.index("--mount") + 1]
    assert mount.endswith("target=/workspace,readonly")
    assert "docker.sock" not in str(command)


def test_sandbox_copy_excludes_secrets_and_source_is_not_modified(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    project = root / "sample"
    project.mkdir(parents=True)
    source_file = project / "app.py"
    source_file.write_text("value = 1\n", encoding="utf-8")
    (project / ".env").write_text("SECRET=private", encoding="utf-8")
    workspace = tmp_path / "scratch"

    count = RepositoryReader(str(root)).copy_to_sandbox("sample", workspace)

    assert count == 1
    assert (workspace / "app.py").read_text(encoding="utf-8") == "value = 1\n"
    assert not (workspace / ".env").exists()
    SandboxService._apply_changes(
        workspace,
        [FileChangeProposal(path="app.py", content="value = 2\n", rationale="test proposal")],
    )
    assert (workspace / "app.py").read_text(encoding="utf-8") == "value = 2\n"
    assert source_file.read_text(encoding="utf-8") == "value = 1\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX workspace permissions are Linux-specific")
def test_repository_sandbox_root_is_traversable_by_the_unprivileged_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repos"
    project = root / "sample"
    project.mkdir(parents=True)
    (project / "app.py").write_text("value = 1\n", encoding="utf-8")
    service = SandboxService()

    def inspect_workspace(workspace: Path, runtime: str) -> dict[str, object]:
        assert runtime == "python"
        assert workspace.stat().st_mode & 0o777 == 0o755
        assert (workspace / "app.py").read_text(encoding="utf-8") == "value = 1\n"
        return {}

    monkeypatch.setattr(service, "run_workspace", inspect_workspace)

    service.run_repository(RepositoryReader(str(root)), "sample", [], "python")


def test_generated_change_cannot_escape_sandbox_workspace(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        FileChangeProposal(path="../outside.py", content="x = 1", rationale="unsafe")
