"""GWay command adapter for repository operations."""

from __future__ import annotations

from .discovery import repository_info
from .github import issue_state, pull_request_state


def info(path: str = ".") -> dict[str, object]:
    """Return structured information about the Git repository containing path."""
    return repository_info(path)


def pr(
    number: int,
    repository: str | None = None,
    path: str = ".",
    body_limit: int = 12_000,
    file_limit: int = 100,
) -> dict[str, object]:
    """Return compact GitHub pull-request state."""
    return pull_request_state(
        number,
        repository=repository,
        path=path,
        body_limit=body_limit,
        file_limit=file_limit,
    )


def issue(
    number: int,
    repository: str | None = None,
    path: str = ".",
    body_limit: int = 12_000,
) -> dict[str, object]:
    """Return compact GitHub issue state."""
    return issue_state(
        number,
        repository=repository,
        path=path,
        body_limit=body_limit,
    )
