"""Bounded, read-only MCP views of explicitly admitted local Git workspaces.

The caller selects a repository identity, never a path, checkout, ref or URL.
No cloning, checkout, git fetch or installation takes place during tool calls.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from pathlib import Path

from . import commands
from .discovery import github_repository

_MANIFEST = Path("/etc/gway-mcp-providers/repo.json")
_SELF_ROOT = Path("/home/arthe/.local/share/gway-repo/checkouts")
_FIXED = {
    "arthexis/gway-remote": Path(
        "/home/arthe/actions-runner/_work/gway-remote/gway-remote"
    ),
    "arthexis/ocpp-csms": Path("/home/arthe/actions-runner/_work/ocpp-csms/ocpp-csms"),
}
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_FILE = re.compile(r"[A-Za-z0-9_.@+ -]+(?:/[A-Za-z0-9_.@+ -]+)*\Z")
_SYMBOL = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,159}\Z")
_MAX_OUTPUT = 96 * 1024

# Strictly bounded independent knobs. Boolean and float values never count as ints.
_LIMITS = {
    "file_limit": (1, 100),
    "limit": (1, 100),
    "import_limit": (0, 100),
    "error_limit": (0, 30),
    "edge_limit": (1, 200),
    "node_limit": (1, 200),
    "context": (0, 10),
    "max_lines": (1, 160),
    "max_chars": (1, 12_000),
    "start_line": (1, 1_000_000),
    "end_line": (1, 1_000_000),
    "pr": (1, 10_000_000),
    "issue": (1, 10_000_000),
    "depth": (0, 3),
}
_FIELDS = {
    "map": {"file_limit"},
    "files": {"kind", "extension", "package", "limit"},
    "symbols": {
        "file",
        "kind",
        "name",
        "package",
        "limit",
        "import_limit",
        "error_limit",
        "include_imports",
    },
    "relations": {
        "file",
        "symbol",
        "kind",
        "edge_limit",
        "node_limit",
        "error_limit",
    },
    "impact": {"file", "pr", "issue", "depth"},
    "code": {
        "file",
        "symbol",
        "start_line",
        "end_line",
        "context",
        "max_lines",
        "max_chars",
    },
}
_DEFAULTS = {
    "map": {"file_limit": 100},
    "files": {"limit": 100},
    "symbols": {"limit": 100, "import_limit": 60, "error_limit": 20},
    "relations": {"edge_limit": 150, "node_limit": 150, "error_limit": 20},
    "impact": {
        "depth": 2,
        "pr_limit": 5,
        "event_limit": 100,
        "file_limit": 50,
        "changed_file_limit": 70,
        "symbol_limit": 80,
        "dependent_limit": 80,
        "test_limit": 50,
        "error_limit": 20,
        "visit_limit": 800,
    },
    "code": {"context": 2, "max_lines": 160, "max_chars": 12_000},
}


def _path_guard(path: Path) -> None:
    """Deny symlinked ancestors or a group/other-writable workspace root."""
    if not path.is_absolute():
        raise ValueError("invalid_workspace")
    for component in (path, *path.parents):
        if component.is_symlink():
            raise ValueError("unsafe_workspace")
    info = path.stat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid not in (0, os.geteuid())
        or info.st_mode & 0o022
    ):
        raise ValueError("unsafe_workspace")


def _self_checkout() -> Path:
    info = _MANIFEST.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or info.st_mode & 0o022
        or _MANIFEST.is_symlink()
    ):
        raise ValueError("untrusted_provider_manifest")
    data = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("source_id") != "repo":
        raise ValueError("invalid_provider_manifest")
    revision = data.get("revision")
    if type(revision) is not str or not _SHA.fullmatch(revision):
        raise ValueError("invalid_provider_revision")
    checkout = _SELF_ROOT / revision
    if data.get("deployment", {}).get("checkout") != str(checkout):
        raise ValueError("provider_checkout_mismatch")
    return checkout


def _root_for(repository: str) -> Path:
    if repository == "arthexis/gway-repo":
        return _self_checkout()
    if repository not in _FIXED:
        raise ValueError("repository_not_admitted")
    return _FIXED[repository]


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        timeout=8,
        check=False,
    )
    if result.returncode:
        raise ValueError("unavailable_workspace")
    return result.stdout.strip()


def _workspace(repository: str) -> tuple[Path, str]:
    if type(repository) is not str:
        raise ValueError("repository_not_admitted")
    root = _root_for(repository)
    _path_guard(root)
    # A checkout can be replaced by an external runner; revalidate every call.
    if Path(_git(root, "rev-parse", "--show-toplevel")) != root:
        raise ValueError("workspace_root_mismatch")
    remote = _git(root, "config", "--get", "remote.origin.url")
    if github_repository(remote) != repository:
        raise ValueError("workspace_identity_mismatch")
    head = _git(root, "rev-parse", "--verify", "HEAD")
    if not _SHA.fullmatch(head):
        raise ValueError("workspace_revision_invalid")
    if repository == "arthexis/gway-repo" and root.name != head:
        raise ValueError("provider_revision_mismatch")
    return root, head


def _arguments(name: str, args: object) -> tuple[str, dict[str, object]]:
    if name not in _FIELDS or type(args) is not dict:
        raise ValueError("invalid_arguments")
    if set(args) - (_FIELDS[name] | {"repository"}):
        raise ValueError("invalid_arguments")
    repository = args.get("repository")
    if type(repository) is not str or repository not in ("arthexis/gway-repo", *_FIXED):
        raise ValueError("repository_not_admitted")
    values = {key: val for key, val in args.items() if key != "repository"}
    for key, val in values.items():
        if key in _LIMITS:
            lower, upper = _LIMITS[key]
            if type(val) is not int or not lower <= val <= upper:
                raise ValueError("invalid_limit")
        elif key == "include_imports":
            if type(val) is not bool:
                raise ValueError("invalid_boolean")
        elif type(val) is not str or not val or len(val) > 160:
            raise ValueError("invalid_filter")
        if key == "file" and (
            not _FILE.fullmatch(val)
            or any(component in (".", "..") for component in val.split("/"))
            or val.startswith("/")
        ):
            raise ValueError("unsafe_file")
        if key == "symbol" and not _SYMBOL.fullmatch(val):
            raise ValueError("invalid_symbol")
        if isinstance(val, str) and any(ord(c) < 32 for c in val):
            raise ValueError("invalid_filter")
    if name == "impact" and sum(key in values for key in ("file", "pr", "issue")) != 1:
        raise ValueError("select_exactly_one_impact")
    if name == "code" and not ({"file", "symbol"} & set(values)):
        raise ValueError("missing_source_selector")
    if (
        name == "code"
        and "symbol" in values
        and ("start_line" in values or "end_line" in values)
    ):
        raise ValueError("conflicting_source_selector")
    if name == "code" and (
        values.get("start_line", 1) > values.get("end_line", 1_000_000)
    ):
        raise ValueError("invalid_line_range")
    return repository, values


def _redact(value: object, root: Path) -> object:
    """Keep analysis evidence but never emit host workspace paths."""
    if isinstance(value, dict):
        return {str(k): _redact(v, root) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, root) for v in value]
    if isinstance(value, str):
        return value.replace(str(root), "[approved-workspace]")
    return value


def invoke(name: str, args: object) -> dict[str, object]:
    repository, values = _arguments(name, args)
    root, head = _workspace(repository)
    selected = getattr(commands, name)
    options = {**_DEFAULTS[name], **values, "path": str(root), "refresh": False}
    if name == "impact":
        options["repository"] = repository
    data = selected(**options)
    if type(data) is not dict:
        raise ValueError("invalid_repository_result")
    cleaned = _redact(data, root)
    answer = {
        **cleaned,
        "workspace": {
            "repository": repository,
            "head": head,
            "scope": "local_git_index",
        },
    }
    encoded = json.dumps(answer, allow_nan=False, sort_keys=True)
    if len(encoded.encode("utf-8")) > _MAX_OUTPUT:
        raise ValueError("repository_result_too_large")
    return answer
