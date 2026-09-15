"""Deterministic structural repository mapping backed by Git."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path, PurePosixPath

from .discovery import find_root, repository_info

_CACHE_VERSION = 1
_CONFIG_NAMES = {
    ".editorconfig",
    ".gitignore",
    ".pre-commit-config.yaml",
    "dockerfile",
    "makefile",
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    "tox.ini",
}
_CONFIG_SUFFIXES = {".cfg", ".ini", ".json", ".toml", ".yaml", ".yml"}
_DOC_NAMES = {"changelog", "contributing", "license", "readme"}
_DOC_SUFFIXES = {".adoc", ".md", ".rst"}
_SCRIPT_SUFFIXES = {".bat", ".cmd", ".ps1", ".sh"}
_ASSET_SUFFIXES = {
    ".bmp",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".svg",
    ".ttf",
    ".webp",
    ".woff",
    ".woff2",
}
_GENERATED_NAMES = {
    "package-lock.json",
    "pipfile.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "uv.lock",
    "yarn.lock",
}


def _run_git(root: Path, *args: str, input_text: str | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
        input=input_text,
        errors="surrogateescape",
    )
    if result.returncode:
        message = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise ValueError(message)
    return result.stdout


def _git_dir(root: Path) -> Path:
    value = _run_git(root, "rev-parse", "--git-dir").strip()
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _cache_path(root: Path) -> Path:
    return _git_dir(root) / "gway-repo" / "map.sqlite3"


def _cache_get(root: Path, key: str) -> dict[str, object] | None:
    path = _cache_path(root)
    if not path.exists():
        return None
    try:
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS maps "
                "(cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            row = connection.execute(
                "SELECT payload FROM maps WHERE cache_key = ?",
                (key,),
            ).fetchone()
    except (OSError, sqlite3.Error):
        return None
    if row is None:
        return None
    try:
        payload = json.loads(row[0])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _cache_put(root: Path, key: str, payload: dict[str, object]) -> None:
    path = _cache_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS maps "
                "(cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT OR REPLACE INTO maps(cache_key, payload) VALUES (?, ?)",
                (key, json.dumps(payload, sort_keys=True, separators=(",", ":"))),
            )
    except (OSError, sqlite3.Error):
        return


def _index_entries(root: Path) -> list[dict[str, object]]:
    output = _run_git(root, "ls-files", "-s", "-z")
    selected: dict[str, tuple[int, str, str]] = {}
    for record in output.split("\0"):
        if not record:
            continue
        metadata, path = record.split("\t", 1)
        mode, object_id, stage_text = metadata.split()
        stage = int(stage_text)
        current = selected.get(path)
        if current is None or stage == 0 or (current[0] != 0 and stage == 2):
            selected[path] = (stage, mode, object_id)

    blob_ids = sorted(
        {object_id for _, mode, object_id in selected.values() if mode != "160000"}
    )
    sizes: dict[str, int] = {}
    if blob_ids:
        size_output = _run_git(
            root,
            "cat-file",
            "--batch-check=%(objectname) %(objecttype) %(objectsize)",
            input_text="\n".join(blob_ids) + "\n",
        )
        for line in size_output.splitlines():
            object_id, object_type, size_text = line.split()
            if object_type == "blob":
                sizes[object_id] = int(size_text)

    entries: list[dict[str, object]] = []
    for path in sorted(selected):
        _, mode, object_id = selected[path]
        entries.append(
            {
                "path": path,
                "mode": mode,
                "object_id": object_id,
                "size": sizes.get(object_id),
            }
        )
    return entries


def _is_generated(path: PurePosixPath) -> bool:
    name = path.name.lower()
    parts = {part.lower() for part in path.parts}
    return (
        name in _GENERATED_NAMES
        or ".generated." in name
        or name.endswith((".min.css", ".min.js"))
        or "generated" in parts
    )


def _kind(path: PurePosixPath) -> str:
    name = path.name.lower()
    suffix = path.suffix.lower()
    parts = [part.lower() for part in path.parts]

    if len(parts) >= 3 and parts[0:2] == [".github", "workflows"]:
        return "workflow"
    if (
        "tests" in parts
        or "test" in parts[:-1]
        or name.startswith("test_")
        or name.endswith("_test.py")
    ):
        return "test"
    if "migrations" in parts or "versions" in parts:
        return "migration"
    if "docs" in parts or suffix in _DOC_SUFFIXES or path.stem.lower() in _DOC_NAMES:
        return "documentation"
    if parts[0] in {"bin", "scripts"} or suffix in _SCRIPT_SUFFIXES:
        return "script"
    if name in _CONFIG_NAMES or suffix in _CONFIG_SUFFIXES:
        return "config"
    if "assets" in parts or "static" in parts or suffix in _ASSET_SUFFIXES:
        return "asset"
    if suffix == ".py":
        return "python"
    return "other"


def _package_roots(paths: set[str]) -> list[dict[str, str]]:
    packages: list[dict[str, str]] = []
    for path_text in sorted(paths):
        path = PurePosixPath(path_text)
        if path.name != "__init__.py":
            continue
        parent = path.parent
        parts = parent.parts
        if len(parts) == 2 and parts[0] == "src":
            packages.append({"name": parts[1], "path": str(parent), "layout": "src"})
        elif len(parts) == 1 and parts[0] not in {
            "docs",
            "scripts",
            "test",
            "tests",
        }:
            packages.append({"name": parts[0], "path": str(parent), "layout": "flat"})
    return packages


def _module_for(
    path: PurePosixPath,
    packages: list[dict[str, str]],
) -> tuple[str | None, str | None]:
    if path.suffix.lower() != ".py":
        return None, None
    for package in packages:
        package_path = PurePosixPath(package["path"])
        try:
            relative = path.relative_to(package_path)
        except ValueError:
            continue
        parts = list(relative.parts)
        filename = parts.pop()
        stem = PurePosixPath(filename).stem
        if stem != "__init__":
            parts.append(stem)
        module_parts = [package["name"], *parts]
        return ".".join(module_parts), package["name"]
    return None, None


def _likely_sources(files: list[dict[str, object]]) -> None:
    candidates: dict[str, list[str]] = {}
    for item in files:
        if item.get("kind") != "python":
            continue
        path = PurePosixPath(str(item["path"]))
        candidates.setdefault(path.stem, []).append(str(path))

    for item in files:
        if item.get("kind") != "test":
            continue
        path = PurePosixPath(str(item["path"]))
        stem = path.stem
        if stem.startswith("test_"):
            source_stem = stem[5:]
        elif stem.endswith("_test"):
            source_stem = stem[:-5]
        else:
            continue
        matches = candidates.get(source_stem, [])
        if len(matches) == 1:
            item["likely_source"] = matches[0]


def _roots(
    files: list[dict[str, object]],
    packages: list[dict[str, str]],
) -> dict[str, list[str]]:
    test_roots: set[str] = set()
    doc_roots: set[str] = set()
    workflow_roots: set[str] = set()

    for item in files:
        path = PurePosixPath(str(item["path"]))
        if item.get("kind") == "test" and path.parts:
            test_roots.add(path.parts[0])
        if "docs" in path.parts:
            index = path.parts.index("docs") + 1
            doc_roots.add(str(PurePosixPath(*path.parts[:index])))
        if item.get("kind") == "workflow":
            workflow_roots.add(".github/workflows")

    return {
        "packages": [package["path"] for package in packages],
        "tests": sorted(test_roots),
        "documentation": sorted(doc_roots),
        "workflows": sorted(workflow_roots),
    }


def _build_map(root: Path, head_sha: str, tree_sha: str) -> dict[str, object]:
    raw_entries = _index_entries(root)
    paths = {str(item["path"]) for item in raw_entries}
    packages = _package_roots(paths)

    files: list[dict[str, object]] = []
    for raw in raw_entries:
        path = PurePosixPath(str(raw["path"]))
        module, package = _module_for(path, packages)
        item: dict[str, object] = {
            "path": str(path),
            "kind": _kind(path),
            "extension": path.suffix.lower() or None,
            "size": raw["size"],
            "mode": raw["mode"],
            "generated": _is_generated(path),
        }
        if module is not None:
            item["module"] = module
        if package is not None:
            item["package"] = package
        files.append(item)

    _likely_sources(files)
    counts: dict[str, int] = {}
    for item in files:
        kind = str(item["kind"])
        counts[kind] = counts.get(kind, 0) + 1

    return {
        "kind": "repository_map",
        "root": str(root),
        "repository": repository_info(root).get("repository"),
        "head_sha": head_sha,
        "tree_sha": tree_sha,
        "summary": {
            "tracked_files": len(files),
            "total_bytes": sum(
                int(item["size"]) for item in files if isinstance(item["size"], int)
            ),
            "python_files": counts.get("python", 0) + counts.get("test", 0),
            "tests": counts.get("test", 0),
            "workflows": counts.get("workflow", 0),
            "configs": counts.get("config", 0),
            "documentation": counts.get("documentation", 0),
            "generated_files": sum(bool(item["generated"]) for item in files),
            "packages": [package["name"] for package in packages],
            "kinds": counts,
        },
        "roots": _roots(files, packages),
        "packages": packages,
        "files": files,
    }


def _full_map(path: str | Path = ".", refresh: bool = False) -> dict[str, object]:
    root = find_root(path)
    head_sha = _run_git(root, "rev-parse", "HEAD").strip()
    tree_sha = _run_git(root, "write-tree").strip()
    key = f"v{_CACHE_VERSION}:{head_sha}:{tree_sha}"

    if not refresh:
        cached = _cache_get(root, key)
        if cached is not None:
            return cached

    payload = _build_map(root, head_sha, tree_sha)
    _cache_put(root, key, payload)
    return payload


def repository_map(
    path: str | Path = ".",
    refresh: bool = False,
    file_limit: int = 500,
) -> dict[str, object]:
    """Return a bounded deterministic map of Git-tracked repository structure."""
    if file_limit < 0:
        raise ValueError("file_limit must be non-negative")
    payload = _full_map(path, refresh=refresh)
    files = payload.get("files")
    if not isinstance(files, list):
        raise TypeError("cached repository map has an invalid files section")

    result = dict(payload)
    result["files"] = files[:file_limit]
    result["files_truncated"] = len(files) > file_limit
    return result


def files_state(
    path: str | Path = ".",
    kind: str | None = None,
    extension: str | None = None,
    package: str | None = None,
    limit: int = 200,
    refresh: bool = False,
) -> dict[str, object]:
    """Return a bounded filtered view of files from the structural repository map."""
    if limit < 0:
        raise ValueError("limit must be non-negative")

    payload = _full_map(path, refresh=refresh)
    raw_files = payload.get("files")
    if not isinstance(raw_files, list):
        raise TypeError("cached repository map has an invalid files section")

    normalized_extension = extension
    if normalized_extension and not normalized_extension.startswith("."):
        normalized_extension = f".{normalized_extension}"
    if normalized_extension:
        normalized_extension = normalized_extension.lower()

    selected: list[dict[str, object]] = []
    for item in raw_files:
        if not isinstance(item, dict):
            continue
        if kind is not None and item.get("kind") != kind:
            continue
        if (
            normalized_extension is not None
            and item.get("extension") != normalized_extension
        ):
            continue
        if package is not None and item.get("package") != package:
            continue
        selected.append(item)

    return {
        "kind": "files",
        "repository": payload.get("repository"),
        "root": payload.get("root"),
        "head_sha": payload.get("head_sha"),
        "tree_sha": payload.get("tree_sha"),
        "filters": {
            "kind": kind,
            "extension": normalized_extension,
            "package": package,
        },
        "total_count": len(selected),
        "files": selected[:limit],
        "truncated": len(selected) > limit,
    }
