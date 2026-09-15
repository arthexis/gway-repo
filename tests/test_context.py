from __future__ import annotations

import pytest

from gway_repo import context as context_module


def test_context_requires_exactly_one_target() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        context_module.context_state()

    with pytest.raises(ValueError, match="exactly one"):
        context_module.context_state(pr=1, issue=2)


def test_issue_context_uses_unified_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        context_module,
        "resolve_repository",
        lambda repository, path: "arthexis/demo",
    )
    monkeypatch.setattr(
        context_module,
        "issue_state",
        lambda number, **kwargs: {
            "kind": "issue",
            "number": number,
            "state": "open",
            "state_reason": None,
            "labels": ["bug"],
            "comments": 2,
        },
    )

    result = context_module.context_state(issue=7, repository="arthexis/demo")

    assert result["target"] == {"kind": "issue", "number": 7}
    assert result["included_sections"] == ["issue"]
    assert result["summary"]["labels"] == ["bug"]
    assert result["pull_request"] is None
    assert result["reviews"] is None
    assert result["checks"] is None
    assert result["workflows"] is None


def test_pr_context_reuses_head_sha(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        context_module,
        "resolve_repository",
        lambda repository, path: "arthexis/demo",
    )
    monkeypatch.setattr(
        context_module,
        "pull_request_state",
        lambda number, **kwargs: {
            "kind": "pull_request",
            "number": number,
            "state": "open",
            "draft": False,
            "mergeable": True,
            "mergeable_state": "clean",
            "head": {"sha": "head-sha"},
        },
    )
    monkeypatch.setattr(
        context_module,
        "review_state",
        lambda number, **kwargs: {
            "decision": "APPROVED",
            "unresolved_count": 0,
        },
    )

    received: list[tuple[str, str | None]] = []

    def fake_checks(number: int, **kwargs):
        received.append(("checks", kwargs.get("sha")))
        return {"problem_checks": [], "pending_checks": []}

    def fake_workflows(number: int, **kwargs):
        received.append(("workflows", kwargs.get("sha")))
        return {
            "runs": [
                {
                    "id": 1,
                    "name": "CI",
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        }

    monkeypatch.setattr(context_module, "check_state", fake_checks)
    monkeypatch.setattr(context_module, "workflow_state", fake_workflows)

    result = context_module.context_state(pr=9, repository="arthexis/demo")

    assert received == [("checks", "head-sha"), ("workflows", "head-sha")]
    assert result["head_sha"] == "head-sha"
    assert result["included_sections"] == [
        "pull_request",
        "reviews",
        "checks",
        "workflows",
    ]
    assert result["summary"]["attention"] is False


def test_pr_context_can_disable_live_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        context_module,
        "resolve_repository",
        lambda repository, path: "arthexis/demo",
    )
    monkeypatch.setattr(
        context_module,
        "pull_request_state",
        lambda number, **kwargs: {
            "state": "open",
            "draft": False,
            "mergeable": True,
            "mergeable_state": "clean",
            "head": {"sha": "head-sha"},
        },
    )

    def unexpected(*args, **kwargs):
        raise AssertionError("disabled live section was called")

    monkeypatch.setattr(context_module, "review_state", unexpected)
    monkeypatch.setattr(context_module, "check_state", unexpected)
    monkeypatch.setattr(context_module, "workflow_state", unexpected)

    result = context_module.context_state(
        pr=3,
        repository="arthexis/demo",
        include_reviews=False,
        include_checks=False,
        include_workflows=False,
    )

    assert result["included_sections"] == ["pull_request"]
    assert result["reviews"] is None
    assert result["checks"] is None
    assert result["workflows"] is None


def test_pr_context_summary_surfaces_attention_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        context_module,
        "resolve_repository",
        lambda repository, path: "arthexis/demo",
    )
    monkeypatch.setattr(
        context_module,
        "pull_request_state",
        lambda number, **kwargs: {
            "state": "open",
            "draft": False,
            "mergeable": True,
            "mergeable_state": "blocked",
            "head": {"sha": "head-sha"},
        },
    )
    monkeypatch.setattr(
        context_module,
        "review_state",
        lambda number, **kwargs: {
            "decision": "CHANGES_REQUESTED",
            "unresolved_count": 2,
        },
    )
    monkeypatch.setattr(
        context_module,
        "check_state",
        lambda number, **kwargs: {
            "problem_checks": ["Quality"],
            "pending_checks": ["Package"],
        },
    )
    monkeypatch.setattr(
        context_module,
        "workflow_state",
        lambda number, **kwargs: {
            "runs": [
                {
                    "id": 1,
                    "name": "Clean Install",
                    "status": "completed",
                    "conclusion": "failure",
                },
                {
                    "id": 2,
                    "name": "Compatibility",
                    "status": "in_progress",
                    "conclusion": None,
                },
            ]
        },
    )

    result = context_module.context_state(pr=5, repository="arthexis/demo")
    summary = result["summary"]

    assert summary["review_decision"] == "CHANGES_REQUESTED"
    assert summary["unresolved_threads"] == 2
    assert summary["problem_checks"] == ["Quality"]
    assert summary["pending_checks"] == ["Package"]
    assert summary["failed_workflows"] == ["Clean Install"]
    assert summary["pending_workflows"] == ["Compatibility"]
    assert summary["attention"] is True
