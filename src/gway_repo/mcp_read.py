"""Bounded, GET-only GitHub branch rules and commit checks for MCP."""

import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .auth import github_token
from .mcp_workflows import WorkflowError, _allowed

_SHA = re.compile(r"^[0-9a-f]{40}$")
_BRANCH = re.compile(r"^[A-Za-z0-9_.\/-]{1,200}$")
PAGE_SIZE = 100
MAX_PAGES = 10
MAX_BYTES = 256 * 1024


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _get(repository, path, params=None):
    """Only a caller-selected, allowlisted repository; paths built internally."""
    _allowed(repository)
    token = github_token()
    if not token:
        raise WorkflowError("github_not_configured")
    url = "https://api.github.com/repos/" + repository + "/" + path
    if params:
        url += "?" + urlencode(params)
    req = Request(
        url,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    try:
        with build_opener(_NoRedirect).open(req, timeout=10) as response:
            body = response.read(MAX_BYTES + 1)
    except HTTPError as exc:
        code = exc.code
        exc.close()
        if code in (301, 302, 303, 307, 308):
            raise WorkflowError("github_redirect_rejected") from None
        if code in (401, 403, 404, 429):
            raise WorkflowError("github_http_" + str(code)) from None
        raise WorkflowError("github_unavailable") from None
    except (URLError, TimeoutError, OSError):
        raise WorkflowError("github_unavailable") from None
    if len(body) > MAX_BYTES:
        raise WorkflowError("github_response_too_large")
    try:
        return json.loads(body)
    except (UnicodeError, ValueError):
        raise WorkflowError("github_invalid_response") from None


def _pages(repository, path, key, *, params=None):
    """Fail closed on missing counts, truncated pages, duplicates and page cap."""
    result = []
    seen = set()
    total = None
    for page in range(1, MAX_PAGES + 1):
        data = _get(
            repository, path, {**(params or {}), "per_page": PAGE_SIZE, "page": page}
        )
        if not isinstance(data, dict) or type(data.get("total_count")) is not int:
            raise WorkflowError("github_invalid_response")
        entries = data.get(key)
        if not isinstance(entries, list) or data["total_count"] < 0:
            raise WorkflowError("github_invalid_response")
        if total is None:
            total = data["total_count"]
        elif total != data["total_count"]:
            raise WorkflowError("github_inconsistent_pagination")
        for item in entries:
            if not isinstance(item, dict) or type(item.get("id")) is not int:
                raise WorkflowError("github_invalid_response")
            if item["id"] in seen:
                raise WorkflowError("github_inconsistent_pagination")
            seen.add(item["id"])
            result.append(item)
        if len(result) == total:
            return result
        if len(result) > total or len(entries) != PAGE_SIZE:
            raise WorkflowError("github_incomplete_pagination")
    raise WorkflowError("github_incomplete_pagination")


def _array_pages(repository, path, *, detect_rule_overlap=False):
    """Array endpoint: short page proves termination; detect verifiable page overlap."""
    result = []
    prior_page_fingerprints = set()
    for page in range(1, MAX_PAGES + 1):
        chunk = _get(repository, path, {"per_page": PAGE_SIZE, "page": page})
        if not isinstance(chunk, list) or any(not isinstance(x, dict) for x in chunk):
            raise WorkflowError("github_invalid_response")
        if detect_rule_overlap:
            # A rule's type alone is not an identity. Different rulesets and
            # parameters may legitimately yield the same type. Identical
            # records across distinct pages cannot certify completeness.
            fingerprints = {
                json.dumps(
                    item, sort_keys=True, separators=(",", ":"), ensure_ascii=True
                )
                for item in chunk
            }
            if fingerprints & prior_page_fingerprints:
                raise WorkflowError("github_inconsistent_pagination")
            prior_page_fingerprints.update(fingerprints)
        result.extend(chunk)
        if len(chunk) > PAGE_SIZE:
            raise WorkflowError("github_invalid_response")
        if len(chunk) < PAGE_SIZE:
            return result
    raise WorkflowError("github_incomplete_pagination")


def branch_effective_rules(args):
    if type(args) is not dict or set(args) != {"repository", "branch"}:
        raise ValueError("invalid_arguments")
    repository = _allowed(args["repository"])
    branch = args["branch"]
    if (
        type(branch) is not str
        or not _BRANCH.fullmatch(branch)
        or ".." in branch
        or branch.startswith(("/", "."))
    ):
        raise ValueError("invalid_branch")
    # GitHub's effective-rules endpoint returns an array, not a ruleset object.
    data = _array_pages(
        repository, "rules/branches/" + quote(branch, safe=""), detect_rule_overlap=True
    )
    rules = []
    for rule in data:
        if (
            not isinstance(rule, dict)
            or type(rule.get("type")) is not str
            or not rule["type"]
            or ("parameters" in rule and not isinstance(rule["parameters"], dict))
            or ("ruleset_id" in rule and type(rule["ruleset_id"]) is not int)
        ):
            raise WorkflowError("github_invalid_response")
        rules.append(
            {
                "type": rule["type"],
                "ruleset_id": rule.get("ruleset_id"),
                "ruleset_source_type": rule.get("ruleset_source_type"),
                "ruleset_source": rule.get("ruleset_source"),
                "parameters": rule.get("parameters"),
            }
        )
    return {
        "schema": "github/rules/v1",
        "source": "github_live",
        "repository": repository,
        "branch": branch,
        "complete": True,
        "rules": rules,
    }


def commit_checks(args):
    if type(args) is not dict or set(args) != {"repository", "sha"}:
        raise ValueError("invalid_arguments")
    repository = _allowed(args["repository"])
    sha = args["sha"]
    if type(sha) is not str or not _SHA.fullmatch(sha):
        raise ValueError("invalid_sha")
    # The reference endpoint is limited to the 1,000 most recent check suites.
    # Fail closed when an exhaustive snapshot cannot be certified.
    suites = _get(
        repository, "commits/" + sha + "/check-suites", {"per_page": 1, "page": 1}
    )
    if (
        not isinstance(suites, dict)
        or type(suites.get("total_count")) is not int
        or suites["total_count"] < 0
        or not isinstance(suites.get("check_suites"), list)
    ):
        raise WorkflowError("github_invalid_response")
    # With per_page=1 the first page must have exactly one entry when
    # total_count is positive, or none when it is zero.
    if len(suites["check_suites"]) != (1 if suites["total_count"] else 0):
        raise WorkflowError("github_invalid_response")
    if suites["total_count"] >= 1000:
        raise WorkflowError("github_check_suite_coverage_unknown")
    checks = _pages(
        repository,
        "commits/" + sha + "/check-runs",
        "check_runs",
        params={"filter": "all"},
    )
    classic = _array_pages(repository, "commits/" + sha + "/statuses")
    normalized = []
    for item in checks:
        app = item.get("app")
        if (
            item.get("head_sha") != sha
            or type(item.get("name")) is not str
            or not item["name"]
            or item.get("status")
            not in (
                "queued",
                "in_progress",
                "completed",
                "waiting",
                "pending",
                "requested",
            )
            or (
                item["status"] == "completed"
                and type(item.get("conclusion")) is not str
            )
            or (item["status"] != "completed" and item.get("conclusion") is not None)
            or not isinstance(app, (dict, type(None)))
            or (app is not None and type(app.get("id")) is not int)
        ):
            raise WorkflowError("github_invalid_response")
        normalized.append(
            {
                "id": item["id"],
                "name": item.get("name"),
                "status": item.get("status"),
                "conclusion": item.get("conclusion"),
                "app_id": app.get("id") if app else None,
                "details_url": item.get("details_url"),
            }
        )
    states, seen = [], set()
    for item in classic:
        ident, creator = item.get("id"), item.get("creator")
        if (
            type(ident) is not int
            or ident in seen
            or item.get("sha") not in (None, sha)
            or type(item.get("context")) is not str
            or not item["context"]
            or item.get("state") not in ("pending", "success", "failure", "error")
            or (
                creator is not None
                and (
                    not isinstance(creator, dict) or type(creator.get("id")) is not int
                )
            )
        ):
            raise WorkflowError("github_invalid_response")
        seen.add(ident)
        states.append(
            {
                "id": ident,
                "context": item["context"],
                "state": item["state"],
                "creator_id": creator.get("id") if creator else None,
            }
        )
    return {
        "schema": "github/checks/v1",
        "source": "github_live",
        "repository": repository,
        "sha": sha,
        "complete": True,
        "coverage": "all_check_runs_for_ref_under_1000_suites",
        "check_runs": normalized,
        "statuses": states,
    }
