"""Bounded unified repository context assembly."""

from __future__ import annotations

from .github import issue_state, pull_request_state, resolve_repository
from .live import check_state, review_state, workflow_state

_SUCCESS_CONCLUSIONS = {"success", "neutral", "skipped"}


def _head_sha(pull: dict[str, object]) -> str:
    head = pull.get("head")
    if not isinstance(head, dict) or not isinstance(head.get("sha"), str):
        raise TypeError("pull-request context requires a head SHA")
    return head["sha"]


def _workflow_summary(
    workflows: dict[str, object] | None,
) -> tuple[list[str], list[str]]:
    if not workflows:
        return [], []

    failed: list[str] = []
    pending: list[str] = []
    runs = workflows.get("runs")
    if not isinstance(runs, list):
        return failed, pending

    for run in runs:
        if not isinstance(run, dict):
            continue
        name = run.get("name")
        label = str(name) if name is not None else f"run:{run.get('id')}"
        status = run.get("status")
        conclusion = run.get("conclusion")
        if status != "completed":
            pending.append(label)
        elif conclusion is not None and conclusion not in _SUCCESS_CONCLUSIONS:
            failed.append(label)
    return failed, pending


def _pr_summary(
    pull: dict[str, object],
    reviews: dict[str, object] | None,
    checks: dict[str, object] | None,
    workflows: dict[str, object] | None,
) -> dict[str, object]:
    problem_checks = checks.get("problem_checks", []) if checks else []
    pending_checks = checks.get("pending_checks", []) if checks else []
    failed_workflows, pending_workflows = _workflow_summary(workflows)
    review_decision = reviews.get("decision") if reviews else None
    unresolved_threads = reviews.get("unresolved_count") if reviews else None

    attention = bool(
        pull.get("mergeable") is False
        or review_decision == "CHANGES_REQUESTED"
        or (isinstance(unresolved_threads, int) and unresolved_threads > 0)
        or problem_checks
        or pending_checks
        or failed_workflows
        or pending_workflows
    )

    return {
        "state": pull.get("state"),
        "draft": pull.get("draft"),
        "mergeable": pull.get("mergeable"),
        "mergeable_state": pull.get("mergeable_state"),
        "review_decision": review_decision,
        "unresolved_threads": unresolved_threads,
        "problem_checks": problem_checks,
        "pending_checks": pending_checks,
        "failed_workflows": failed_workflows,
        "pending_workflows": pending_workflows,
        "attention": attention,
    }


def context_state(
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
    """Return one bounded context envelope for a PR or issue."""
    if (pr is None) == (issue is None):
        raise ValueError("pass exactly one of --pr or --issue")

    repo = resolve_repository(repository, path)

    if issue is not None:
        number = int(issue)
        item = issue_state(
            number,
            repository=repo,
            path=path,
            body_limit=body_limit,
        )
        return {
            "kind": "context",
            "repository": repo,
            "target": {"kind": "issue", "number": number},
            "included_sections": ["issue"],
            "summary": {
                "state": item.get("state"),
                "state_reason": item.get("state_reason"),
                "labels": item.get("labels", []),
                "comments": item.get("comments", 0),
            },
            "issue": item,
            "pull_request": None,
            "reviews": None,
            "checks": None,
            "workflows": None,
        }

    number = int(pr)
    pull = pull_request_state(
        number,
        repository=repo,
        path=path,
        body_limit=body_limit,
        file_limit=file_limit,
    )
    head_sha = _head_sha(pull)

    reviews = (
        review_state(
            number,
            repository=repo,
            path=path,
            review_limit=review_limit,
            thread_limit=thread_limit,
            body_limit=review_body_limit,
        )
        if include_reviews
        else None
    )
    checks = (
        check_state(
            number,
            repository=repo,
            path=path,
            sha=head_sha,
            check_limit=check_limit,
            summary_limit=summary_limit,
        )
        if include_checks
        else None
    )
    workflows = (
        workflow_state(
            number,
            repository=repo,
            path=path,
            sha=head_sha,
            run_limit=run_limit,
            job_limit=job_limit,
        )
        if include_workflows
        else None
    )

    included = ["pull_request"]
    if reviews is not None:
        included.append("reviews")
    if checks is not None:
        included.append("checks")
    if workflows is not None:
        included.append("workflows")

    return {
        "kind": "context",
        "repository": repo,
        "target": {"kind": "pull_request", "number": number},
        "head_sha": head_sha,
        "included_sections": included,
        "summary": _pr_summary(pull, reviews, checks, workflows),
        "issue": None,
        "pull_request": pull,
        "reviews": reviews,
        "checks": checks,
        "workflows": workflows,
    }
