"""Node-owned, bounded MCP stdio adapter for repository intelligence.

No HTTP, SSH, arbitrary checkout paths or caller-supplied credentials.
Transport authorization and release registration belong to GWay Remote.
"""

import json
import re
import sys

from . import mcp_actions, mcp_local, mcp_read, mcp_workflows
from .context import context_state
from .github import issue_state, pull_request_state
from .links import issue_pull_requests_state
from .live import check_state, review_state, workflow_state

PROTOCOL = "2025-03-26"
MAX_LINE = 256 * 1024
_REPO = re.compile(r"^arthexis/[A-Za-z0-9_.-]{1,100}$")


def _tool(name, description):
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": {
                "repository": {"type": "string"},
                "number": {"type": "integer", "minimum": 1},
            },
            "required": ["repository", "number"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    }


TOOLS = [
    _tool("pr", "Read bounded pull-request metadata."),
    _tool("issue", "Read bounded issue metadata."),
    _tool("reviews", "Read a pull request's review state."),
    _tool("checks", "Read a pull request's CI checks."),
    _tool("workflows", "Read a pull request's workflow runs."),
    _tool("prs", "Find pull requests linked to an issue."),
    _tool("context", "Read GitHub context without local filesystem access."),
]


def _github_tool(name, description, properties, required, *, write=False):
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": not write,
            "destructiveHint": write,
            "idempotentHint": not write,
        },
    }


_REPOSITORY = {"type": "string"}
_NUMBER = {"type": "integer", "minimum": 1}
_TEXT = {"type": "string"}

TOOLS.extend(
    [
        _github_tool(
            "github_workflow_runs",
            "List bounded paginated Actions runs from reviewed repositories.",
            {
                "repository": _REPOSITORY,
                "branch": _TEXT,
                "status": _TEXT,
                "event": _TEXT,
                "limit": _NUMBER,
                "page": _NUMBER,
                "workflow_id": _NUMBER,
            },
            [],
        ),
        _github_tool(
            "github_workflow_status",
            "Read a run's latest jobs and step metadata.",
            {"repository": _REPOSITORY, "run_id": _NUMBER},
            ["repository", "run_id"],
        ),
        _github_tool(
            "github_workflow_errors",
            "Read failed job and step metadata without raw build logs.",
            {
                "repository": _REPOSITORY,
                "run_id": _NUMBER,
                "job_id": _NUMBER,
                "limit": _NUMBER,
            },
            ["repository", "run_id"],
        ),
        _github_tool(
            "github_branch_effective_rules",
            "Read the complete effective rules on one GitHub branch.",
            {"repository": _REPOSITORY, "branch": _TEXT},
            ["repository", "branch"],
        ),
        _github_tool(
            "github_commit_checks",
            "Read bounded and completeness-checked commit checks.",
            {"repository": _REPOSITORY, "sha": _TEXT},
            ["repository", "sha"],
        ),
        _github_tool(
            "github_workflow_list",
            "Page through GitHub Actions workflow definitions.",
            {"repository": _REPOSITORY, "page": _NUMBER, "limit": _NUMBER},
            ["repository"],
        ),
        _github_tool(
            "github_workflow_health",
            "Summarize only sampled recent GitHub Actions runs.",
            {"repository": _REPOSITORY, "branch": _TEXT, "limit": _NUMBER},
            ["repository"],
        ),
        _github_tool(
            "github_workflow_dispatch",
            "Request workflow_dispatch on reviewed main using existing execute scope.",
            {
                "repository": _REPOSITORY,
                "workflow_id": _NUMBER,
                "ref": _TEXT,
                "inputs": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
            },
            ["repository", "workflow_id", "ref"],
            write=True,
        ),
        _github_tool(
            "github_workflow_cancel",
            "Request cancellation of one Actions run using execute scope.",
            {"repository": _REPOSITORY, "run_id": _NUMBER},
            ["repository", "run_id"],
            write=True,
        ),
    ]
)

# Local intelligence is dispatched only by repository identity, never by a host path.
_LOCAL_FIELDS = {
    "map": {"file_limit": {"type": "integer", "minimum": 1, "maximum": 100}},
    "files": {
        "kind": _TEXT, "extension": _TEXT, "package": _TEXT,
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
    },
    "symbols": {
        "file": _TEXT, "kind": _TEXT, "name": _TEXT, "package": _TEXT,
        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
        "import_limit": {"type": "integer", "minimum": 0, "maximum": 100},
        "error_limit": {"type": "integer", "minimum": 0, "maximum": 30},
        "include_imports": {"type": "boolean"},
    },
    "relations": {
        "file": _TEXT, "symbol": _TEXT, "kind": _TEXT,
        "edge_limit": {"type": "integer", "minimum": 1, "maximum": 200},
        "node_limit": {"type": "integer", "minimum": 1, "maximum": 200},
        "error_limit": {"type": "integer", "minimum": 0, "maximum": 30},
    },
    "impact": {
        "file": _TEXT, "pr": _NUMBER, "issue": _NUMBER,
        "depth": {"type": "integer", "minimum": 0, "maximum": 3},
    },
    "code": {
        "file": _TEXT, "symbol": _TEXT,
        "start_line": _NUMBER, "end_line": _NUMBER,
        "context": {"type": "integer", "minimum": 0, "maximum": 10},
        "max_lines": {"type": "integer", "minimum": 1, "maximum": 160},
        "max_chars": {"type": "integer", "minimum": 1, "maximum": 12000},
    },
}
_LOCAL_DOCS = {
    "map": "Inspect the bounded Git-index repository structure.",
    "files": "List tracked repository files using bounded filters.",
    "symbols": "Find bounded Python symbol definitions and imports.",
    "relations": "Inspect conservative static dependency relationships.",
    "impact": "Inspect bounded change impact for a file, PR or issue.",
    "code": "Read a bounded Git-index source snippet by file or symbol.",
}
for _name, _fields in _LOCAL_FIELDS.items():
    TOOLS.append(
        _github_tool(
            _name,
            _LOCAL_DOCS[_name],
            {
                "repository": {
                    "type": "string",
                    "enum": [
                        "arthexis/gway-repo",
                        "arthexis/gway-remote",
                        "arthexis/ocpp-csms",
                    ],
                },
                **_fields,
            },
            ["repository"],
        )
    )


_GITHUB_HANDLERS = {
    "github_workflow_runs": mcp_workflows.runs,
    "github_workflow_status": mcp_workflows.status,
    "github_workflow_errors": mcp_workflows.errors,
    "github_branch_effective_rules": mcp_read.branch_effective_rules,
    "github_commit_checks": mcp_read.commit_checks,
    "github_workflow_list": mcp_actions.workflow_list,
    "github_workflow_health": mcp_actions.workflow_health,
    "github_workflow_dispatch": mcp_actions.workflow_dispatch,
    "github_workflow_cancel": mcp_actions.workflow_cancel,
}


def _invoke(name, args):
    if name in _LOCAL_FIELDS:
        return mcp_local.invoke(name, args)
    if name in _GITHUB_HANDLERS:
        return _GITHUB_HANDLERS[name](args)
    if (
        type(args) is not dict
        or set(args) != {"repository", "number"}
        or type(args["number"]) is not int
        or args["number"] < 1
        or type(args["repository"]) is not str
        or not _REPO.fullmatch(args["repository"])
    ):
        raise ValueError("invalid_arguments")
    repository = args["repository"]
    number = args["number"]
    if name == "pr":
        return pull_request_state(number, repository=repository)
    if name == "issue":
        return issue_state(number, repository=repository)
    if name == "reviews":
        return review_state(number, repository=repository)
    if name == "checks":
        return check_state(number, repository=repository)
    if name == "workflows":
        return workflow_state(number, repository=repository)
    if name == "prs":
        return issue_pull_requests_state(number, repository=repository)
    if name == "context":
        return context_state(pr=number, repository=repository, include_impact=False)
    raise ValueError("tool_not_found")


def _error(request_id, code, message):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def respond(request):
    """Handle one JSON-RPC request without leaking upstream failures."""
    if type(request) is not dict or request.get("jsonrpc") != "2.0":
        return _error(None, -32600, "Invalid Request")
    request_id = request.get("id")
    if "id" not in request:
        return None
    method = request.get("method")
    params = request.get("params", {})
    if type(params) is not dict:
        return _error(request_id, -32602, "Invalid params")
    if method == "initialize":
        if params.get("protocolVersion") != PROTOCOL:
            return _error(request_id, -32602, "Unsupported protocol")
        result = {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "gway-repo", "version": "0.1.0"},
        }
    elif method == "tools/list":
        if params:
            return _error(request_id, -32602, "Invalid params")
        result = {"tools": TOOLS}
    elif method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments")
        if name not in {tool["name"] for tool in TOOLS}:
            return _error(request_id, -32601, "Unknown tool")
        try:
            data = _invoke(name, arguments)
            payload = json.dumps(data, sort_keys=True, allow_nan=False)
            if len(payload.encode("utf-8")) > MAX_LINE // 2:
                raise ValueError("oversized_reply")
        except ValueError:
            return _error(request_id, -32602, "Invalid arguments or result")
        except (RuntimeError, OSError, TypeError, LookupError):
            return _error(request_id, -32000, "Repository operation unavailable")
        result = {"content": [{"type": "text", "text": payload}]}
    else:
        return _error(request_id, -32601, "Method not found")
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def serve(stdin=sys.stdin, stdout=sys.stdout):
    for line in stdin:
        if len(line.encode("utf-8")) > MAX_LINE:
            continue
        try:
            response = respond(json.loads(line))
            if response is None:
                continue
            encoded = json.dumps(response, separators=(",", ":"), allow_nan=False)
            if len(encoded.encode("utf-8")) > MAX_LINE:
                continue
            stdout.write(encoded + "\n")
            stdout.flush()
        except (UnicodeError, ValueError, TypeError):
            continue


def main():
    serve()


if __name__ == "__main__":
    main()
