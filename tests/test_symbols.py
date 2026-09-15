from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gway_repo.symbols import symbols_state


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def symbol_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")

    files = {
        "src/demo/__init__.py": "",
        "src/demo/core.py": (
            "import os as operating\n"
            "from .helpers import helper as h\n"
            "\n"
            "@registered\n"
            "class Worker(Base, metaclass=Meta):\n"
            "    @classmethod\n"
            "    async def run(cls, x: int = 1) -> str:\n"
            "        import json\n"
            "        return str(x)\n"
            "\n"
            "def top(a, *, flag=False):\n"
            "    return a\n"
            "\n"
            "def outer():\n"
            "    def nested():\n"
            "        return 1\n"
            "    return nested()\n"
        ),
        "src/demo/helpers.py": "def helper():\n    return 1\n",
        "tests/test_core.py": "from demo.core import Worker\n",
        "broken.py": "def broken(:\n    pass\n",
    }
    for name, content in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    _git(repo, "add", *files)
    _git(repo, "commit", "-m", "initial")
    _git(repo, "remote", "add", "origin", "https://github.com/example/demo.git")
    return repo


def test_symbols_index_definitions_signatures_and_imports(symbol_repo: Path) -> None:
    result = symbols_state(symbol_repo)

    by_name = {item["qualified_name"]: item for item in result["symbols"]}
    worker = by_name["demo.core.Worker"]
    run = by_name["demo.core.Worker.run"]
    nested = by_name["demo.core.outer.nested"]

    assert worker["kind"] == "class"
    assert worker["bases"] == ["Base", "metaclass=Meta"]
    assert worker["decorators"] == ["registered"]
    assert run["kind"] == "method"
    assert run["async"] is True
    assert run["signature"] == "(cls, x: int=1)"
    assert run["returns"] == "str"
    assert run["decorators"] == ["classmethod"]
    assert nested["kind"] == "function"

    relative = next(
        item
        for item in result["imports"]
        if item["module"] == "helpers" and item["name"] == "helper"
    )
    assert relative["kind"] == "from"
    assert relative["level"] == 1
    assert relative["alias"] == "h"
    assert relative["source_module"] == "demo.core"


def test_symbol_filters_and_limits_are_bounded(symbol_repo: Path) -> None:
    result = symbols_state(
        symbol_repo,
        file="src/demo/core.py",
        kind="function",
        name="top",
        limit=1,
        import_limit=1,
    )

    assert result["summary"]["python_files"] == 1
    assert result["summary"]["symbols"] == 1
    assert result["symbols"][0]["qualified_name"] == "demo.core.top"
    assert len(result["imports"]) == 1
    assert result["imports_truncated"] is True


def test_symbol_cache_uses_index_blob_not_worktree_copy(symbol_repo: Path) -> None:
    first = symbols_state(symbol_repo, file="src/demo/helpers.py")
    assert first["summary"]["cache_hits"] == 0

    second = symbols_state(symbol_repo, file="src/demo/helpers.py")
    assert second["summary"]["cache_hits"] == 1

    helpers = symbol_repo / "src/demo/helpers.py"
    helpers.write_text("def changed():\n    return 2\n")
    unstaged = symbols_state(symbol_repo, file="src/demo/helpers.py")
    assert [item["name"] for item in unstaged["symbols"]] == ["helper"]

    _git(symbol_repo, "add", "src/demo/helpers.py")
    staged = symbols_state(symbol_repo, file="src/demo/helpers.py")
    assert staged["tree_sha"] != first["tree_sha"]
    assert [item["name"] for item in staged["symbols"]] == ["changed"]


def test_parse_errors_are_reported_without_aborting(symbol_repo: Path) -> None:
    result = symbols_state(symbol_repo)

    assert result["summary"]["parse_errors"] == 1
    assert result["parse_errors"][0]["path"] == "broken.py"
    assert result["parse_errors"][0]["type"] == "SyntaxError"
    assert any(item["qualified_name"] == "demo.helpers.helper" for item in result["symbols"])


def test_invalid_symbol_filters_are_rejected(symbol_repo: Path) -> None:
    with pytest.raises(ValueError, match="limit"):
        symbols_state(symbol_repo, limit=-1)
    with pytest.raises(ValueError, match="import_limit"):
        symbols_state(symbol_repo, import_limit=-1)
    with pytest.raises(ValueError, match="kind"):
        symbols_state(symbol_repo, kind="variable")
