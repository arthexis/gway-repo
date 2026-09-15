from __future__ import annotations

import pytest

from gway_repo import links


def test_issue_pull_requests_discovers_closing_mentions_and_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = [
        {
            "event": "cross-referenced",
            "source": {
                "issue": {
                    "number": 11,
                    "state": "closed",
                    "title": "Fix it",
                    "html_url": "https://github.com/arthexis/demo/pull/11",
                    "repository_url": "https://api.github.com/repos/arthexis/demo",
                    "pull_request": {"url": "https://api.github.com/pulls/11"},
                    "body": "Fixes #7",
                }
            },
        },
        {
            "event": "cross-referenced",
            "source": {
                "issue": {
                    "number": 12,
                    "state": "open",
                    "title": "Discuss it",
                    "html_url": "https://github.com/arthexis/demo/pull/12",
                    "repository_url": "https://api.github.com/repos/arthexis/demo",
                    "pull_request": {"url": "https://api.github.com/pulls/12"},
                    "body": "Related to #7",
                }
            },
        },
        {
            "event": "connected",
            "subject": {
                "title": "Cross repo",
                "url": "https://api.github.com/repos/arthexis/other/pulls/13",
            },
        },
    ]
    calls: list[str] = []

    def fake_request(repository: str, endpoint: str):
        calls.append(endpoint)
        return events

    monkeypatch.setattr(links, "_request_json", fake_request)

    result = links.issue_pull_requests_state(7, repository="arthexis/demo")

    assert calls == ["issues/7/timeline?per_page=100&page=1"]
    assert result["summary"]["pull_requests"] == 3
    assert result["pull_requests"] == [
        {
            "number": 11,
            "repository": "arthexis/demo",
            "relation": "closes",
            "confidence": "exact",
            "state": "closed",
            "title": "Fix it",
            "url": "https://github.com/arthexis/demo/pull/11",
        },
        {
            "number": 12,
            "repository": "arthexis/demo",
            "relation": "mentions",
            "confidence": "likely",
            "state": "open",
            "title": "Discuss it",
            "url": "https://github.com/arthexis/demo/pull/12",
        },
        {
            "number": 13,
            "repository": "arthexis/other",
            "relation": "connected",
            "confidence": "exact",
            "state": None,
            "title": "Cross repo",
            "url": None,
        },
    ]


def test_issue_pull_requests_prefers_stronger_duplicate_relation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = [
        {
            "event": "cross-referenced",
            "source": {
                "issue": {
                    "number": 11,
                    "repository_url": "https://api.github.com/repos/arthexis/demo",
                    "pull_request": {},
                    "body": "Related to #7",
                }
            },
        },
        {
            "event": "cross-referenced",
            "source": {
                "issue": {
                    "number": 11,
                    "repository_url": "https://api.github.com/repos/arthexis/demo",
                    "pull_request": {},
                    "body": "Resolves #7",
                }
            },
        },
    ]
    monkeypatch.setattr(links, "_request_json", lambda repository, endpoint: events)

    result = links.issue_pull_requests_state(7, repository="arthexis/demo")

    assert result["pull_requests"][0]["relation"] == "closes"
    assert result["pull_requests"][0]["confidence"] == "exact"


def test_issue_pull_requests_bounds_timeline_and_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = [
        {
            "event": "connected",
            "subject": {
                "url": f"https://api.github.com/repos/arthexis/demo/pulls/{number}"
            },
        }
        for number in range(1, 101)
    ]
    monkeypatch.setattr(links, "_request_json", lambda repository, endpoint: events)

    result = links.issue_pull_requests_state(
        7,
        repository="arthexis/demo",
        limit=2,
        event_limit=100,
    )

    assert result["summary"]["events_scanned"] == 100
    assert result["summary"]["timeline_truncated"] is True
    assert len(result["pull_requests"]) == 2
    assert result["pull_requests_truncated"] is True


def test_issue_pull_requests_validates_limits() -> None:
    with pytest.raises(ValueError, match="limit"):
        links.issue_pull_requests_state(7, repository="arthexis/demo", limit=-1)
    with pytest.raises(ValueError, match="event_limit"):
        links.issue_pull_requests_state(7, repository="arthexis/demo", event_limit=-1)
