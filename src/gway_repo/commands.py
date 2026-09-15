"""GWay command adapter for repository operations."""

from __future__ import annotations

from .context import context_state
from .discovery import repository_info
from .github import issue_state, pull_request_state
from .live import check_state, review_state, workflow_state
from .mapping import files_state, repository_map


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


def context(
    pr: int | None = None,
    issue: int | None = None,
    repository: str | None = None,
    path: str = ".",
    include_reviews: bool = True,
    include_checks: bool = True,
    include_workflows: bool = True,
    body_limit: int = 12_000,
    file_limit: int = 100,
    review_limit: int = 100,
    thread_limit: int = 50,
    review_body_limit: int = 2_000,
    check_limit: int = 100,
    summary_limit: int = 2_000,
    run_limit: int = 20,
    job_limit: int = 50,
) -> dict[str, object]:
    """Return one bounded context envelope for a pull request or issue."""
    return context_state(
        pr=pr,
        issue=issue,
        repository=repository,
        path=path,
        include_reviews=include_reviews,
        include_checks=include_checks,
        include_workflows=include_workflows,
        body_limit=body_limit,
        file_limit=file_limit,
        review_limit=review_limit,
        thread_limit=thread_limit,
        review_body_limit=review_body_limit,
        check_limit=check_limit,
        summary_limit=summary_limit,
        run_limit=run_limit,
        job_limit=job_limit,
    )


def map(
    path: str = ".",
    refresh: bool = False,
    file_limit: int = 500,
) -> dict[str, object]:
    """Return a bounded structural map of the local Git repository."""
    return repository_map(path=path, refresh=refresh, file_limit=file_limit)


def files(
    path: str = ".",
    kind: str | None = None,
    extension: str | None = None,
    package: str | None = None,
    limit: int = 200,
    refresh: bool = False,
) -> dict[str, object]:
    """Return a bounded filtered view of Git-tracked repository files."""
    return files_state(
        path=path,
        kind=kind,
        extension=extension,
        package=package,
        limit=limit,
        refresh=refresh,
    )
