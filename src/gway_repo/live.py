"""Live GitHub review, check, and workflow state collectors."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .github import GitHubAPIError, _body, _request_json, resolve_repository

_GRAPHQL_URL = "https://api.github.com/graphql"
_FAILURE_CONCLUSIONS = {
    "action_required",
    "cancelled",
    "failure",
    "startup_failure",
    "stale",
    "timed_out",
}


def _token() -> str | None:
    return os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")


def _request_graphql(query: str, variables: dict[str, object]) -> dict[str, Any]:
    token = _token()
    if not token:
        raise GitHubAPIError(
            "GitHub review-thread state requires GH_TOKEN or GITHUB_TOKEN"
        )

    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = Request(
        _GRAPHQL_URL,
        data=payload,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "gway-repo",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=20) as response:
            result = json.load(response)
    except HTTPError as exc:
        detail = exc.reason
        try:
            error_payload = json.load(exc)
            detail = error_payload.get("message", detail)
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass
        raise GitHubAPIError(f"GitHub GraphQL returned {exc.code}: {detail}") from exc
    except URLError as exc:
        raise GitHubAPIError(f"GitHub GraphQL request failed: {exc.reason}") from exc

    if not isinstance(result, dict):
        raise GitHubAPIError("GitHub GraphQL response was not an object")
    errors = result.get("errors")
    if errors:
        messages = [
            str(item.get("message"))
            for item in errors
            if isinstance(item, dict) and item.get("message")
        ]
        raise GitHubAPIError("GitHub GraphQL error: " + "; ".join(messages))
    data = result.get("data")
    if not isinstance(data, dict):
        raise GitHubAPIError("GitHub GraphQL response did not contain data")
    return data


def _head_sha(repository: str, number: int, sha: str | None) -> str:
    if sha:
        return sha
    payload = _request_json(repository, f"pulls/{int(number)}")
    if not isinstance(payload, dict):
        raise GitHubAPIError("GitHub pull-request response was not an object")
    head = payload.get("head")
    if not isinstance(head, dict) or not isinstance(head.get("sha"), str):
        raise GitHubAPIError("GitHub pull request did not provide a head SHA")
    return head["sha"]


def _review_decision(reviews: list[dict[str, object]]) -> str | None:
    latest: dict[str, str] = {}
    for review in reviews:
        author = review.get("author")
        state = review.get("state")
        if not isinstance(author, str) or not isinstance(state, str):
            continue
        normalized = state.upper()
        if normalized in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            latest[author] = normalized
    if any(state == "CHANGES_REQUESTED" for state in latest.values()):
        return "CHANGES_REQUESTED"
    if any(state == "APPROVED" for state in latest.values()):
        return "APPROVED"
    return None


def _review_submissions(
    repository: str,
    number: int,
    limit: int,
    body_limit: int,
) -> tuple[list[dict[str, object]], bool]:
    if limit < 0:
        raise ValueError("review_limit cannot be negative")
    if limit == 0:
        return [], False

    reviews: list[dict[str, object]] = []
    page = 1
    while len(reviews) < limit:
        per_page = min(100, limit - len(reviews))
        payload = _request_json(
            repository,
            f"pulls/{number}/reviews?per_page={per_page}&page={page}",
        )
        if not isinstance(payload, list):
            raise GitHubAPIError("GitHub reviews response was not a list")
        for item in payload:
            if not isinstance(item, dict):
                continue
            user = item.get("user")
            body, body_truncated = _body(item.get("body"), body_limit)
            reviews.append(
                {
                    "id": item.get("id"),
                    "author": user.get("login") if isinstance(user, dict) else None,
                    "state": item.get("state"),
                    "commit_id": item.get("commit_id"),
                    "submitted_at": item.get("submitted_at"),
                    "url": item.get("html_url"),
                    "body": body,
                    "body_truncated": body_truncated,
                }
            )
        if len(payload) < per_page:
            return reviews, False
        page += 1
    return reviews[:limit], True


def _review_threads(
    repository: str,
    number: int,
    limit: int,
    body_limit: int,
) -> tuple[str | None, list[dict[str, object]], bool]:
    if limit < 0 or limit > 100:
        raise ValueError("thread_limit must be between 0 and 100")
    if limit == 0:
        return None, [], False

    owner, name = repository.split("/", 1)
    query = """
    query($owner: String!, $name: String!, $number: Int!, $limit: Int!) {
      repository(owner: $owner, name: $name) {
        pullRequest(number: $number) {
          reviewDecision
          reviewThreads(first: $limit) {
            pageInfo { hasNextPage }
            nodes {
              id
              isResolved
              isOutdated
              path
              line
              startLine
              comments(first: 1) {
                totalCount
                nodes {
                  author { login }
                  body
                  url
                  createdAt
                }
              }
            }
          }
        }
      }
    }
    """
    data = _request_graphql(
        query,
        {"owner": owner, "name": name, "number": int(number), "limit": limit},
    )
    repo_data = data.get("repository")
    pull = repo_data.get("pullRequest") if isinstance(repo_data, dict) else None
    if not isinstance(pull, dict):
        raise GitHubAPIError("GitHub GraphQL could not resolve the pull request")
    container = pull.get("reviewThreads")
    if not isinstance(container, dict):
        return pull.get("reviewDecision"), [], False

    threads: list[dict[str, object]] = []
    nodes = container.get("nodes")
    if isinstance(nodes, list):
        for node in nodes:
            if not isinstance(node, dict):
                continue
            comments = node.get("comments")
            comment_nodes = comments.get("nodes") if isinstance(comments, dict) else None
            first = (
                comment_nodes[0]
                if isinstance(comment_nodes, list)
                and comment_nodes
                and isinstance(comment_nodes[0], dict)
                else None
            )
            author = first.get("author") if isinstance(first, dict) else None
            body, body_truncated = _body(
                first.get("body") if isinstance(first, dict) else None,
                body_limit,
            )
            threads.append(
                {
                    "id": node.get("id"),
                    "resolved": bool(node.get("isResolved", False)),
                    "outdated": bool(node.get("isOutdated", False)),
                    "path": node.get("path"),
                    "line": node.get("line"),
                    "start_line": node.get("startLine"),
                    "comment_count": (
                        comments.get("totalCount") if isinstance(comments, dict) else 0
                    ),
                    "comment": {
                        "author": (
                            author.get("login") if isinstance(author, dict) else None
                        ),
                        "body": body,
                        "body_truncated": body_truncated,
                        "url": first.get("url") if isinstance(first, dict) else None,
                        "created_at": (
                            first.get("createdAt") if isinstance(first, dict) else None
                        ),
                    },
                }
            )
    page_info = container.get("pageInfo")
    truncated = bool(
        page_info.get("hasNextPage") if isinstance(page_info, dict) else False
    )
    decision = pull.get("reviewDecision")
    return decision if isinstance(decision, str) else None, threads, truncated


def review_state(
    number: int,
    repository: str | None = None,
    path: str = ".",
    unresolved_only: bool = False,
    review_limit: int = 100,
    thread_limit: int = 50,
    body_limit: int = 2_000,
) -> dict[str, object]:
    """Return review submissions and, when authenticated, review-thread state."""
    repo = resolve_repository(repository, path)
    number = int(number)
    reviews, reviews_truncated = _review_submissions(
        repo, number, review_limit, body_limit
    )

    threads_available = _token() is not None and thread_limit > 0
    threads: list[dict[str, object]] = []
    threads_truncated = False
    graphql_decision: str | None = None
    if threads_available:
        graphql_decision, threads, threads_truncated = _review_threads(
            repo, number, thread_limit, body_limit
        )
    elif unresolved_only:
        raise GitHubAPIError(
            "--unresolved-only requires GH_TOKEN or GITHUB_TOKEN for GraphQL thread state"
        )

    unresolved = [thread for thread in threads if not thread["resolved"]]
    selected_threads = unresolved if unresolved_only else threads
    return {
        "kind": "reviews",
        "repository": repo,
        "number": number,
        "decision": graphql_decision or _review_decision(reviews),
        "decision_source": "graphql" if graphql_decision else "review_submissions",
        "reviews": reviews,
        "reviews_truncated": reviews_truncated,
        "threads_available": threads_available,
        "threads": selected_threads,
        "threads_truncated": threads_truncated,
        "unresolved_count": len(unresolved) if threads_available else None,
    }


def check_state(
    number: int,
    repository: str | None = None,
    path: str = ".",
    sha: str | None = None,
    check_limit: int = 100,
    summary_limit: int = 2_000,
) -> dict[str, object]:
    """Return check-run and commit-status state for a pull request head."""
    if check_limit < 0 or check_limit > 100:
        raise ValueError("check_limit must be between 0 and 100")
    repo = resolve_repository(repository, path)
    number = int(number)
    head_sha = _head_sha(repo, number, sha)

    check_runs: list[dict[str, object]] = []
    total_count = 0
    if check_limit:
        payload = _request_json(
            repo,
            f"commits/{head_sha}/check-runs?per_page={check_limit}",
        )
        if not isinstance(payload, dict):
            raise GitHubAPIError("GitHub check-runs response was not an object")
        total_count = int(payload.get("total_count") or 0)
        raw_runs = payload.get("check_runs")
        if isinstance(raw_runs, list):
            for item in raw_runs[:check_limit]:
                if not isinstance(item, dict):
                    continue
                app = item.get("app")
                output = item.get("output")
                summary, summary_truncated = _body(
                    output.get("summary") if isinstance(output, dict) else None,
                    summary_limit,
                )
                check_runs.append(
                    {
                        "name": item.get("name"),
                        "status": item.get("status"),
                        "conclusion": item.get("conclusion"),
                        "started_at": item.get("started_at"),
                        "completed_at": item.get("completed_at"),
                        "url": item.get("html_url"),
                        "app": (
                            app.get("slug") or app.get("name")
                            if isinstance(app, dict)
                            else None
                        ),
                        "title": output.get("title") if isinstance(output, dict) else None,
                        "summary": summary,
                        "summary_truncated": summary_truncated,
                    }
                )

    status_payload = _request_json(repo, f"commits/{head_sha}/status?per_page=100")
    if not isinstance(status_payload, dict):
        raise GitHubAPIError("GitHub commit-status response was not an object")
    statuses: list[dict[str, object]] = []
    raw_statuses = status_payload.get("statuses")
    if isinstance(raw_statuses, list):
        for item in raw_statuses:
            if not isinstance(item, dict):
                continue
            statuses.append(
                {
                    "context": item.get("context"),
                    "state": item.get("state"),
                    "description": item.get("description"),
                    "url": item.get("target_url"),
                    "updated_at": item.get("updated_at"),
                }
            )

    problem_checks = [
        item.get("name")
        for item in check_runs
        if item.get("conclusion") in _FAILURE_CONCLUSIONS
    ]
    pending_checks = [
        item.get("name") for item in check_runs if item.get("status") != "completed"
    ]
    return {
        "kind": "checks",
        "repository": repo,
        "number": number,
        "head_sha": head_sha,
        "state": status_payload.get("state"),
        "check_runs": check_runs,
        "check_runs_truncated": total_count > len(check_runs),
        "statuses": statuses,
        "problem_checks": problem_checks,
        "pending_checks": pending_checks,
    }


def workflow_state(
    number: int,
    repository: str | None = None,
    path: str = ".",
    sha: str | None = None,
    run_limit: int = 20,
    job_limit: int = 50,
) -> dict[str, object]:
    """Return bounded Actions run/job state for a pull request head."""
    if run_limit < 0 or run_limit > 100:
        raise ValueError("run_limit must be between 0 and 100")
    if job_limit < 0 or job_limit > 100:
        raise ValueError("job_limit must be between 0 and 100")
    repo = resolve_repository(repository, path)
    number = int(number)
    head_sha = _head_sha(repo, number, sha)

    if run_limit == 0:
        return {
            "kind": "workflows",
            "repository": repo,
            "number": number,
            "head_sha": head_sha,
            "runs": [],
            "runs_truncated": False,
        }

    payload = _request_json(
        repo,
        f"actions/runs?head_sha={head_sha}&per_page={run_limit}",
    )
    if not isinstance(payload, dict):
        raise GitHubAPIError("GitHub workflow-runs response was not an object")
    total_count = int(payload.get("total_count") or 0)
    raw_runs = payload.get("workflow_runs")
    runs: list[dict[str, object]] = []
    if isinstance(raw_runs, list):
        for item in raw_runs[:run_limit]:
            if not isinstance(item, dict):
                continue
            run_id = item.get("id")
            jobs: list[dict[str, object]] = []
            jobs_truncated = False
            if job_limit and isinstance(run_id, int):
                jobs_payload = _request_json(
                    repo,
                    f"actions/runs/{run_id}/jobs?filter=latest&per_page={job_limit}",
                )
                if not isinstance(jobs_payload, dict):
                    raise GitHubAPIError("GitHub workflow-jobs response was not an object")
                raw_jobs = jobs_payload.get("jobs")
                if isinstance(raw_jobs, list):
                    for job in raw_jobs[:job_limit]:
                        if not isinstance(job, dict):
                            continue
                        raw_steps = job.get("steps")
                        failed_steps: list[dict[str, object]] = []
                        if isinstance(raw_steps, list):
                            for step in raw_steps:
                                if not isinstance(step, dict):
                                    continue
                                if step.get("conclusion") in _FAILURE_CONCLUSIONS:
                                    failed_steps.append(
                                        {
                                            "number": step.get("number"),
                                            "name": step.get("name"),
                                            "conclusion": step.get("conclusion"),
                                        }
                                    )
                        jobs.append(
                            {
                                "id": job.get("id"),
                                "name": job.get("name"),
                                "status": job.get("status"),
                                "conclusion": job.get("conclusion"),
                                "url": job.get("html_url"),
                                "started_at": job.get("started_at"),
                                "completed_at": job.get("completed_at"),
                                "failed_steps": failed_steps,
                            }
                        )
                jobs_truncated = int(jobs_payload.get("total_count") or 0) > len(jobs)

            runs.append(
                {
                    "id": run_id,
                    "name": item.get("name"),
                    "event": item.get("event"),
                    "status": item.get("status"),
                    "conclusion": item.get("conclusion"),
                    "run_number": item.get("run_number"),
                    "attempt": item.get("run_attempt"),
                    "url": item.get("html_url"),
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                    "jobs": jobs,
                    "jobs_truncated": jobs_truncated,
                }
            )

    return {
        "kind": "workflows",
        "repository": repo,
        "number": number,
        "head_sha": head_sha,
        "runs": runs,
        "runs_truncated": total_count > len(runs),
    }
