from __future__ import annotations

import pytest

from gway_repo import live
from gway_repo.github import GitHubAPIError


def test_review_state_uses_rest_without_graphql_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reviews = [
        {
            "id": 1,
            "user": {"login": "alice"},
            "state": "APPROVED",
            "body": "looks good",
            "commit_id": "abc",
            "submitted_at": "2026-01-01T00:00:00Z",
            "html_url": "https://github.com/arthexis/demo/pull/4#review-1",
        }
    ]
    monkeypatch.setattr(live, "_token", lambda: None)
    monkeypatch.setattr(live, "_request_json", lambda repository, endpoint: reviews)

    result = live.review_state(4, repository="arthexis/demo")

    assert result["decision"] == "APPROVED"
    assert result["decision_source"] == "review_submissions"
    assert result["threads_available"] is False
    assert result["unresolved_count"] is None
    assert result["reviews"][0]["author"] == "alice"


def test_unresolved_review_threads_require_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(live, "_token", lambda: None)
    monkeypatch.setattr(live, "_request_json", lambda repository, endpoint: [])

    with pytest.raises(GitHubAPIError, match="unresolved-only"):
        live.review_state(
            4,
            repository="arthexis/demo",
            unresolved_only=True,
        )


def test_review_state_filters_unresolved_graphql_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(live, "_token", lambda: "token")
    monkeypatch.setattr(live, "_request_json", lambda repository, endpoint: [])
    monkeypatch.setattr(
        live,
        "_request_graphql",
        lambda query, variables: {
            "repository": {
                "pullRequest": {
                    "reviewDecision": "CHANGES_REQUESTED",
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False},
                        "nodes": [
                            {
                                "id": "thread-1",
                                "isResolved": False,
                                "isOutdated": False,
                                "path": "src/demo.py",
                                "line": 10,
                                "startLine": None,
                                "comments": {
                                    "totalCount": 2,
                                    "nodes": [
                                        {
                                            "author": {"login": "rabbit"},
                                            "body": "please fix this",
                                            "url": "https://example/review/1",
                                            "createdAt": "2026-01-01T00:00:00Z",
                                        }
                                    ],
                                },
                            },
                            {
                                "id": "thread-2",
                                "isResolved": True,
                                "isOutdated": False,
                                "path": "src/demo.py",
                                "line": 20,
                                "startLine": None,
                                "comments": {"totalCount": 1, "nodes": []},
                            },
                        ],
                    },
                }
            }
        },
    )

    result = live.review_state(
        4,
        repository="arthexis/demo",
        unresolved_only=True,
    )

    assert result["decision"] == "CHANGES_REQUESTED"
    assert result["decision_source"] == "graphql"
    assert result["unresolved_count"] == 1
    assert [thread["id"] for thread in result["threads"]] == ["thread-1"]
    assert result["threads"][0]["comment"]["author"] == "rabbit"


def test_check_state_normalizes_failures_and_pending_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_request(repository: str, endpoint: str):
        assert repository == "arthexis/demo"
        if endpoint.startswith("commits/head/check-runs"):
            return {
                "total_count": 2,
                "check_runs": [
                    {
                        "name": "Quality",
                        "status": "completed",
                        "conclusion": "failure",
                        "started_at": "2026-01-01T00:00:00Z",
                        "completed_at": "2026-01-01T00:01:00Z",
                        "html_url": "https://example/check/1",
                        "app": {"slug": "github-actions"},
                        "output": {
                            "title": "Ruff failed",
                            "summary": "formatting mismatch",
                        },
                    },
                    {
                        "name": "Package",
                        "status": "in_progress",
                        "conclusion": None,
                        "app": {"name": "GitHub Actions"},
                        "output": {},
                    },
                ],
            }
        if endpoint.startswith("commits/head/status"):
            return {
                "state": "failure",
                "statuses": [
                    {
                        "context": "legacy",
                        "state": "success",
                        "description": "ok",
                        "target_url": "https://example/status",
                        "updated_at": "2026-01-01T00:00:00Z",
                    }
                ],
            }
        raise AssertionError(endpoint)

    monkeypatch.setattr(live, "_request_json", fake_request)

    result = live.check_state(4, repository="arthexis/demo", sha="head")

    assert result["head_sha"] == "head"
    assert result["state"] == "failure"
    assert result["problem_checks"] == ["Quality"]
    assert result["pending_checks"] == ["Package"]
    assert result["check_runs"][0]["summary"] == "formatting mismatch"
    assert result["statuses"][0]["context"] == "legacy"


def test_workflow_state_summarizes_failed_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_request(repository: str, endpoint: str):
        calls.append(endpoint)
        if endpoint.startswith("actions/runs?head_sha=head"):
            return {
                "total_count": 1,
                "workflow_runs": [
                    {
                        "id": 99,
                        "name": "python / Quality",
                        "event": "pull_request",
                        "status": "completed",
                        "conclusion": "failure",
                        "run_number": 3,
                        "run_attempt": 1,
                        "html_url": "https://example/run/99",
                        "created_at": "2026-01-01T00:00:00Z",
                        "updated_at": "2026-01-01T00:02:00Z",
                    }
                ],
            }
        if endpoint.startswith("actions/runs/99/jobs"):
            return {
                "total_count": 1,
                "jobs": [
                    {
                        "id": 100,
                        "name": "Quality",
                        "status": "completed",
                        "conclusion": "failure",
                        "html_url": "https://example/job/100",
                        "started_at": "2026-01-01T00:00:00Z",
                        "completed_at": "2026-01-01T00:02:00Z",
                        "steps": [
                            {
                                "number": 1,
                                "name": "Ruff lint",
                                "conclusion": "success",
                            },
                            {
                                "number": 2,
                                "name": "Ruff format",
                                "conclusion": "failure",
                            },
                        ],
                    }
                ],
            }
        raise AssertionError(endpoint)

    monkeypatch.setattr(live, "_request_json", fake_request)

    result = live.workflow_state(4, repository="arthexis/demo", sha="head")

    assert calls == [
        "actions/runs?head_sha=head&per_page=20",
        "actions/runs/99/jobs?filter=latest&per_page=50",
    ]
    assert result["runs"][0]["jobs"][0]["failed_steps"] == [
        {"number": 2, "name": "Ruff format", "conclusion": "failure"}
    ]
    assert result["runs_truncated"] is False
