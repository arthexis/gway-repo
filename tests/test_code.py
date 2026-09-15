from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gway_repo.source import code_state


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def code_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")

    files = {
        "src/demo/__init__.py": "",
        "src/demo/core.py": (
            "HEADER = 1\n"
            "\n"
            "def first():\n"
            "    return 'first'\n"
            "\n"
            "class Worker:\n"
            "    def run(self, value):\n"
            "        return value + 1\n"
            "\n"
            "def duplicate():\n"
            "    return 1\n"
        ),
        "src/demo/other.py": "def duplicate():\n    return 2\n",
    }
    for name, content in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    _git(repo, "add", *files)
    _git(repo, "commit", "-m", "initial")
    _git(repo, "remote", "add", "origin", "https://github.com/example/demo.git")
    return repo


def test_code_reads_git_index_not_unstaged_worktree(code_repo: Path) -> None:
    target = code_repo / "src/demo/core.py"
    target.write_text("CHANGED = True\n")

    result = code_state(code_repo, file="src/demo/core.py", start_line=1, end_line=4)

    assert result["path"] == "src/demo/core.py"
    assert result["content"].startswith("HEADER = 1\n")
    assert "CHANGED" not in result["content"]
    assert result["head_sha"]
    assert result["tree_sha"]


def test_code_resolves_qualified_symbol_with_context(code_repo: Path) -> None:
    result = code_state(
        code_repo,
        symbol="demo.core.Worker.run",
        context=1,
    )

    assert result["path"] == "src/demo/core.py"
    assert result["symbol"]["qualified_name"] == "demo.core.Worker.run"
    assert result["start_line"] == 6
    assert "def run(self, value):" in result["content"]
    assert "return value + 1" in result["content"]


def test_code_file_can_disambiguate_simple_symbol(code_repo: Path) -> None:
    with pytest.raises(ValueError, match="ambiguous"):
        code_state(code_repo, symbol="duplicate")

    result = code_state(
        code_repo,
        file="src/demo/other.py",
        symbol="duplicate",
        context=0,
    )
    assert result["symbol"]["qualified_name"] == "demo.other.duplicate"
    assert result["content"] == "def duplicate():\n    return 2\n"


def test_code_bounds_lines_and_characters(code_repo: Path) -> None:
    line_bounded = code_state(
        code_repo,
        file="src/demo/core.py",
        max_lines=2,
        max_chars=10_000,
    )
    assert line_bounded["truncated"] is True
    assert line_bounded["end_line"] == 2

    char_bounded = code_state(
        code_repo,
        file="src/demo/core.py",
        start_line=3,
        end_line=4,
        max_lines=10,
        max_chars=8,
    )
    assert len(char_bounded["content"]) == 8
    assert char_bounded["char_truncated"] is True
    assert char_bounded["truncated"] is True


def test_code_rejects_invalid_or_untracked_selectors(code_repo: Path) -> None:
    with pytest.raises(ValueError, match="one of file or symbol"):
        code_state(code_repo)
    with pytest.raises(ValueError, match="not tracked"):
        code_state(code_repo, file="missing.py")
    with pytest.raises(ValueError, match="cannot be combined"):
        code_state(code_repo, symbol="demo.core.first", start_line=1)
    with pytest.raises(ValueError, match="max_lines"):
        code_state(code_repo, file="src/demo/core.py", max_lines=0)
