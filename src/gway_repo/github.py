"""Compact GitHub pull-request and issue state collectors."""

from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .discovery import repository_info

_API_ROOT = "https://api.github.com"
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_ISSUE_REFERENCE = re.compile(
    r"(?<![A-Za-z0-9_.-])(?:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?#(?P<number>\d+)\b"
)


class GitHubAPIError(RuntimeError):
    """Raised when GitHub cannot satisfy a repository-state request."""


def resolve_repository(repository: str | None = None, path: str = ".") -> str:
    """Resolve an explicit ``owner/repo`` or infer it from the local Git remote."""
    if repository is None:
        discovered = repository_info(path).get("repository")
        if not isinstance(discovered, str) or not discovered:
            raise ValueError(
                "GitHub repository could not be inferred; pass --repository owner/repo"
            )
        repository = discovered

    repository = repository.strip()
    if not _REPOSITORY.fullmatch(repository):
        raise ValueError("repository must use owner/repo form")
    return repository


def _request_json(repository: str, endpoint: str) -> Any:
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "gway-repo",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = Request(f"{_API_ROOT}/repos/{repository}/{endpoint}", headers=headers)
    try:
        with urlopen(request, timeout=20) as response:
            return json.load(response)
    except HTTPError as exc:
        detail = exc.reason
        try:
            payload = json.load(exc)
            detail = payload.get("message", detail)
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass
        raise GitHubAPIError(f"GitHub API returned {exc.code}: {detail}") from exc
    except URLError as exc:
        raise GitHubAPIError(f"GitHub API request failed: {exc.reason}") from exc


def _body(value: object, limit: int) -> tuple[str | None, bool]:
    if limit < 0:
        raise ValueError("body_limit cannot be negative")
    if value is None:
        return None, False
    text = str(value)
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _names(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        if isinstance(value, dict):
            name = value.get("name") or value.get("login")
            if isinstance(name, str):
                result.append(name)
    return result


def _linked_issues(body: object) -> list[int]:
    if not isinstance(body, str):
        return []
    return sorted({int(match.group("number")) for match in _ISSUE_REFERENCE.finditer(body)})


def issue_state(
    number: int,
    repository: str | None = None,
    path: str = ".",
    body_limit: int = 12_000,
) -> dict[str, object]:
    """Return compact, serializable GitHub issue state."""
    repo = resolve_repository(repository, path)
    payload = _request_json(repo, f"issues/{int(number)}")
    if not isinstance(payload, dict):
        raise GitHubAPIError("GitHub issue response was not an object")

    body, body_truncated = _body(payload.get("body"), body_limit)
    milestone = payload.get("milestone")
    user = payload.get("user")

    return {
        "kind": "pull_request" if "pull_request" in payload else "issue",
        "repository": repo,
        "number": payload.get("number", int(number)),
        "title": payload.get("title"),
        "state": payload.get("state"),
        "state_reason": payload.get("state_reason"),
        "url": payload.get("html_url"),
        "author": user.get("login") if isinstance(user, dict) else None,
        "labels": _names(payload.get("labels")),
        "assignees": _names(payload.get("assignees")),
        "milestone": milestone.get("title") if isinstance(milestone, dict) else None,
        "locked": bool(payload.get("locked", False)),
        "comments": payload.get("comments", 0),
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
        "closed_at": payload.get("closed_at"),
        "body": body,
        "body_truncated": body_truncated,
    }


def _pull_files(repository: str, number: int, limit: int) -> list[dict[str, object]]:
    if limit < 0:
        raise ValueError("file_limit cannot be negative")
    if limit == 0:
        return []

    files: list[dict[str, object]] = []
    page = 1
    while len(files) < limit:
        per_page = min(100, limit - len(files))
        payload = _request_json(
            repository,
            f"pulls/{number}/files?per_page={per_page}&page={page}",
        )
        if not isinstance(payload, list):
            raise GitHubAPIError("GitHub pull-request files response was not a list")

        for item in payload:
            if not isinstance(item, dict):
                continue
            files.append(
                {
                    "path": item.get("filename"),
                    "status": item.get("status"),
                    "additions": item.get("additions", 0),
                    "deletions": item.get("deletions", 0),
                    "changes": item.get("changes", 0),
                    "previous_path": item.get("previous_filename"),
                }
            )
        if len(payload) < per_page:
            break
        page += 1

    return files[:limit]


def pull_request_state(
    number: int,
    repository: str | None = None,
    path: str = ".",
    body_limit: int = 12_000,
    file_limit: int = 100,
) -> dict[str, object]:
    """Return compact, serializable GitHub pull-request state."""
    repo = resolve_repository(repository, path)
    number = int(number)
    payload = _request_json(repo, f"pulls/{number}")
    if not isinstance(payload, dict):
        raise GitHubAPIError("GitHub pull-request response was not an object")

    body, body_truncated = _body(payload.get("body"), body_limit)
    files = _pull_files(repo, number, file_limit)
    base = payload.get("base")
    head = payload.get("head")
    user = payload.get("user")
    milestone = payload.get("milestone")
    changed_files = int(payload.get("changed_files") or 0)

    def ref(value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            return {"ref": None, "sha": None, "repository": None}
        nested_repo = value.get("repo")
        return {
            "ref": value.get("ref"),
            "sha": value.get("sha"),
            "repository": (
                nested_repo.get("full_name") if isinstance(nested_repo, dict) else None
            ),
        }

    return {
        "kind": "pull_request",
        "repository": repo,
        "number": payload.get("number", number),
        "title": payload.get("title"),
        "state": payload.get("state"),
        "draft": bool(payload.get("draft", False)),
        "url": payload.get("html_url"),
        "author": user.get("login") if isinstance(user, dict) else None,
        "base": ref(base),
        "head": ref(head),
        "mergeable": payload.get("mergeable"),
        "mergeable_state": payload.get("mergeable_state"),
        "labels": _names(payload.get("labels")),
        "assignees": _names(payload.get("assignees")),
        "requested_reviewers": _names(payload.get("requested_reviewers")),
        "milestone": milestone.get("title") if isinstance(milestone, dict) else None,
        "commits": payload.get("commits", 0),
        "changed_files": changed_files,
        "additions": payload.get("additions", 0),
        "deletions": payload.get("deletions", 0),
        "comments": payload.get("comments", 0),
        "review_comments": payload.get("review_comments", 0),
        "linked_issues": _linked_issues(payload.get("body")),
        "files": files,
        "files_truncated": changed_files > len(files),
        "body": body,
        "body_truncated": body_truncated,
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
        "closed_at": payload.get("closed_at"),
        "merged_at": payload.get("merged_at"),
    }
