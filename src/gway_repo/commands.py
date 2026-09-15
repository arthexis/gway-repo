"""GWay command adapter for repository operations."""

from __future__ import annotations

from .discovery import repository_info
from .github import issue_state, pull_request_state
from .live import check_state, review_state, workflow_state


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


def reviews(
    number: int,
    repository: str | None = None,
    path: str = ".",
    unresolved_only: bool = False,
    review_limit: int = 100,
    thread_limit: int = 50,
    body_limit: int = 2_000,
) -> dict[str, object]:
    """Return review submissions and review-thread state for a pull request."""
    return review_state(
        number,
        repository=repository,
        path=path,
        unresolved_only=unresolved_only,
        review_limit=review_limit,
        thread_limit=thread_limit,
        body_limit=body_limit,
    )


def checks(
    number: int,
    repository: str | None = None,
    path: str = ".",
    sha: str | None = None,
    check_limit: int = 100,
    summary_limit: int = 2_000,
) -> dict[str, object]:
    """Return check-run and commit-status state for a pull request head."""
    return check_state(
        number,
        repository=repository,
        path=path,
        sha=sha,
        check_limit=check_limit,
        summary_limit=summary_limit,
    )


def workflows(
    number: int,
    repository: str | None = None,
    path: str = ".",
    sha: str | None = None,
    run_limit: int = 20,
    job_limit: int = 50,
) -> dict[str, object]:
    """Return bounded Actions run/job state for a pull request head."""
    return workflow_state(
        number,
        repository=repository,
        path=path,
        sha=sha,
        run_limit=run_limit,
        job_limit=job_limit,
    )
