from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from gway_repo import impact as impact_module


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def impact_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")

    files = {
        "src/demo/__init__.py": "",
        "src/demo/core.py": "def target():\n    return 1\n",
        "src/demo/service.py": (
            "from .core import target\n\ndef caller():\n    return target()\n"
        ),
        "src/demo/api.py": (
            "from .service import caller\n\ndef top():\n    return caller()\n"
        ),
        "tests/test_core.py": (
            "from demo.core import target\n\n"
            "def test_target():\n"
            "    assert target() == 1\n"
        ),
    }
    for name, content in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    _git(repo, "add", *files)
    _git(repo, "commit", "-m", "initial")
    _git(repo, "remote", "add", "origin", "https://github.com/example/demo.git")
    return repo


def _pull(
    number: int,
    head_sha: str,
    path: str,
    *,
    status: str = "modified",
) -> dict[str, object]:
    return {
        "kind": "pull_request",
        "number": number,
        "state": "open",
        "head": {"sha": head_sha},
        "files": [
            {
                "path": path,
                "status": status,
                "previous_path": None,
            }
        ],
        "files_truncated": False,
    }


def test_file_impact_finds_dependents_and_tests(impact_repo: Path) -> None:
    result = impact_module.impact_state(
        file="src/demo/core.py",
        path=impact_repo,
        depth=2,
    )

    assert result["available"] is True
    assert result["basis"]["authoritative"] is True
    assert any(
        symbol["id"] == "demo.core.target" for symbol in result["changed_symbols"]
    )

    dependents = {item["id"]: item for item in result["dependents"]}
    assert dependents["demo.service.caller"]["distance"] == 1
    assert dependents["demo.api.top"]["distance"] == 2
    assert any(
        item["path"] == "tests/test_core.py" and item["confidence"] == "exact"
        for item in result["tests"]
    )


def test_file_impact_respects_depth(impact_repo: Path) -> None:
    result = impact_module.impact_state(
        file="src/demo/core.py",
        path=impact_repo,
        depth=1,
    )

    ids = {item["id"] for item in result["dependents"]}
    assert "demo.service.caller" in ids
    assert "demo.api.top" not in ids


def test_pull_impact_marks_exact_checkout_authoritative(impact_repo: Path) -> None:
    head = _git(impact_repo, "rev-parse", "HEAD")
    result = impact_module.impact_for_pull_state(
        _pull(10, head, "src/demo/core.py"),
        repository="example/demo",
        path=impact_repo,
    )

    assert result["available"] is True
    assert result["basis"]["authoritative"] is True
    assert result["basis"]["head_matches"] is True
    assert result["source_complete"] is True


def test_pull_impact_keeps_mismatched_checkout_but_marks_it_non_authoritative(
    impact_repo: Path,
) -> None:
    result = impact_module.impact_for_pull_state(
        _pull(10, "other-head", "src/demo/core.py"),
        repository="example/demo",
        path=impact_repo,
    )

    assert result["available"] is True
    assert result["basis"]["authoritative"] is False
    assert result["basis"]["reason"] == "local_checkout_does_not_match_pr_head"


def test_pull_impact_reports_deleted_or_unknown_file_as_unmapped(
    impact_repo: Path,
) -> None:
    head = _git(impact_repo, "rev-parse", "HEAD")
    result = impact_module.impact_for_pull_state(
        _pull(10, head, "src/demo/removed.py", status="removed"),
        repository="example/demo",
        path=impact_repo,
    )

    assert result["summary"]["unmapped_files"] == 1
    assert result["unmapped_files"][0]["path"] == "src/demo/removed.py"


def test_issue_impact_aggregates_prs_and_preserves_provenance(
    impact_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    head = _git(impact_repo, "rev-parse", "HEAD")
    monkeypatch.setattr(
        impact_module,
        "issue_pull_requests_state",
        lambda issue, **kwargs: {
            "kind": "issue_pull_requests",
            "repository": "example/demo",
            "issue": issue,
            "summary": {
                "pull_requests": 2,
                "events_scanned": 2,
                "timeline_truncated": False,
            },
            "pull_requests": [
                {
                    "number": 10,
                    "repository": "example/demo",
                    "relation": "closes",
                    "confidence": "exact",
                    "state": "open",
                    "title": "Core",
                    "url": None,
                },
                {
                    "number": 11,
                    "repository": "example/demo",
                    "relation": "mentions",
                    "confidence": "likely",
                    "state": "open",
                    "title": "Service",
                    "url": None,
                },
            ],
            "pull_requests_truncated": False,
        },
    )

    pulls = {
        10: _pull(10, head, "src/demo/core.py"),
        11: _pull(11, head, "src/demo/service.py"),
    }
    monkeypatch.setattr(
        impact_module,
        "pull_request_state",
        lambda number, **kwargs: pulls[number],
    )

    result = impact_module.impact_state(
        issue=7,
        repository="example/demo",
        path=impact_repo,
    )

    assert result["available"] is True
    assert result["basis"]["authoritative"] is True
    assert [item["number"] for item in result["pull_requests"]] == [10, 11]
    changed = {item["id"]: item for item in result["changed_symbols"]}
    assert changed["demo.core.target"]["pull_requests"] == [10]
    assert changed["demo.service.caller"]["pull_requests"] == [11]
    top = next(item for item in result["dependents"] if item["id"] == "demo.api.top")
    assert top["pull_requests"] == [10, 11]


def test_issue_impact_surfaces_cross_repository_pr_without_guessing(
    impact_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        impact_module,
        "issue_pull_requests_state",
        lambda issue, **kwargs: {
            "summary": {
                "pull_requests": 1,
                "events_scanned": 1,
                "timeline_truncated": False,
            },
            "pull_requests": [
                {
                    "number": 4,
                    "repository": "example/other",
                    "relation": "connected",
                    "confidence": "exact",
                }
            ],
            "pull_requests_truncated": False,
        },
    )

    result = impact_module.impact_state(
        issue=7,
        repository="example/demo",
        path=impact_repo,
    )

    assert result["pull_requests"][0]["available"] is False
    assert result["pull_requests"][0]["reason"] == (
        "cross_repository_impact_not_supported"
    )
    assert result["source_complete"] is False


def test_impact_requires_exactly_one_target_and_valid_limits() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        impact_module.impact_state()
    with pytest.raises(ValueError, match="exactly one"):
        impact_module.impact_state(pr=1, issue=2)
    with pytest.raises(ValueError, match="depth"):
        impact_module.impact_state(file="x.py", depth=-1)
