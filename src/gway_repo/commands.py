"""GWay command adapter for repository operations."""

from __future__ import annotations

from .context import context_state
from .discovery import repository_info
from .github import issue_state, pull_request_state
from .impact import impact_state
from .links import issue_pull_requests_state
from .live import check_state, review_state, workflow_state
from .mapping import files_state, repository_map
from .relationships import relationships_state
from .symbols import symbols_state


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


def prs(
    issue: int,
    repository: str | None = None,
    path: str = ".",
    limit: int = 20,
    event_limit: int = 300,
) -> dict[str, object]:
    """Return pull requests connected to an issue."""
    return issue_pull_requests_state(
        issue,
        repository=repository,
        path=path,
        limit=limit,
        event_limit=event_limit,
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
    include_impact: bool = True,
    body_limit: int = 12_000,
    file_limit: int = 100,
    review_limit: int = 100,
    thread_limit: int = 50,
    review_body_limit: int = 2_000,
    check_limit: int = 100,
    summary_limit: int = 2_000,
    run_limit: int = 20,
    job_limit: int = 50,
    impact_depth: int = 2,
    impact_pr_limit: int = 20,
    impact_event_limit: int = 300,
    impact_changed_file_limit: int = 100,
    impact_symbol_limit: int = 100,
    impact_dependent_limit: int = 100,
    impact_test_limit: int = 100,
    impact_error_limit: int = 20,
    impact_visit_limit: int = 2_000,
    refresh: bool = False,
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
        include_impact=include_impact,
        body_limit=body_limit,
        file_limit=file_limit,
        review_limit=review_limit,
        thread_limit=thread_limit,
        review_body_limit=review_body_limit,
        check_limit=check_limit,
        summary_limit=summary_limit,
        run_limit=run_limit,
        job_limit=job_limit,
        impact_depth=impact_depth,
        impact_pr_limit=impact_pr_limit,
        impact_event_limit=impact_event_limit,
        impact_changed_file_limit=impact_changed_file_limit,
        impact_symbol_limit=impact_symbol_limit,
        impact_dependent_limit=impact_dependent_limit,
        impact_test_limit=impact_test_limit,
        impact_error_limit=impact_error_limit,
        impact_visit_limit=impact_visit_limit,
        refresh=refresh,
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


def symbols(
    path: str = ".",
    file: str | None = None,
    kind: str | None = None,
    name: str | None = None,
    package: str | None = None,
    limit: int = 200,
    import_limit: int = 200,
    error_limit: int = 50,
    include_imports: bool = True,
    refresh: bool = False,
) -> dict[str, object]:
    """Return a bounded Python symbol/import index for the local repository."""
    return symbols_state(
        path=path,
        file=file,
        kind=kind,
        name=name,
        package=package,
        limit=limit,
        import_limit=import_limit,
        error_limit=error_limit,
        include_imports=include_imports,
        refresh=refresh,
    )


def relations(
    path: str = ".",
    file: str | None = None,
    symbol: str | None = None,
    kind: str | None = None,
    edge_limit: int = 500,
    node_limit: int = 500,
    error_limit: int = 50,
    refresh: bool = False,
) -> dict[str, object]:
    """Return a bounded conservative relationship graph for the repository."""
    return relationships_state(
        path=path,
        file=file,
        symbol=symbol,
        kind=kind,
        edge_limit=edge_limit,
        node_limit=node_limit,
        error_limit=error_limit,
        refresh=refresh,
    )


def impact(
    pr: int | None = None,
    issue: int | None = None,
    file: str | None = None,
    repository: str | None = None,
    path: str = ".",
    depth: int = 2,
    pr_limit: int = 20,
    event_limit: int = 300,
    file_limit: int = 100,
    changed_file_limit: int = 200,
    symbol_limit: int = 200,
    dependent_limit: int = 200,
    test_limit: int = 100,
    error_limit: int = 50,
    visit_limit: int = 2_000,
    refresh: bool = False,
) -> dict[str, object]:
    """Return bounded code impact for one PR, issue, or local file."""
    return impact_state(
        pr=pr,
        issue=issue,
        file=file,
        repository=repository,
        path=path,
        depth=depth,
        pr_limit=pr_limit,
        event_limit=event_limit,
        file_limit=file_limit,
        changed_file_limit=changed_file_limit,
        symbol_limit=symbol_limit,
        dependent_limit=dependent_limit,
        test_limit=test_limit,
        error_limit=error_limit,
        visit_limit=visit_limit,
        refresh=refresh,
    )
