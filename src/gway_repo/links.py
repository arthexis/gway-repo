"""Issue-to-pull-request relationship discovery."""

from __future__ import annotations

import re

from .github import GitHubAPIError, _request_json, resolve_repository

_CLOSING_REFERENCE = re.compile(
    r"(?i)\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+"
    r"(?:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?#(?P<number>\d+)\b"
)
_REPOSITORY_URL = re.compile(r"/repos/(?P<repository>[^/]+/[^/]+)$")
_PULL_URL = re.compile(r"/repos/(?P<repository>[^/]+/[^/]+)/pulls/(?P<number>\d+)$")
_RELATION_PRIORITY = {"mentions": 1, "connected": 2, "closes": 3}


def _source_repository(value: object, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    match = _REPOSITORY_URL.search(value.rstrip("/"))
    return match.group("repository") if match else fallback


def _closes_issue(body: object, issue: int) -> bool:
    if not isinstance(body, str):
        return False
    return any(
        int(match.group("number")) == issue
        for match in _CLOSING_REFERENCE.finditer(body)
    )


def _cross_reference(
    event: dict[str, object],
    *,
    repository: str,
    issue: int,
) -> dict[str, object] | None:
    source = event.get("source")
    if not isinstance(source, dict):
        return None
    item = source.get("issue")
    if not isinstance(item, dict) or not isinstance(item.get("pull_request"), dict):
        return None
    number = item.get("number")
    if not isinstance(number, int):
        return None

    relation = "closes" if _closes_issue(item.get("body"), issue) else "mentions"
    return {
        "number": number,
        "repository": _source_repository(item.get("repository_url"), repository),
        "relation": relation,
        "confidence": "exact" if relation == "closes" else "likely",
        "state": item.get("state"),
        "title": item.get("title"),
        "url": item.get("html_url"),
    }


def _connected_reference(event: dict[str, object]) -> dict[str, object] | None:
    subject = event.get("subject")
    if not isinstance(subject, dict):
        return None
    url = subject.get("url")
    if not isinstance(url, str):
        return None
    match = _PULL_URL.search(url.rstrip("/"))
    if not match:
        return None
    return {
        "number": int(match.group("number")),
        "repository": match.group("repository"),
        "relation": "connected",
        "confidence": "exact",
        "state": None,
        "title": subject.get("title"),
        "url": None,
    }


def _candidate(
    event: dict[str, object],
    *,
    repository: str,
    issue: int,
) -> dict[str, object] | None:
    event_name = event.get("event")
    if event_name == "cross-referenced":
        return _cross_reference(event, repository=repository, issue=issue)
    if event_name == "connected":
        return _connected_reference(event)
    return None


def issue_pull_requests_state(
    issue: int,
    repository: str | None = None,
    path: str = ".",
    limit: int = 20,
    event_limit: int = 300,
) -> dict[str, object]:
    """Return PRs connected to an issue using bounded GitHub timeline evidence."""
    if limit < 0:
        raise ValueError("limit must be non-negative")
    if event_limit < 0:
        raise ValueError("event_limit must be non-negative")

    repo = resolve_repository(repository, path)
    issue = int(issue)
    events: list[dict[str, object]] = []
    timeline_truncated = False
    page = 1

    while len(events) < event_limit:
        per_page = min(100, event_limit - len(events))
        payload = _request_json(
            repo,
            f"issues/{issue}/timeline?per_page={per_page}&page={page}",
        )
        if not isinstance(payload, list):
            raise GitHubAPIError("GitHub issue timeline response was not a list")
        page_items = [item for item in payload if isinstance(item, dict)]
        events.extend(page_items)
        if len(payload) < per_page:
            break
        if len(events) >= event_limit:
            timeline_truncated = True
            break
        page += 1

    candidates: dict[tuple[str, int], dict[str, object]] = {}
    for event in events:
        item = _candidate(event, repository=repo, issue=issue)
        if item is None:
            continue
        item_repo = item.get("repository")
        number = item.get("number")
        if not isinstance(item_repo, str) or not isinstance(number, int):
            continue
        key = (item_repo, number)
        current = candidates.get(key)
        if (
            current is None
            or _RELATION_PRIORITY[str(item["relation"])]
            > _RELATION_PRIORITY[str(current["relation"])]
        ):
            candidates[key] = item

    linked = sorted(
        candidates.values(),
        key=lambda item: (str(item.get("repository")), int(item.get("number", 0))),
    )
    return {
        "kind": "issue_pull_requests",
        "repository": repo,
        "issue": issue,
        "summary": {
            "pull_requests": len(linked),
            "events_scanned": len(events),
            "timeline_truncated": timeline_truncated,
        },
        "pull_requests": linked[:limit],
        "pull_requests_truncated": len(linked) > limit,
    }
