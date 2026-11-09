"""Local repository boundaries and generated path validation."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from app.agents.contracts import ChangeSetProposal, FileChangeProposal
from app.services.repositories import RepositoryPathError, RepositoryReader
from pydantic import ValidationError


def test_snapshot_omits_secrets_binary_and_generated_dependency_folders(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    project = root / "sample"
    (project / "src").mkdir(parents=True)
    (project / ".git").mkdir()
    (project / "node_modules" / "pkg").mkdir(parents=True)
    (project / "src" / "app.py").write_text("print('hello')\n", encoding="utf-8")
    (project / ".env").write_text("SECRET=do-not-read", encoding="utf-8")
    (project / "credentials.json").write_text("{}", encoding="utf-8")
    (project / ".git" / "config").write_text("private git config", encoding="utf-8")
    (project / "node_modules" / "pkg" / "index.js").write_text("ignored", encoding="utf-8")
    (project / "image.png").write_bytes(b"\x89PNG\x00data")

    snapshot = RepositoryReader(str(root)).snapshot("sample", "sample project")

    assert [file.path for file in snapshot.files] == ["src/app.py"]
    assert "do-not-read" not in str(snapshot.model_dump())


def test_snapshot_rejects_traversal_and_absolute_paths(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    root.mkdir()
    reader = RepositoryReader(str(root))

    for path in ("../outside", str(tmp_path / "outside"), "C:/outside", "repo\\..\\outside"):
        with pytest.raises(RepositoryPathError):
            reader.snapshot(path, "project")


def test_snapshot_is_bounded_to_forty_files(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    project = root / "sample"
    project.mkdir(parents=True)
    for index in range(45):
        (project / f"module_{index:02}.py").write_text("x = 1\n", encoding="utf-8")

    snapshot = RepositoryReader(str(root)).snapshot("sample", "sample project")

    assert len(snapshot.files) == 40
    assert snapshot.omitted_file_count == 5


@pytest.mark.parametrize(
    "path",
    ["../outside.py", ".env", "secrets.json", ".git/config", "C:/outside.py", "a\\b.py"],
)
def test_generated_change_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValidationError):
        FileChangeProposal(path=path, content="x", rationale="test")


def test_generated_change_set_rejects_duplicate_paths() -> None:
    change = {"path": "src/app.py", "content": "", "rationale": "test"}
    with pytest.raises(ValidationError):
        ChangeSetProposal(
            summary="duplicate",
            files=[change, change],
            acceptance_checks=["Check the changed file."],
        )


def test_approval_capture_creates_unified_diff_without_editing_checkout(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    project = root / "sample"
    project.mkdir(parents=True)
    source = project / "app.py"
    source.write_text("def value():\n    return 1\n", encoding="utf-8")

    base_hashes, diff = RepositoryReader(str(root)).capture_change_bases(
        "sample",
        [
            FileChangeProposal(
                path="app.py", content="def value():\n    return 2\n", rationale="Fix value"
            )
        ],
    )

    assert len(base_hashes["app.py"] or "") == 40
    assert "-    return 1" in diff
    assert "+    return 2" in diff
    assert source.read_text(encoding="utf-8") == "def value():\n    return 1\n"


def test_approval_capture_rejects_sensitive_or_symlink_paths(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    project = root / "sample"
    project.mkdir(parents=True)
    secret = project / "credentials.json"
    secret.write_text("private", encoding="utf-8")
    reader = RepositoryReader(str(root))

    with pytest.raises(RepositoryPathError):
        reader.capture_change_bases(
            "sample",
            [SimpleNamespace(path="credentials.json", content="changed")],
        )


def test_approval_capture_rejects_symlink_paths(tmp_path: Path) -> None:
    root = tmp_path / "repos"
    project = root / "sample"
    project.mkdir(parents=True)
    source = project / "app.py"
    source.write_text("value = 1\n", encoding="utf-8")
    link = project / "linked.py"
    try:
        link.symlink_to(source)
    except OSError:
        pytest.skip("Creating symlinks requires Windows developer mode or elevated privileges.")
    with pytest.raises(RepositoryPathError):
        RepositoryReader(str(root)).capture_change_bases(
            "sample",
            [FileChangeProposal(path="linked.py", content="changed", rationale="test")],
        )
