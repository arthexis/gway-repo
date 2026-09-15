from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gway_repo.discovery import find_root, github_repository, repository_info


def _git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> Path:
    repo = tmp_path / "demo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "README.md").write_text("demo\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")
    _git(repo, "remote", "add", "origin", "git@github.com:arthexis/demo.git")
    _git(repo, "checkout", "-b", "feature/test")
    return repo


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("git@github.com:arthexis/gway-repo.git", "arthexis/gway-repo"),
        ("https://github.com/arthexis/gway-repo.git", "arthexis/gway-repo"),
        ("ssh://git@github.com/arthexis/gway-repo.git", "arthexis/gway-repo"),
        ("https://example.com/arthexis/gway-repo.git", None),
    ],
)
def test_github_repository(url: str, expected: str | None) -> None:
    assert github_repository(url) == expected


def test_find_root_from_nested_directory(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    nested = repo / "src" / "package"
    nested.mkdir(parents=True)

    assert find_root(nested) == repo.resolve()


def test_repository_info_is_structured_and_local(tmp_path: Path) -> None:
    repo = _repository(tmp_path)

    result = repository_info(repo)

    assert result["root"] == str(repo.resolve())
    assert result["branch"] == "feature/test"
    assert len(str(result["head"])) == 40
    assert result["remotes"] == {"origin": "git@github.com:arthexis/demo.git"}
    assert result["primary_remote"] == "origin"
    assert result["repository"] == "arthexis/demo"
    assert result["default_branch"] == "main"
