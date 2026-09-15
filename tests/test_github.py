from __future__ import annotations

import pytest

from gway_repo import github


def test_resolve_repository_prefers_explicit_value() -> None:
    assert github.resolve_repository("arthexis/gway-repo", "/does/not/exist") == (
        "arthexis/gway-repo"
    )


def test_resolve_repository_rejects_invalid_value() -> None:
    with pytest.raises(ValueError, match="owner/repo"):
        github.resolve_repository("not-a-repository")


def test_issue_state_is_compact_and_bounds_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "number": 7,
        "title": "Example issue",
        "state": "open",
        "state_reason": None,
        "html_url": "https://github.com/arthexis/demo/issues/7",
        "user": {"login": "alice"},
        "labels": [{"name": "bug"}],
        "assignees": [{"login": "bob"}],
        "milestone": {"title": "v1"},
        "locked": False,
        "comments": 3,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "closed_at": None,
        "body": "abcdefgh",
    }

    monkeypatch.setattr(github, "_request_json", lambda repository, endpoint: payload)

    result = github.issue_state(7, repository="arthexis/demo", body_limit=4)

    assert result["kind"] == "issue"
    assert result["repository"] == "arthexis/demo"
    assert result["labels"] == ["bug"]
    assert result["assignees"] == ["bob"]
    assert result["milestone"] == "v1"
    assert result["body"] == "abcd"
    assert result["body_truncated"] is True


def test_issue_state_identifies_pr_from_issue_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "number": 3,
        "title": "A pull request",
        "state": "open",
        "pull_request": {"url": "https://api.github.com/example"},
    }
    monkeypatch.setattr(github, "_request_json", lambda repository, endpoint: payload)

    result = github.issue_state(3, repository="arthexis/demo")

    assert result["kind"] == "pull_request"


def test_pull_request_state_fetches_files_and_issue_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    pull = {
        "number": 11,
        "title": "Fix the thing",
        "state": "open",
        "draft": False,
        "html_url": "https://github.com/arthexis/demo/pull/11",
        "user": {"login": "alice"},
        "base": {
            "ref": "main",
            "sha": "base-sha",
            "repo": {"full_name": "arthexis/demo"},
        },
        "head": {
            "ref": "fix/thing",
            "sha": "head-sha",
            "repo": {"full_name": "arthexis/demo"},
        },
        "mergeable": True,
        "mergeable_state": "clean",
        "labels": [{"name": "bug"}],
        "assignees": [],
        "requested_reviewers": [{"login": "reviewer"}],
        "milestone": None,
        "commits": 2,
        "changed_files": 2,
        "additions": 8,
        "deletions": 3,
        "comments": 1,
        "review_comments": 2,
        "body": "Fixes #12 and discusses arthexis/demo#14. Duplicate #12.",
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-02T00:00:00Z",
        "closed_at": None,
        "merged_at": None,
    }
    files = [
        {
            "filename": "src/demo.py",
            "status": "modified",
            "additions": 5,
            "deletions": 2,
            "changes": 7,
        },
        {
            "filename": "tests/test_demo.py",
            "status": "renamed",
            "previous_filename": "tests/old_test.py",
            "additions": 3,
            "deletions": 1,
            "changes": 4,
        },
    ]

    def fake_request(repository: str, endpoint: str):
        assert repository == "arthexis/demo"
        calls.append(endpoint)
        return files if endpoint.startswith("pulls/11/files?") else pull

    monkeypatch.setattr(github, "_request_json", fake_request)

    result = github.pull_request_state(11, repository="arthexis/demo")

    assert calls == ["pulls/11", "pulls/11/files?per_page=100&page=1"]
    assert result["base"] == {
        "ref": "main",
        "sha": "base-sha",
        "repository": "arthexis/demo",
    }
    assert result["head"]["ref"] == "fix/thing"
    assert result["requested_reviewers"] == ["reviewer"]
    assert result["linked_issues"] == [12, 14]
    assert result["files"][1]["previous_path"] == "tests/old_test.py"
    assert result["files_truncated"] is False


def test_pull_request_state_can_skip_files(monkeypatch: pytest.MonkeyPatch) -> None:
    pull = {
        "number": 4,
        "title": "No files please",
        "state": "open",
        "changed_files": 3,
    }
    calls: list[str] = []

    def fake_request(repository: str, endpoint: str):
        calls.append(endpoint)
        return pull

    monkeypatch.setattr(github, "_request_json", fake_request)

    result = github.pull_request_state(
        4,
        repository="arthexis/demo",
        file_limit=0,
    )

    assert calls == ["pulls/4"]
    assert result["files"] == []
    assert result["files_truncated"] is True
