from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gway_repo.relationships import relationships_state


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def relation_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")

    files = {
        "src/demo/__init__.py": "",
        "src/demo/base.py": "class Base:\n    pass\n",
        "src/demo/helpers.py": "def helper():\n    return 1\n",
        "src/demo/core.py": (
            "from .base import Base\n"
            "from .helpers import helper as h\n"
            "from . import helpers\n"
            "\n"
            "class Worker(Base):\n"
            "    def run(self):\n"
            "        h()\n"
            "        helpers.helper()\n"
            "        missing()\n"
        ),
        "tests/test_core.py": (
            "from demo.core import Worker\n\ndef test_worker():\n    Worker()\n"
        ),
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


def _edges(result: dict[str, object], kind: str) -> list[dict[str, object]]:
    return [edge for edge in result["edges"] if edge["kind"] == kind]


def test_relationships_resolve_import_calls_inheritance_and_tests(
    relation_repo: Path,
) -> None:
    result = relationships_state(relation_repo)

    import_edges = _edges(result, "import")
    call_edges = _edges(result, "call")
    inheritance = _edges(result, "inheritance")
    tested_by = _edges(result, "tested_by")

    assert any(
        edge["from"] == "demo.core" and edge["to"] == "demo.helpers.helper"
        for edge in import_edges
    )
    assert (
        sum(
            edge["from"] == "demo.core.Worker.run"
            and edge["to"] == "demo.helpers.helper"
            for edge in call_edges
        )
        == 2
    )
    assert any(
        edge["from"] == "demo.core.Worker.run"
        and edge["expression"] == "missing"
        and edge["to"] is None
        and edge["resolved"] is False
        for edge in call_edges
    )
    assert any(
        edge["from"] == "demo.core.Worker"
        and edge["to"] == "demo.base.Base"
        and edge["confidence"] == "exact"
        for edge in inheritance
    )
    assert any(
        edge["from"] == "demo.core.Worker"
        and edge["to"] == "file:tests/test_core.py"
        and edge["confidence"] == "exact"
        for edge in tested_by
    )
    assert any(
        edge["from"] == "file:src/demo/core.py"
        and edge["to"] == "file:tests/test_core.py"
        and edge["resolution"] == "filename_convention"
        for edge in tested_by
    )
    assert result["summary"]["parse_errors"] == 1
    assert result["parse_errors"][0]["path"] == "broken.py"


def test_relationship_symbol_query_returns_incoming_and_outgoing(
    relation_repo: Path,
) -> None:
    result = relationships_state(
        relation_repo,
        symbol="demo.helpers.helper",
        kind="call",
    )

    assert result["summary"]["incoming"] == 2
    assert result["summary"]["outgoing"] == 0
    assert result["summary"]["edges"] == 2
    assert all(edge["to"] == "demo.helpers.helper" for edge in result["edges"])
    assert any(node["id"] == "demo.helpers.helper" for node in result["nodes"])


def test_relationship_file_filter_and_bounds(relation_repo: Path) -> None:
    result = relationships_state(
        relation_repo,
        file="tests/test_core.py",
        edge_limit=2,
        node_limit=1,
    )

    assert result["summary"]["edges"] > 2
    assert len(result["edges"]) == 2
    assert result["edges_truncated"] is True
    assert len(result["nodes"]) == 1
    assert result["nodes_truncated"] is True
    assert all(
        "tests/test_core.py" in {edge["source_path"], edge["target_path"]}
        for edge in result["edges"]
    )


def test_relationship_cache_tracks_git_index_not_unstaged_worktree(
    relation_repo: Path,
) -> None:
    first = relationships_state(relation_repo, kind="call")
    second = relationships_state(relation_repo, kind="call")
    assert first["summary"]["graph_cache_hit"] is False
    assert second["summary"]["graph_cache_hit"] is True

    core = relation_repo / "src/demo/core.py"
    core.write_text("from .helpers import helper\n\ndef changed():\n    helper()\n")
    unstaged = relationships_state(relation_repo, kind="call")
    assert unstaged["tree_sha"] == first["tree_sha"]
    assert unstaged["summary"]["graph_cache_hit"] is True
    assert not any(edge["from"] == "demo.core.changed" for edge in unstaged["edges"])

    _git(relation_repo, "add", "src/demo/core.py")
    staged = relationships_state(relation_repo, kind="call")
    assert staged["tree_sha"] != first["tree_sha"]
    assert staged["summary"]["graph_cache_hit"] is False
    assert any(
        edge["from"] == "demo.core.changed" and edge["to"] == "demo.helpers.helper"
        for edge in staged["edges"]
    )


def test_relationship_filters_validate_inputs(relation_repo: Path) -> None:
    with pytest.raises(ValueError, match="edge_limit"):
        relationships_state(relation_repo, edge_limit=-1)
    with pytest.raises(ValueError, match="node_limit"):
        relationships_state(relation_repo, node_limit=-1)
    with pytest.raises(ValueError, match="kind"):
        relationships_state(relation_repo, kind="dependency")
