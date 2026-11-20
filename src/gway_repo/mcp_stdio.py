"""Node-owned, bounded MCP stdio adapter for repository intelligence.

No HTTP, SSH, arbitrary checkout paths or caller-supplied credentials.
Transport authorization and release registration belong to GWay Remote.
"""
import json
import re
import sys

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


def _invoke(name, args):
    if (type(args) is not dict or set(args) != {"repository", "number"}
            or type(args["number"]) is not int or args["number"] < 1
            or type(args["repository"]) is not str
            or not _REPO.fullmatch(args["repository"])):
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
        return context_state(
            pr=number, repository=repository, include_impact=False
        )
    raise ValueError("tool_not_found")


def _error(request_id, code, message):
    return {
        "jsonrpc": "2.0", "id": request_id,
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
