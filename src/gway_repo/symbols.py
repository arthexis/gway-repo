"""Python symbol indexing for Git-tracked repository blobs."""

from __future__ import annotations

import ast
import json
import sqlite3
import subprocess
from pathlib import Path

from .discovery import find_root
from .mapping import _cache_path, _full_map, _index_entries

_SYMBOL_CACHE_VERSION = 1


def _blob_bytes(root: Path, object_id: str) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(root), "cat-file", "blob", object_id],
        check=False,
        capture_output=True,
    )
    if result.returncode:
        message = (
            result.stderr.decode(errors="replace").strip()
            or result.stdout.decode(errors="replace").strip()
            or "git cat-file failed"
        )
        raise ValueError(message)
    return result.stdout


def _cache_get(root: Path, object_id: str) -> dict[str, object] | None:
    path = _cache_path(root)
    if not path.exists():
        return None
    key = f"v{_SYMBOL_CACHE_VERSION}:{object_id}"
    try:
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS symbol_blobs "
                "(cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            row = connection.execute(
                "SELECT payload FROM symbol_blobs WHERE cache_key = ?",
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


def _cache_put(root: Path, object_id: str, payload: dict[str, object]) -> None:
    path = _cache_path(root)
    key = f"v{_SYMBOL_CACHE_VERSION}:{object_id}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS symbol_blobs "
                "(cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT OR REPLACE INTO symbol_blobs(cache_key, payload) "
                "VALUES (?, ?)",
                (key, json.dumps(payload, sort_keys=True, separators=(",", ":"))),
            )
    except (OSError, sqlite3.Error):
        return


def _unparse(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    return ast.unparse(node)


class _SymbolVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.symbols: list[dict[str, object]] = []
        self.imports: list[dict[str, object]] = []
        self._scope: list[tuple[str, str]] = []

    def _scope_names(self) -> list[str]:
        return [name for _, name in self._scope]

    def _record_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        is_async: bool,
    ) -> None:
        parent_kind = self._scope[-1][0] if self._scope else None
        kind = "method" if parent_kind == "class" else "function"
        self.symbols.append(
            {
                "kind": kind,
                "name": node.name,
                "scope": self._scope_names(),
                "line": node.lineno,
                "end_line": getattr(node, "end_lineno", node.lineno),
                "signature": f"({ast.unparse(node.args)})",
                "returns": _unparse(node.returns),
                "decorators": [ast.unparse(item) for item in node.decorator_list],
                "async": is_async,
            }
        )
        self._scope.append(("function", node.name))
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record_function(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record_function(node, is_async=True)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        bases = [ast.unparse(base) for base in node.bases]
        for item in node.keywords:
            if item.arg is None:
                bases.append(f"**{ast.unparse(item.value)}")
            else:
                bases.append(f"{item.arg}={ast.unparse(item.value)}")
        self.symbols.append(
            {
                "kind": "class",
                "name": node.name,
                "scope": self._scope_names(),
                "line": node.lineno,
                "end_line": getattr(node, "end_lineno", node.lineno),
                "bases": bases,
                "decorators": [ast.unparse(item) for item in node.decorator_list],
            }
        )
        self._scope.append(("class", node.name))
        self.generic_visit(node)
        self._scope.pop()

    def visit_Import(self, node: ast.Import) -> None:
        scope = self._scope_names()
        for item in node.names:
            self.imports.append(
                {
                    "kind": "import",
                    "module": item.name,
                    "name": None,
                    "alias": item.asname,
                    "level": 0,
                    "scope": scope,
                    "line": node.lineno,
                }
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        scope = self._scope_names()
        for item in node.names:
            self.imports.append(
                {
                    "kind": "from",
                    "module": node.module,
                    "name": item.name,
                    "alias": item.asname,
                    "level": node.level,
                    "scope": scope,
                    "line": node.lineno,
                }
            )


def _parse_blob(root: Path, object_id: str) -> dict[str, object]:
    try:
        tree = ast.parse(_blob_bytes(root, object_id))
    except (SyntaxError, ValueError) as exc:
        return {
            "symbols": [],
            "imports": [],
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
                "line": getattr(exc, "lineno", None),
                "offset": getattr(exc, "offset", None),
            },
        }

    visitor = _SymbolVisitor()
    visitor.visit(tree)
    return {
        "symbols": visitor.symbols,
        "imports": visitor.imports,
        "error": None,
    }


def _blob_index(
    root: Path,
    object_id: str,
    *,
    refresh: bool,
) -> tuple[dict[str, object], bool]:
    if not refresh:
        cached = _cache_get(root, object_id)
        if cached is not None:
            return cached, True

    payload = _parse_blob(root, object_id)
    _cache_put(root, object_id, payload)
    return payload, False


def _qualified_name(
    module: str | None,
    scope: list[str],
    name: str,
) -> str:
    parts = [*scope, name]
    if module:
        parts.insert(0, module)
    return ".".join(parts)


def _decorate_symbol(
    raw: dict[str, object],
    *,
    path: str,
    module: str | None,
    package: str | None,
) -> dict[str, object]:
    scope = raw.get("scope")
    scope_names = [str(item) for item in scope] if isinstance(scope, list) else []
    result = dict(raw)
    result["path"] = path
    result["module"] = module
    result["package"] = package
    result["qualified_name"] = _qualified_name(
        module,
        scope_names,
        str(raw["name"]),
    )
    return result


def _decorate_import(
    raw: dict[str, object],
    *,
    path: str,
    module: str | None,
    package: str | None,
) -> dict[str, object]:
    result = dict(raw)
    result["path"] = path
    result["source_module"] = module
    result["package"] = package
    return result


def symbols_state(
    path: str | Path = ".",
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
    """Return a bounded AST symbol/import index for Git-tracked Python blobs."""
    if limit < 0:
        raise ValueError("limit must be non-negative")
    if import_limit < 0:
        raise ValueError("import_limit must be non-negative")
    if error_limit < 0:
        raise ValueError("error_limit must be non-negative")
    if kind is not None and kind not in {"class", "function", "method"}:
        raise ValueError("kind must be class, function, or method")

    root = find_root(path)
    mapped = _full_map(root, refresh=refresh)
    raw_files = mapped.get("files")
    if not isinstance(raw_files, list):
        raise TypeError("repository map has an invalid files section")
    object_ids = {
        str(item["path"]): str(item["object_id"])
        for item in _index_entries(root)
        if isinstance(item.get("path"), str) and isinstance(item.get("object_id"), str)
    }

    normalized_file = file.replace("\\", "/") if file is not None else None
    candidates: list[dict[str, object]] = []
    for item in raw_files:
        if not isinstance(item, dict):
            continue
        if item.get("extension") != ".py":
            continue
        if normalized_file is not None and item.get("path") != normalized_file:
            continue
        if package is not None and item.get("package") != package:
            continue
        candidates.append(item)

    symbols: list[dict[str, object]] = []
    imports: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    cache_hits = 0

    for item in candidates:
        file_path = item.get("path")
        object_id = object_ids.get(str(file_path))
        if not isinstance(file_path, str) or object_id is None:
            errors.append(
                {
                    "path": str(file_path),
                    "type": "MissingObjectId",
                    "message": "Git index is missing the mapped Python blob",
                    "line": None,
                    "offset": None,
                }
            )
            continue

        indexed, cache_hit = _blob_index(root, object_id, refresh=refresh)
        cache_hits += int(cache_hit)
        module = item.get("module")
        module_name = str(module) if isinstance(module, str) else None
        package_value = item.get("package")
        package_name = str(package_value) if isinstance(package_value, str) else None

        error = indexed.get("error")
        if isinstance(error, dict):
            errors.append({"path": file_path, **error})
            continue

        raw_symbols = indexed.get("symbols")
        if isinstance(raw_symbols, list):
            for raw in raw_symbols:
                if not isinstance(raw, dict) or "name" not in raw:
                    continue
                symbol = _decorate_symbol(
                    raw,
                    path=file_path,
                    module=module_name,
                    package=package_name,
                )
                if kind is not None and symbol.get("kind") != kind:
                    continue
                if name is not None and name not in {
                    symbol.get("name"),
                    symbol.get("qualified_name"),
                }:
                    continue
                symbols.append(symbol)

        if include_imports:
            raw_imports = indexed.get("imports")
            if isinstance(raw_imports, list):
                for raw in raw_imports:
                    if not isinstance(raw, dict):
                        continue
                    imports.append(
                        _decorate_import(
                            raw,
                            path=file_path,
                            module=module_name,
                            package=package_name,
                        )
                    )

    symbols.sort(
        key=lambda item: (
            str(item.get("path")),
            int(item.get("line", 0)),
            str(item.get("qualified_name")),
        )
    )
    imports.sort(
        key=lambda item: (
            str(item.get("path")),
            int(item.get("line", 0)),
            str(item.get("module")),
            str(item.get("name")),
        )
    )
    errors.sort(key=lambda item: str(item.get("path")))

    return {
        "kind": "symbols",
        "repository": mapped.get("repository"),
        "root": mapped.get("root"),
        "head_sha": mapped.get("head_sha"),
        "tree_sha": mapped.get("tree_sha"),
        "filters": {
            "file": normalized_file,
            "kind": kind,
            "name": name,
            "package": package,
        },
        "summary": {
            "python_files": len(candidates),
            "cache_hits": cache_hits,
            "symbols": len(symbols),
            "imports": len(imports) if include_imports else 0,
            "parse_errors": len(errors),
        },
        "symbols": symbols[:limit],
        "symbols_truncated": len(symbols) > limit,
        "imports": imports[:import_limit] if include_imports else [],
        "imports_truncated": include_imports and len(imports) > import_limit,
        "parse_errors": errors[:error_limit],
        "parse_errors_truncated": len(errors) > error_limit,
    }
