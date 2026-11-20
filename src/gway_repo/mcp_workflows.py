"""Bounded, read-only GitHub Actions API adapter for MCP."""
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .auth import github_token

MAX_BYTES = 256 * 1024
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
# Remote is deployed separately from the appliance TARGETS registry.
SELF_REPOSITORY = "arthexis/gway-remote"


def _repositories():
    # Explicit initial parity with the legacy GWay Remote appliance registry.
    # A broader repository policy is a separate, operator-reviewed change.
    return (
        "arthexis/gway-remote",
        "arthexis/ocpp-csms",
        "arthexis/ocpp-reports",
        "arthexis/gway-lcd-sound",
        "arthexis/gway-agent-ops",
        "arthexis/gway-ap-kiosk",
        "arthexis/gway-rfid",
        "arthexis/gway-bastion",
        "arthexis/gway-repo",
    )


class WorkflowError(RuntimeError):
    pass


def _allowed(repository):
    allowed = set(_repositories())
    if repository not in allowed or not _REPO.fullmatch(repository):
        raise ValueError("repository_not_allowed")
    return repository


def _positive(value, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError("invalid_number")
    return value


def _request(repository, path, params=None, *, binary=False):
    _allowed(repository)
    token = github_token()
    if not token:
        raise WorkflowError("github_not_configured")
    url = "https://api.github.com/repos/" + repository + "/actions/" + path
    if params:
        url += "?" + urlencode(params)
    req = Request(url, headers={"Authorization": "Bearer " + token,
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28"}, method="GET")
    try:
        with urlopen(req, timeout=10) as response:
            data = response.read(MAX_BYTES + 1)
    except HTTPError as exc:
        exc.close()
        if exc.code in (401, 403, 404, 429):
            raise WorkflowError("github_http_" + str(exc.code)) from None
        raise WorkflowError("github_unavailable") from None
    except (URLError, TimeoutError, OSError):
        raise WorkflowError("github_unavailable") from None
    if len(data) > MAX_BYTES:
        raise WorkflowError("github_response_too_large")
    if binary:
        return data
    try:
        result = json.loads(data)
    except (ValueError, UnicodeError):
        raise WorkflowError("github_invalid_response") from None
    if not isinstance(result, dict):
        raise WorkflowError("github_invalid_response")
    return result


def _fields(item, fields):
    if not isinstance(item, dict):
        raise WorkflowError("github_invalid_response")
    return {field: item.get(field) for field in fields}


RUN = ("id", "name", "workflow_id", "head_branch", "head_sha", "event",
       "status", "conclusion", "created_at", "updated_at", "html_url")
JOB = ("id", "name", "status", "conclusion", "started_at", "completed_at", "html_url")
STEP = ("name", "number", "status", "conclusion", "started_at", "completed_at")


def runs(args):
    """Existing run listing, extended with opt-in stable page metadata."""
    allowed_keys = {"repository", "branch", "status", "event", "limit", "page", "workflow_id"}
    if type(args) is not dict or set(args) - allowed_keys:
        raise ValueError("invalid_arguments")
    limit = _positive(args.get("limit", 10), 20)
    page = _positive(args.get("page", 1), 50)
    if "workflow_id" in args and "repository" not in args:
        raise ValueError("repository_required_for_workflow")
    workflow_id = (_positive(args["workflow_id"], 10**18)
                   if "workflow_id" in args else None)
    repos = [args["repository"]] if "repository" in args else _repositories()
    for repo in repos:
        _allowed(repo)
    params = {"per_page": limit, "page": page}
    for key in ("branch", "event"):
        if key in args:
            v = args[key]
            if type(v) is not str or not re.fullmatch(r"[A-Za-z0-9_./-]{1,100}", v):
                raise ValueError("invalid_filter")
            params[key] = v
    if "status" in args:
        if type(args["status"]) is not str or args["status"] not in (
                "queued", "in_progress", "completed", "success", "failure",
                "cancelled", "timed_out", "neutral"):
            raise ValueError("invalid_filter")
        params["status"] = args["status"]
    results = []
    pagination = []
    for repo in repos:
        path = ("workflows/" + str(workflow_id) + "/runs"
                if workflow_id is not None else "runs")
        data = _request(repo, path, params)
        items = data.get("workflow_runs")
        if not isinstance(items, list) or len(items) > limit:
            raise WorkflowError("github_invalid_response")
        results.extend({"repository": repo, **_fields(x, RUN)} for x in items)
        if "page" in args or workflow_id is not None:
            total = data.get("total_count")
            if type(total) is not int or total < 0:
                raise WorkflowError("github_invalid_response")
            pagination.append({"repository": repo, "page": page,
                               "per_page": limit, "total_count": total,
                               "has_more": page * limit < total})
    output = {"source": "github_live", "runs": results}
    if pagination:
        output["pagination"] = {"by_repository": pagination,
                                "has_more": any(p["has_more"] for p in pagination)}
    return output

def status(args):
    if type(args) is not dict or set(args) != {"repository", "run_id"}:
        raise ValueError("invalid_arguments")
    repo, run_id = args["repository"], _positive(args["run_id"], 10**18)
    run = _request(repo, "runs/" + str(run_id))
    jobs = _request(repo, "runs/" + str(run_id) + "/jobs", {"per_page": 100})
    items = jobs.get("jobs")
    if (not isinstance(items, list) or type(jobs.get("total_count")) is not int
            or jobs["total_count"] > len(items)):
        raise WorkflowError("github_jobs_truncated")
    return {"source": "github_live", "repository": repo, "run": _fields(run, RUN),
            "jobs": [{**_fields(j, JOB), "steps": [_fields(s, STEP) for s in (j.get("steps") or [])]}
                     for j in items]}


def errors(args):
    if type(args) is not dict or set(args) - {"repository", "run_id", "job_id", "limit"} or not {"repository", "run_id"} <= set(args):
        raise ValueError("invalid_arguments")
    repo, run_id = args["repository"], _positive(args["run_id"], 10**18)
    limit = _positive(args.get("limit", 20), 30)
    job_id = _positive(args["job_id"], 10**18) if "job_id" in args else None
    record = status({"repository": repo, "run_id": run_id})
    jobs = [j for j in record["jobs"] if j["conclusion"] == "failure"][:5]
    if job_id is not None:
        jobs = [j for j in jobs if j["id"] == job_id]
    # Logs can contain arbitrary secrets despite masking. Do not export raw lines.
    # Return bounded job/step failure metadata instead of unsafe excerpts.
    summaries = []
    for job in jobs:
        detail = _request(repo, "jobs/" + str(job["id"]))
        summaries.append({"job": _fields(detail, JOB),
                          "failed_steps": [_fields(s, STEP) for s in detail.get("steps", [])
                                           if s.get("conclusion") == "failure"][:limit],
                          "log_excerpt": "withheld_untrusted_build_output"})
    return {"source": "github_live", "repository": repo, "run_id": run_id,
            "failures": summaries, "logs_available": False}
