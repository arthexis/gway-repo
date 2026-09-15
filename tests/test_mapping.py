from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gway_repo.mapping import files_state, repository_map


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def mapped_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")

    files = {
        "src/demo/__init__.py": "",
        "src/demo/core.py": "def value():\n    return 1\n",
        "tests/test_core.py": "def test_value():\n    assert True\n",
        ".github/workflows/ci.yml": "name: ci\n",
        "pyproject.toml": "[project]\nname = 'demo'\n",
        "README.md": "# Demo\n",
        "scripts/build.sh": "#!/bin/sh\n",
        "assets/logo.png": "not-really-a-png",
    }
    for name, content in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    (repo / "untracked.py").write_text("UNTRACKED = True\n")
    _git(repo, "add", *files)
    _git(repo, "commit", "-m", "initial")
    _git(repo, "remote", "add", "origin", "https://github.com/example/demo.git")
    return repo


def test_repository_map_classifies_only_tracked_files(mapped_repo: Path) -> None:
    result = repository_map(mapped_repo)

    assert result["repository"] == "example/demo"
    assert result["summary"]["tracked_files"] == 8
    assert result["summary"]["packages"] == ["demo"]
    assert result["summary"]["tests"] == 1
    assert result["summary"]["workflows"] == 1
    assert result["packages"] == [{"name": "demo", "path": "src/demo", "layout": "src"}]

    by_path = {item["path"]: item for item in result["files"]}
    assert "untracked.py" not in by_path
    assert by_path["src/demo/core.py"]["kind"] == "python"
    assert by_path["src/demo/core.py"]["module"] == "demo.core"
    assert by_path["tests/test_core.py"]["kind"] == "test"
    assert by_path["tests/test_core.py"]["likely_source"] == "src/demo/core.py"
    assert by_path[".github/workflows/ci.yml"]["kind"] == "workflow"
    assert by_path["pyproject.toml"]["kind"] == "config"
    assert by_path["README.md"]["kind"] == "documentation"
    assert by_path["scripts/build.sh"]["kind"] == "script"
    assert by_path["assets/logo.png"]["kind"] == "asset"


def test_repository_map_file_limit_is_bounded(mapped_repo: Path) -> None:
    result = repository_map(mapped_repo, file_limit=2)

    assert len(result["files"]) == 2
    assert result["files_truncated"] is True
    assert result["summary"]["tracked_files"] == 8


def test_staged_structure_change_invalidates_cache(mapped_repo: Path) -> None:
    first = repository_map(mapped_repo)
    head = first["head_sha"]

    new_file = mapped_repo / "src/demo/new_module.py"
    new_file.write_text("VALUE = 2\n")
    _git(mapped_repo, "add", "src/demo/new_module.py")

    second = repository_map(mapped_repo)

    assert second["head_sha"] == head
    assert second["tree_sha"] != first["tree_sha"]
    assert second["summary"]["tracked_files"] == 9
    assert any(item["path"] == "src/demo/new_module.py" for item in second["files"])


def test_files_state_filters_and_bounds(mapped_repo: Path) -> None:
    tests = files_state(mapped_repo, kind="test")
    python_files = files_state(mapped_repo, extension="py", limit=1)

    assert tests["total_count"] == 1
    assert tests["files"][0]["path"] == "tests/test_core.py"
    assert python_files["total_count"] == 3
    assert len(python_files["files"]) == 1
    assert python_files["truncated"] is True
    assert python_files["filters"]["extension"] == ".py"


def test_flat_package_layout_is_detected(tmp_path: Path) -> None:
    repo = tmp_path / "flat"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    package = repo / "flatpkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "feature.py").write_text("VALUE = 1\n")
    _git(repo, "add", "flatpkg")
    _git(repo, "commit", "-m", "initial")

    result = repository_map(repo)
    by_path = {item["path"]: item for item in result["files"]}

    assert result["packages"] == [
        {"name": "flatpkg", "path": "flatpkg", "layout": "flat"}
    ]
    assert by_path["flatpkg/feature.py"]["module"] == "flatpkg.feature"


def test_negative_limits_are_rejected(mapped_repo: Path) -> None:
    with pytest.raises(ValueError, match="file_limit"):
        repository_map(mapped_repo, file_limit=-1)
    with pytest.raises(ValueError, match="limit"):
        files_state(mapped_repo, limit=-1)
