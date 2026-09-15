"""Conservative relationship analysis for Git-indexed Python code."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path, PurePosixPath

from .discovery import find_root
from .mapping import _cache_path, _full_map, _index_entries
from .symbols import _blob_index, _qualified_name

_RELATION_CACHE_VERSION = 1
_RELATION_KINDS = {"defines", "import", "call", "inheritance", "tested_by"}


def _graph_cache_get(root: Path, tree_sha: str) -> dict[str, object] | None:
    path = _cache_path(root)
    if not path.exists():
        return None
    key = f"v{_RELATION_CACHE_VERSION}:{tree_sha}"
    try:
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS relation_graphs "
                "(cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            row = connection.execute(
                "SELECT payload FROM relation_graphs WHERE cache_key = ?",
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


def _graph_cache_put(root: Path, tree_sha: str, payload: dict[str, object]) -> None:
    path = _cache_path(root)
    key = f"v{_RELATION_CACHE_VERSION}:{tree_sha}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS relation_graphs "
                "(cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT OR REPLACE INTO relation_graphs(cache_key, payload) VALUES (?, ?)",
                (key, json.dumps(payload, sort_keys=True, separators=(",", ":"))),
            )
    except (OSError, sqlite3.Error):
        return


def _symbol_id(
    module: str | None,
    path: str,
    scope: list[str],
    name: str,
) -> str:
    if module:
        return _qualified_name(module, scope, name)
    local = ".".join([*scope, name])
    return f"file:{path}::{local}"


def _scope_owner(
    module: str | None,
    path: str,
    scope: list[str],
    symbol_ids: set[str],
) -> str:
    if scope:
        for length in range(len(scope), 0, -1):
            candidate = _symbol_id(module, path, scope[: length - 1], scope[length - 1])
            if candidate in symbol_ids:
                return candidate
    return module or f"file:{path}"


def _absolute_import(
    source_module: str | None,
    source_path: str,
    module: str | None,
    level: int,
) -> str | None:
    if level == 0:
        return module
    if source_module is None:
        return None

    parts = source_module.split(".")
    if PurePosixPath(source_path).name != "__init__.py":
        parts = parts[:-1]

    ascend = level - 1
    if ascend > len(parts):
        return None
    if ascend:
        parts = parts[:-ascend]
    if module:
        parts.extend(module.split("."))
    return ".".join(parts) or None


def _scope_names(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw]


def _binding_for(
    name: str,
    scope: list[str],
    bindings: list[tuple[tuple[str, ...], str, str]],
) -> str | None:
    matches: list[tuple[int, str]] = []
    for binding_scope, binding_name, target in bindings:
        if binding_name != name:
            continue
        if len(binding_scope) > len(scope):
            continue
        if tuple(scope[: len(binding_scope)]) != binding_scope:
            continue
        matches.append((len(binding_scope), target))
    if not matches:
        return None
    matches.sort(key=lambda item: item[0], reverse=True)
    return matches[0][1]


def _resolve_reference(
    expression: str,
    *,
    source_module: str | None,
    source_path: str,
    scope: list[str],
    bindings: list[tuple[tuple[str, ...], str, str]],
    symbol_ids: set[str],
    module_ids: set[str],
) -> tuple[str | None, str | None]:
    if expression in symbol_ids or expression in module_ids:
        return expression, "qualified"

    parts = expression.split(".")
    binding = _binding_for(parts[0], scope, bindings)
    if binding is not None:
        candidate = ".".join([binding, *parts[1:]])
        if candidate in symbol_ids or candidate in module_ids:
            return candidate, "import"

    if "." not in expression:
        if source_module:
            for length in range(len(scope), -1, -1):
                candidate = ".".join([source_module, *scope[:length], expression])
                if candidate in symbol_ids:
                    return candidate, "lexical"
        else:
            for length in range(len(scope), -1, -1):
                candidate = _symbol_id(None, source_path, scope[:length], expression)
                if candidate in symbol_ids:
                    return candidate, "lexical"
    return None, None


def _node_path(node_id: str, nodes: dict[str, dict[str, object]]) -> str | None:
    node = nodes.get(node_id)
    if node is None:
        return None
    value = node.get("path")
    return value if isinstance(value, str) else None


def _add_edge(
    edges: list[dict[str, object]],
    nodes: dict[str, dict[str, object]],
    *,
    kind: str,
    source: str,
    target: str | None,
    line: int | None = None,
    expression: str | None = None,
    resolution: str | None = None,
    confidence: str | None = None,
    evidence: str | None = None,
    resolved: bool | None = None,
) -> None:
    edge: dict[str, object] = {
        "kind": kind,
        "from": source,
        "to": target,
        "source_path": _node_path(source, nodes),
        "target_path": _node_path(target, nodes) if target is not None else None,
        "resolved": target is not None if resolved is None else resolved,
    }
    if line is not None:
        edge["line"] = line
    if expression is not None:
        edge["expression"] = expression
    if resolution is not None:
        edge["resolution"] = resolution
    if confidence is not None:
        edge["confidence"] = confidence
    if evidence is not None:
        edge["evidence"] = evidence
    edges.append(edge)


def _build_graph(
    root: Path,
    mapped: dict[str, object],
    *,
    refresh: bool,
) -> dict[str, object]:
    raw_files = mapped.get("files")
    if not isinstance(raw_files, list):
        raise TypeError("repository map has an invalid files section")

    object_ids = {
        str(item["path"]): str(item["object_id"])
        for item in _index_entries(root)
        if isinstance(item.get("path"), str) and isinstance(item.get("object_id"), str)
    }
    files = {
        str(item["path"]): item
        for item in raw_files
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    python_files = [item for item in files.values() if item.get("extension") == ".py"]

    nodes: dict[str, dict[str, object]] = {}
    module_paths: dict[str, str] = {}
    file_payloads: dict[str, dict[str, object]] = {}
    parse_errors: list[dict[str, object]] = []
    symbol_ids: set[str] = set()
    symbol_kinds: dict[str, str] = {}

    for item in python_files:
        file_path = str(item["path"])
        file_id = f"file:{file_path}"
        nodes[file_id] = {
            "id": file_id,
            "kind": "file",
            "path": file_path,
            "test": item.get("kind") == "test",
        }
        module = item.get("module")
        module_name = module if isinstance(module, str) else None
        if module_name:
            module_paths[module_name] = file_path
            nodes[module_name] = {
                "id": module_name,
                "kind": "module",
                "path": file_path,
                "module": module_name,
                "test": item.get("kind") == "test",
            }

        object_id = object_ids.get(file_path)
        if object_id is None:
            parse_errors.append(
                {
                    "path": file_path,
                    "type": "MissingObjectId",
                    "message": "Git index is missing the mapped Python blob",
                    "line": None,
                    "offset": None,
                }
            )
            continue
        indexed, _ = _blob_index(root, object_id, refresh=refresh)
        file_payloads[file_path] = indexed
        error = indexed.get("error")
        if isinstance(error, dict):
            parse_errors.append({"path": file_path, **error})
            continue

        raw_symbols = indexed.get("symbols")
        if not isinstance(raw_symbols, list):
            continue
        for raw in raw_symbols:
            if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
                continue
            scope = _scope_names(raw.get("scope"))
            symbol_id = _symbol_id(module_name, file_path, scope, str(raw["name"]))
            symbol_ids.add(symbol_id)
            symbol_kind = str(raw.get("kind", "symbol"))
            symbol_kinds[symbol_id] = symbol_kind
            nodes[symbol_id] = {
                "id": symbol_id,
                "kind": symbol_kind,
                "name": raw["name"],
                "path": file_path,
                "module": module_name,
                "line": raw.get("line"),
                "end_line": raw.get("end_line"),
                "test": item.get("kind") == "test",
            }

    module_ids = set(module_paths)
    edges: list[dict[str, object]] = []

    for item in python_files:
        file_path = str(item["path"])
        indexed = file_payloads.get(file_path)
        if indexed is None or isinstance(indexed.get("error"), dict):
            continue

        module = item.get("module")
        module_name = module if isinstance(module, str) else None
        file_id = f"file:{file_path}"
        container = module_name or file_id

        raw_symbols = indexed.get("symbols")
        if isinstance(raw_symbols, list):
            for raw in raw_symbols:
                if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
                    continue
                scope = _scope_names(raw.get("scope"))
                target = _symbol_id(module_name, file_path, scope, str(raw["name"]))
                _add_edge(
                    edges,
                    nodes,
                    kind="defines",
                    source=container,
                    target=target,
                    line=int(raw["line"]) if isinstance(raw.get("line"), int) else None,
                    resolution="ast",
                    confidence="exact",
                )

        bindings: list[tuple[tuple[str, ...], str, str]] = []
        raw_imports = indexed.get("imports")
        if isinstance(raw_imports, list):
            for raw in raw_imports:
                if not isinstance(raw, dict):
                    continue
                scope = _scope_names(raw.get("scope"))
                source = _scope_owner(module_name, file_path, scope, symbol_ids)
                import_kind = raw.get("kind")
                raw_module = raw.get("module")
                imported_module = raw_module if isinstance(raw_module, str) else None
                line = int(raw["line"]) if isinstance(raw.get("line"), int) else None
                alias = raw.get("alias")
                alias_name = alias if isinstance(alias, str) else None

                target: str
                binding_name: str | None = None
                binding_target: str | None = None
                expression: str | None = None

                if import_kind == "import" and imported_module:
                    expression = imported_module
                    if imported_module in module_ids:
                        target = imported_module
                    else:
                        target = f"external:{imported_module}"
                        nodes.setdefault(
                            target,
                            {
                                "id": target,
                                "kind": "external",
                                "name": imported_module,
                                "path": None,
                            },
                        )
                    if alias_name:
                        binding_name = alias_name
                        binding_target = imported_module
                    else:
                        binding_name = imported_module.split(".")[0]
                        binding_target = binding_name
                elif import_kind == "from":
                    level = int(raw.get("level", 0))
                    base = _absolute_import(
                        module_name,
                        file_path,
                        imported_module,
                        level,
                    )
                    imported_name = raw.get("name")
                    imported_name = (
                        imported_name if isinstance(imported_name, str) else None
                    )
                    expression = f"{'.' * level}{imported_module or ''}" + (
                        f" import {imported_name}" if imported_name else ""
                    )
                    candidate = (
                        ".".join(part for part in (base, imported_name) if part)
                        if imported_name != "*"
                        else base
                    )
                    if candidate and (
                        candidate in symbol_ids or candidate in module_ids
                    ):
                        target = candidate
                    elif base and base in module_ids:
                        target = base
                    else:
                        external_name = candidate or base or imported_name or "unknown"
                        target = f"external:{external_name}"
                        nodes.setdefault(
                            target,
                            {
                                "id": target,
                                "kind": "external",
                                "name": external_name,
                                "path": None,
                            },
                        )
                    if imported_name and imported_name != "*":
                        binding_name = alias_name or imported_name
                        binding_target = candidate
                else:
                    continue

                _add_edge(
                    edges,
                    nodes,
                    kind="import",
                    source=source,
                    target=target,
                    line=line,
                    expression=expression,
                    resolution="import",
                    confidence="exact",
                )
                if binding_name and binding_target:
                    bindings.append((tuple(scope), binding_name, binding_target))

        raw_calls = indexed.get("calls")
        if isinstance(raw_calls, list):
            for raw in raw_calls:
                if not isinstance(raw, dict):
                    continue
                expression = raw.get("expression")
                if not isinstance(expression, str):
                    continue
                scope = _scope_names(raw.get("scope"))
                source = _scope_owner(module_name, file_path, scope, symbol_ids)
                target, resolution = _resolve_reference(
                    expression,
                    source_module=module_name,
                    source_path=file_path,
                    scope=scope,
                    bindings=bindings,
                    symbol_ids=symbol_ids,
                    module_ids=module_ids,
                )
                _add_edge(
                    edges,
                    nodes,
                    kind="call",
                    source=source,
                    target=target,
                    line=int(raw["line"]) if isinstance(raw.get("line"), int) else None,
                    expression=expression,
                    resolution=resolution or "unresolved",
                    confidence="exact" if target is not None else "unknown",
                )

        if isinstance(raw_symbols, list):
            for raw in raw_symbols:
                if (
                    not isinstance(raw, dict)
                    or raw.get("kind") != "class"
                    or not isinstance(raw.get("name"), str)
                ):
                    continue
                scope = _scope_names(raw.get("scope"))
                source = _symbol_id(module_name, file_path, scope, str(raw["name"]))
                bases = raw.get("bases")
                if not isinstance(bases, list):
                    continue
                for base in bases:
                    if (
                        not isinstance(base, str)
                        or "=" in base
                        or base.startswith("**")
                    ):
                        continue
                    target, resolution = _resolve_reference(
                        base,
                        source_module=module_name,
                        source_path=file_path,
                        scope=scope,
                        bindings=bindings,
                        symbol_ids=symbol_ids,
                        module_ids=module_ids,
                    )
                    if target is not None and symbol_kinds.get(target) != "class":
                        target = None
                        resolution = None
                    _add_edge(
                        edges,
                        nodes,
                        kind="inheritance",
                        source=source,
                        target=target,
                        line=int(raw["line"])
                        if isinstance(raw.get("line"), int)
                        else None,
                        expression=base,
                        resolution=resolution or "unresolved",
                        confidence="exact" if target is not None else "unknown",
                    )

    exact_tested_by: set[tuple[str, str]] = set()
    original_edges = tuple(edges)
    for edge in original_edges:
        if edge.get("kind") not in {"import", "call"} or not edge.get("resolved"):
            continue
        source_path = edge.get("source_path")
        target = edge.get("to")
        if not isinstance(source_path, str) or not isinstance(target, str):
            continue
        source_item = files.get(source_path)
        if not isinstance(source_item, dict) or source_item.get("kind") != "test":
            continue
        target_path = _node_path(target, nodes)
        if target_path is None:
            continue
        target_item = files.get(target_path)
        if isinstance(target_item, dict) and target_item.get("kind") == "test":
            continue
        key = (target, source_path)
        if key in exact_tested_by:
            continue
        exact_tested_by.add(key)
        _add_edge(
            edges,
            nodes,
            kind="tested_by",
            source=target,
            target=f"file:{source_path}",
            line=int(edge["line"]) if isinstance(edge.get("line"), int) else None,
            resolution=str(edge.get("resolution", "ast")),
            confidence="exact",
            evidence=str(edge.get("kind")),
        )

    conventional_tested_by: set[tuple[str, str]] = set()
    for item in python_files:
        if item.get("kind") != "test":
            continue
        likely_source = item.get("likely_source")
        test_path = str(item["path"])
        if not isinstance(likely_source, str) or likely_source not in files:
            continue
        key = (likely_source, test_path)
        if key in conventional_tested_by:
            continue
        conventional_tested_by.add(key)
        _add_edge(
            edges,
            nodes,
            kind="tested_by",
            source=f"file:{likely_source}",
            target=f"file:{test_path}",
            resolution="filename_convention",
            confidence="likely",
            evidence="repository_map",
        )

    edges.sort(
        key=lambda edge: (
            str(edge.get("kind")),
            str(edge.get("source_path")),
            int(edge.get("line", 0)),
            str(edge.get("from")),
            str(edge.get("to")),
            str(edge.get("expression")),
        )
    )
    parse_errors.sort(key=lambda item: str(item.get("path")))

    return {
        "nodes": sorted(nodes.values(), key=lambda node: str(node["id"])),
        "edges": edges,
        "parse_errors": parse_errors,
    }


def _graph(
    root: Path,
    *,
    refresh: bool,
) -> tuple[dict[str, object], dict[str, object], bool]:
    mapped = _full_map(root, refresh=refresh)
    tree_sha = mapped.get("tree_sha")
    if not isinstance(tree_sha, str):
        raise TypeError("repository map has an invalid tree SHA")

    if not refresh:
        cached = _graph_cache_get(root, tree_sha)
        if cached is not None:
            return mapped, cached, True

    graph = _build_graph(root, mapped, refresh=refresh)
    _graph_cache_put(root, tree_sha, graph)
    return mapped, graph, False


def _resolve_symbol_filter(requested: str, nodes: list[dict[str, object]]) -> str:
    ids = {str(node["id"]) for node in nodes if "id" in node}
    if requested in ids:
        return requested
    matches = [
        str(node["id"])
        for node in nodes
        if node.get("name") == requested
        and node.get("kind") in {"class", "function", "method"}
    ]
    return matches[0] if len(matches) == 1 else requested


def relationships_state(
    path: str | Path = ".",
    file: str | None = None,
    symbol: str | None = None,
    kind: str | None = None,
    edge_limit: int = 500,
    node_limit: int = 500,
    error_limit: int = 50,
    refresh: bool = False,
) -> dict[str, object]:
    """Return a bounded graph of conservative repository code relationships."""
    if edge_limit < 0:
        raise ValueError("edge_limit must be non-negative")
    if node_limit < 0:
        raise ValueError("node_limit must be non-negative")
    if error_limit < 0:
        raise ValueError("error_limit must be non-negative")
    if kind is not None and kind not in _RELATION_KINDS:
        raise ValueError(
            "kind must be defines, import, call, inheritance, or tested_by"
        )

    root = find_root(path)
    mapped, graph, cache_hit = _graph(root, refresh=refresh)

    raw_nodes = graph.get("nodes")
    raw_edges = graph.get("edges")
    raw_errors = graph.get("parse_errors")
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        raise TypeError("cached relationship graph is invalid")
    nodes = [node for node in raw_nodes if isinstance(node, dict)]
    edges = [edge for edge in raw_edges if isinstance(edge, dict)]
    errors = [error for error in raw_errors or [] if isinstance(error, dict)]

    normalized_file = file.replace("\\", "/") if file is not None else None
    normalized_symbol = (
        _resolve_symbol_filter(symbol, nodes) if symbol is not None else None
    )

    selected: list[dict[str, object]] = []
    for edge in edges:
        if kind is not None and edge.get("kind") != kind:
            continue
        if normalized_file is not None and normalized_file not in {
            edge.get("source_path"),
            edge.get("target_path"),
        }:
            continue
        if normalized_symbol is not None and normalized_symbol not in {
            edge.get("from"),
            edge.get("to"),
        }:
            continue
        selected.append(edge)

    edge_counts: dict[str, int] = {}
    for edge in selected:
        edge_kind = str(edge.get("kind"))
        edge_counts[edge_kind] = edge_counts.get(edge_kind, 0) + 1

    returned_edges = selected[:edge_limit]
    referenced_ids: set[str] = set()
    for edge in returned_edges:
        source = edge.get("from")
        target = edge.get("to")
        if isinstance(source, str):
            referenced_ids.add(source)
        if isinstance(target, str):
            referenced_ids.add(target)
    if normalized_symbol is not None:
        referenced_ids.add(normalized_symbol)

    selected_nodes = [
        node
        for node in nodes
        if isinstance(node.get("id"), str) and node["id"] in referenced_ids
    ]
    selected_nodes.sort(key=lambda node: str(node["id"]))

    incoming = 0
    outgoing = 0
    if normalized_symbol is not None:
        incoming = sum(edge.get("to") == normalized_symbol for edge in selected)
        outgoing = sum(edge.get("from") == normalized_symbol for edge in selected)

    return {
        "kind": "relationships",
        "repository": mapped.get("repository"),
        "root": mapped.get("root"),
        "head_sha": mapped.get("head_sha"),
        "tree_sha": mapped.get("tree_sha"),
        "filters": {
            "file": normalized_file,
            "symbol": normalized_symbol,
            "kind": kind,
        },
        "summary": {
            "graph_cache_hit": cache_hit,
            "edges": len(selected),
            "resolved_edges": sum(bool(edge.get("resolved")) for edge in selected),
            "unresolved_edges": sum(
                not bool(edge.get("resolved")) for edge in selected
            ),
            "incoming": incoming,
            "outgoing": outgoing,
            "by_kind": edge_counts,
            "parse_errors": len(errors),
        },
        "nodes": selected_nodes[:node_limit],
        "nodes_truncated": len(selected_nodes) > node_limit,
        "edges": returned_edges,
        "edges_truncated": len(selected) > edge_limit,
        "parse_errors": errors[:error_limit],
        "parse_errors_truncated": len(errors) > error_limit,
    }
