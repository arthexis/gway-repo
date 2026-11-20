"""Bounded GitHub Actions control using the existing MCP GitHub token and scopes.

This module only calls GitHub's Actions API. It never performs node deployment,
SSH, shell execution, or alters workflow YAML.
"""
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .auth import github_token
from .mcp_workflows import WorkflowError, _allowed, _positive, _request, runs

MAX_INPUTS = 10
MAX_INPUT_LENGTH = 200
_REF = re.compile(r"^[A-Za-z0-9_./-]{1,200}$")
_INPUT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")


def _arguments(args, required, optional=()):
    if (type(args) is not dict or not set(required) <= set(args)
            or set(args) - set(required) - set(optional)):
        raise ValueError("invalid_arguments")
    return _allowed(args["repository"])


def _reference(value):
    if (type(value) is not str or not _REF.fullmatch(value)
            or value.startswith((".", "/")) or value.endswith("/")
            or ".." in value or "//" in value):
        raise ValueError("invalid_ref")
    return value


def workflow_list(args):
    repository = _arguments(args, ("repository",), ("page", "limit"))
    page = _positive(args.get("page", 1), 50)
    limit = _positive(args.get("limit", 20), 50)
    data = _request(repository, "workflows", {"per_page": limit, "page": page})
    items, total = data.get("workflows"), data.get("total_count")
    if (type(total) is not int or total < 0 or not isinstance(items, list)
            or len(items) > limit):
        raise WorkflowError("github_invalid_response")
    workflows = []
    for item in items:
        if (type(item) is not dict or type(item.get("id")) is not int
                or not isinstance(item.get("name"), str)
                or not isinstance(item.get("state"), str)):
            raise WorkflowError("github_invalid_response")
        workflows.append({key: item.get(key) for key in (
            "id", "name", "path", "state", "html_url")})
    return {"source": "github_live", "repository": repository,
            "workflows": workflows,
            "pagination": {"page": page, "per_page": limit,
                           "total_count": total, "has_more": page * limit < total}}


def workflow_health(args):
    """Report only the sampled latest runs, never claim complete CI coverage."""
    repository = _arguments(args, ("repository",), ("branch", "limit"))
    limit = _positive(args.get("limit", 10), 20)
    query = {"repository": repository, "limit": limit, "page": 1}
    if "branch" in args:
        query["branch"] = _reference(args["branch"])
    data = runs(query)
    recent = data["runs"]
    counts = {"success": 0, "failure": 0, "pending": 0, "other": 0}
    failures, pending = [], []
    for item in recent:
        if item["status"] != "completed":
            counts["pending"] += 1
            pending.append({key: item.get(key) for key in (
                "id", "name", "status", "html_url")})
        elif item["conclusion"] in ("failure", "timed_out", "action_required"):
            counts["failure"] += 1
            failures.append({key: item.get(key) for key in (
                "id", "name", "conclusion", "html_url")})
        elif item["conclusion"] == "success":
            counts["success"] += 1
        else:
            counts["other"] += 1
    if not recent:
        state = "unknown"
    elif counts["failure"]:
        state = "failure_observed"
    elif counts["pending"]:
        state = "in_progress"
    elif counts["other"]:
        state = "mixed"
    else:
        state = "recent_success"
    return {"schema": "github/workflow-health/v1", "source": "github_live",
            "repository": repository, "scope": "recent_runs_only",
            "state": state, "sampled": len(recent), "counts": counts,
            "failures": failures, "pending": pending,
            "older_runs_unchecked": data["pagination"]["has_more"]}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _post(repository, path, payload, expected_status):
    _allowed(repository)
    token = github_token()
    if not token:
        raise WorkflowError("github_not_configured")
    request = Request(
        "https://api.github.com/repos/" + repository + "/actions/" + path,
        data=json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28",
                 "Content-Type": "application/json"},
        method="POST")
    try:
        with build_opener(_NoRedirect).open(request, timeout=10) as response:
            if response.getcode() != expected_status:
                raise WorkflowError("github_unexpected_response")
    except HTTPError as exc:
        code = exc.code
        exc.close()
        if code in (301, 302, 303, 307, 308):
            raise WorkflowError("github_redirect_rejected") from None
        if code in (401, 403, 404, 409, 422, 429):
            raise WorkflowError("github_http_" + str(code)) from None
        raise WorkflowError("github_result_unknown") from None
    except (URLError, TimeoutError, OSError):
        # A failed connection after submission cannot prove that GitHub did
        # not accept the operation. Never retry automatically.
        raise WorkflowError("github_result_unknown") from None


def workflow_dispatch(args):
    repository = _arguments(args, ("repository", "workflow_id", "ref"), ("inputs",))
    workflow_id = _positive(args["workflow_id"], 10**18)
    ref = _reference(args["ref"])
    # A caller-selected workflow from an unreviewed branch could execute on
    # a privileged self-hosted runner. Only the reviewed default branch is
    # permitted in this initial control surface.
    if ref != "main":
        raise ValueError("unsupported_ref")
    inputs = args.get("inputs", {})
    if (type(inputs) is not dict or len(inputs) > MAX_INPUTS
            or any(type(k) is not str or not _INPUT_NAME.fullmatch(k)
                   or type(v) is not str or len(v) > MAX_INPUT_LENGTH
                   for k, v in inputs.items())):
        raise ValueError("invalid_inputs")
    _post(repository, "workflows/" + str(workflow_id) + "/dispatches",
          {"ref": ref, "inputs": inputs}, 204)
    return {"source": "github_live", "repository": repository,
            "workflow_id": workflow_id, "ref": ref,
            "accepted": True, "execution_verified": False,
            "note": "Accepted by GitHub; check runs for the resulting workflow execution"}


def workflow_cancel(args):
    repository = _arguments(args, ("repository", "run_id"))
    run_id = _positive(args["run_id"], 10**18)
    _post(repository, "runs/" + str(run_id) + "/cancel", {}, 202)
    return {"source": "github_live", "repository": repository,
            "run_id": run_id, "accepted": True, "cancelled_verified": False}
