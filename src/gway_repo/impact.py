"""Bounded change-impact analysis over the repository relationship graph."""

from __future__ import annotations

import subprocess
from collections import deque
from pathlib import Path

from .discovery import find_root
from .github import pull_request_state, resolve_repository
from .links import issue_pull_requests_state
from .mapping import _run_git
from .relationships import _graph

_DEPENDENCY_KINDS = {"call", "import", "inheritance"}
_CONFIDENCE = {"unknown": 0, "likely": 1, "exact": 2}


def _validate_limits(**values: int) -> None:
    for name, value in values.items():
        if value < 0:
            raise ValueError(f"{name} must be non-negative")


def _local_graph(
    path: str | Path,
    *,
    refresh: bool,
) -> tuple[dict[str, object] | None, dict[str, object] | None, bool, str | None]:
    try:
        root = find_root(path)
        mapped, graph, cache_hit = _graph(root, refresh=refresh)
        if not isinstance(mapped, dict) or not isinstance(graph, dict):
            raise TypeError("relationship graph is invalid")
        if not isinstance(graph.get("nodes"), list):
            raise TypeError("relationship graph has invalid nodes")
        if not isinstance(graph.get("edges"), list):
            raise TypeError("relationship graph has invalid edges")
        return mapped, graph, cache_hit, None
    except (
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        return None, None, False, str(exc)


def _head_tree(path: str | Path) -> str | None:
    try:
        root = find_root(path)
        return _run_git(root, "rev-parse", "HEAD^{tree}").strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _pull_head_sha(pull: dict[str, object]) -> str | None:
    head = pull.get("head")
    if not isinstance(head, dict):
        return None
    sha = head.get("sha")
    return sha if isinstance(sha, str) else None


def _pull_files(pull: dict[str, object], number: int) -> list[dict[str, object]]:
    raw = pull.get("files")
    if not isinstance(raw, list):
        return []
    files: list[dict[str, object]] = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            continue
        previous = item.get("previous_path")
        files.append(
            {
                "path": str(item["path"]).replace("\\", "/"),
                "statuses": [str(item.get("status") or "modified")],
                "previous_paths": [previous] if isinstance(previous, str) else [],
                "pull_requests": [number],
            }
        )
    return files


def _merge_file_records(
    groups: list[list[dict[str, object]]],
) -> list[dict[str, object]]:
    merged: dict[str, dict[str, object]] = {}
    for group in groups:
        for item in group:
            path = item.get("path")
            if not isinstance(path, str):
                continue
            record = merged.setdefault(
                path,
                {
                    "path": path,
                    "statuses": set(),
                    "previous_paths": set(),
                    "pull_requests": set(),
                },
            )
            for status in item.get("statuses", []):
                if isinstance(status, str):
                    record["statuses"].add(status)
            for previous in item.get("previous_paths", []):
                if isinstance(previous, str):
                    record["previous_paths"].add(previous)
            for number in item.get("pull_requests", []):
                if isinstance(number, int):
                    record["pull_requests"].add(number)

    return [
        {
            "path": path,
            "statuses": sorted(item["statuses"]),
            "previous_paths": sorted(item["previous_paths"]),
            "pull_requests": sorted(item["pull_requests"]),
        }
        for path, item in sorted(merged.items())
    ]


def _better_confidence(current: str, candidate: str) -> str:
    if _CONFIDENCE.get(candidate, 0) > _CONFIDENCE.get(current, 0):
        return candidate
    return current


def _add_test(
    tests: dict[str, dict[str, object]],
    *,
    path: str,
    confidence: str,
    evidence: str,
    source: str,
    pull_requests: set[int],
) -> None:
    record = tests.setdefault(
        path,
        {
            "path": path,
            "confidence": confidence,
            "evidence": set(),
            "sources": set(),
            "pull_requests": set(),
        },
    )
    record["confidence"] = _better_confidence(
        str(record["confidence"]),
        confidence,
    )
    record["evidence"].add(evidence)
    record["sources"].add(source)
    record["pull_requests"].update(pull_requests)


def _tests_for(
    node_id: str,
    pull_requests: set[int],
    tested_by: dict[str, list[dict[str, object]]],
    tests: dict[str, dict[str, object]],
) -> None:
    for edge in tested_by.get(node_id, []):
        path = edge.get("target_path")
        if not isinstance(path, str):
            continue
        evidence = edge.get("evidence") or edge.get("resolution") or "tested_by"
        _add_test(
            tests,
            path=path,
            confidence=str(edge.get("confidence") or "unknown"),
            evidence=str(evidence),
            source=node_id,
            pull_requests=pull_requests,
        )


def _graph_indexes(
    graph: dict[str, object],
) -> tuple[
    dict[str, dict[str, object]],
    dict[str, list[str]],
    dict[str, list[dict[str, object]]],
    dict[str, list[dict[str, object]]],
]:
    raw_nodes = graph["nodes"]
    raw_edges = graph["edges"]
    nodes = {
        str(node["id"]): node
        for node in raw_nodes
        if isinstance(node, dict) and isinstance(node.get("id"), str)
    }
    nodes_by_path: dict[str, list[str]] = {}
    for node_id, node in nodes.items():
        node_path = node.get("path")
        if isinstance(node_path, str):
            nodes_by_path.setdefault(node_path, []).append(node_id)

    incoming: dict[str, list[dict[str, object]]] = {}
    tested_by: dict[str, list[dict[str, object]]] = {}
    for edge in raw_edges:
        if not isinstance(edge, dict):
            continue
        source = edge.get("from")
        target = edge.get("to")
        kind = edge.get("kind")
        if kind == "tested_by" and isinstance(source, str):
            tested_by.setdefault(source, []).append(edge)
        if (
            kind in _DEPENDENCY_KINDS
            and edge.get("resolved")
            and isinstance(target, str)
        ):
            incoming.setdefault(target, []).append(edge)
    return nodes, nodes_by_path, incoming, tested_by


def _map_paths(mapped: dict[str, object]) -> set[str]:
    raw = mapped.get("files")
    if not isinstance(raw, list):
        return set()
    return {
        str(item["path"])
        for item in raw
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }


def _seed_changes(
    records: list[dict[str, object]],
    nodes: dict[str, dict[str, object]],
    nodes_by_path: dict[str, list[str]],
    map_paths: set[str],
) -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    dict[str, set[int]],
    dict[str, dict[str, object]],
]:
    changed_files: list[dict[str, object]] = []
    unmapped: list[dict[str, object]] = []
    seeds: dict[str, set[int]] = {}
    symbols: dict[str, dict[str, object]] = {}

    for item in records:
        file_path = str(item["path"])
        pull_requests = {
            number
            for number in item.get("pull_requests", [])
            if isinstance(number, int)
        }
        mapped_file = file_path in map_paths
        output = {**item, "mapped": mapped_file}
        changed_files.append(output)
        if not mapped_file:
            unmapped.append(output)
            continue

        for node_id in nodes_by_path.get(file_path, []):
            seeds.setdefault(node_id, set()).update(pull_requests)
            node = nodes[node_id]
            if node.get("kind") not in {"class", "function", "method"}:
                continue
            symbol = symbols.setdefault(
                node_id,
                {
                    "id": node_id,
                    "kind": node.get("kind"),
                    "path": file_path,
                    "line": node.get("line"),
                    "pull_requests": set(),
                },
            )
            symbol["pull_requests"].update(pull_requests)
    return changed_files, unmapped, seeds, symbols


def _walk_dependents(
    nodes: dict[str, dict[str, object]],
    incoming: dict[str, list[dict[str, object]]],
    tested_by: dict[str, list[dict[str, object]]],
    seeds: dict[str, set[int]],
    *,
    depth: int,
    visit_limit: int,
) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, object]], bool]:
    queue: deque[str] = deque(seeds)
    distance = {node_id: 0 for node_id in seeds}
    provenance = {node_id: set(values) for node_id, values in seeds.items()}
    dependents: dict[str, dict[str, object]] = {}
    tests: dict[str, dict[str, object]] = {}
    truncated = len(distance) > visit_limit

    if truncated:
        retained = set(sorted(distance)[:visit_limit])
        queue = deque(node_id for node_id in queue if node_id in retained)
        distance = {
            node_id: value for node_id, value in distance.items() if node_id in retained
        }
        provenance = {
            node_id: value
            for node_id, value in provenance.items()
            if node_id in retained
        }

    while queue:
        current = queue.popleft()
        current_distance = distance[current]
        current_prs = provenance[current]
        _tests_for(current, current_prs, tested_by, tests)
        if current_distance >= depth:
            continue

        for edge in incoming.get(current, []):
            source = edge.get("from")
            if not isinstance(source, str) or source not in nodes:
                continue
            source_node = nodes[source]
            new_distance = current_distance + 1
            if source_node.get("test"):
                test_path = source_node.get("path")
                if isinstance(test_path, str):
                    _add_test(
                        tests,
                        path=test_path,
                        confidence="exact",
                        evidence=str(edge.get("kind")),
                        source=source,
                        pull_requests=current_prs,
                    )
                continue

            if source not in seeds:
                dependent = dependents.setdefault(
                    source,
                    {
                        "id": source,
                        "kind": source_node.get("kind"),
                        "path": source_node.get("path"),
                        "distance": new_distance,
                        "confidence": str(edge.get("confidence") or "exact"),
                        "via": set(),
                        "pull_requests": set(),
                    },
                )
                dependent["distance"] = min(
                    int(dependent["distance"]),
                    new_distance,
                )
                dependent["confidence"] = _better_confidence(
                    str(dependent["confidence"]),
                    str(edge.get("confidence") or "exact"),
                )
                dependent["via"].add(str(edge.get("kind")))
                dependent["pull_requests"].update(current_prs)

            existing_distance = distance.get(source)
            existing_prs = provenance.setdefault(source, set())
            new_prs = current_prs - existing_prs
            improved = existing_distance is None or new_distance < existing_distance
            if existing_distance is None and len(distance) >= visit_limit:
                truncated = True
                continue
            if improved:
                distance[source] = new_distance
            if new_prs:
                existing_prs.update(new_prs)
            if improved or new_prs:
                queue.append(source)

    for node_id, record in dependents.items():
        _tests_for(node_id, record["pull_requests"], tested_by, tests)
    return dependents, tests, truncated


def _serialize_symbols(
    symbols: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    return [
        {**item, "pull_requests": sorted(item["pull_requests"])}
        for _, item in sorted(symbols.items())
    ]


def _serialize_dependents(
    dependents: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    ordered = sorted(
        dependents.items(),
        key=lambda pair: (int(pair[1]["distance"]), pair[0]),
    )
    return [
        {
            **item,
            "id": node_id,
            "via": sorted(item["via"]),
            "pull_requests": sorted(item["pull_requests"]),
        }
        for node_id, item in ordered
    ]


def _serialize_tests(
    tests: dict[str, dict[str, object]],
) -> list[dict[str, object]]:
    return [
        {
            **item,
            "evidence": sorted(item["evidence"]),
            "sources": sorted(item["sources"]),
            "pull_requests": sorted(item["pull_requests"]),
        }
        for _, item in sorted(tests.items())
    ]


def _impact_from_records(
    mapped: dict[str, object],
    graph: dict[str, object],
    records: list[dict[str, object]],
    *,
    target: dict[str, object],
    basis: dict[str, object],
    graph_cache_hit: bool,
    depth: int,
    changed_file_limit: int,
    symbol_limit: int,
    dependent_limit: int,
    test_limit: int,
    error_limit: int,
    visit_limit: int,
) -> dict[str, object]:
    nodes, nodes_by_path, incoming, tested_by = _graph_indexes(graph)
    changed_files, unmapped, seeds, raw_symbols = _seed_changes(
        records,
        nodes,
        nodes_by_path,
        _map_paths(mapped),
    )
    raw_dependents, raw_tests, traversal_truncated = _walk_dependents(
        nodes,
        incoming,
        tested_by,
        seeds,
        depth=depth,
        visit_limit=visit_limit,
    )
    symbols = _serialize_symbols(raw_symbols)
    dependents = _serialize_dependents(raw_dependents)
    tests = _serialize_tests(raw_tests)
    raw_errors = graph.get("parse_errors")
    errors = [item for item in raw_errors or [] if isinstance(item, dict)]

    return {
        "kind": "impact",
        "repository": mapped.get("repository"),
        "target": target,
        "available": True,
        "basis": basis,
        "summary": {
            "graph_cache_hit": graph_cache_hit,
            "changed_files": len(changed_files),
            "mapped_files": len(changed_files) - len(unmapped),
            "unmapped_files": len(unmapped),
            "changed_symbols": len(symbols),
            "dependents": len(dependents),
            "tests": len(tests),
            "traversal_truncated": traversal_truncated,
            "parse_errors": len(errors),
        },
        "changed_files": changed_files[:changed_file_limit],
        "changed_files_truncated": len(changed_files) > changed_file_limit,
        "changed_symbols": symbols[:symbol_limit],
        "changed_symbols_truncated": len(symbols) > symbol_limit,
        "dependents": dependents[:dependent_limit],
        "dependents_truncated": len(dependents) > dependent_limit,
        "tests": tests[:test_limit],
        "tests_truncated": len(tests) > test_limit,
        "unmapped_files": unmapped[:changed_file_limit],
        "unmapped_files_truncated": len(unmapped) > changed_file_limit,
        "parse_errors": errors[:error_limit],
        "parse_errors_truncated": len(errors) > error_limit,
    }


def _unavailable(
    *,
    repository: str | None,
    target: dict[str, object],
    reason: str,
    pull_requests: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "kind": "impact",
        "repository": repository,
        "target": target,
        "available": False,
        "reason": reason,
        "basis": {"authoritative": False, "reason": reason},
        "summary": {
            "changed_files": 0,
            "mapped_files": 0,
            "unmapped_files": 0,
            "changed_symbols": 0,
            "dependents": 0,
            "tests": 0,
        },
        "pull_requests": pull_requests or [],
        "changed_files": [],
        "changed_symbols": [],
        "dependents": [],
        "tests": [],
        "unmapped_files": [],
        "parse_errors": [],
    }


def _basis(
    mapped: dict[str, object],
    path: str | Path,
    *,
    repository: str | None,
    target_head_sha: str | None,
) -> dict[str, object]:
    analysis_head = mapped.get("head_sha")
    index_tree = mapped.get("tree_sha")
    head_tree = _head_tree(path)
    repository_matches = repository is None or mapped.get("repository") == repository
    head_matches = target_head_sha is None or analysis_head == target_head_sha
    index_clean = isinstance(index_tree, str) and index_tree == head_tree
    authoritative = bool(repository_matches and head_matches and index_clean)

    reason = None
    if not repository_matches:
        reason = "local_repository_does_not_match_target"
    elif not index_clean:
        reason = "git_index_differs_from_head"
    elif not head_matches:
        reason = "local_checkout_does_not_match_pr_head"

    return {
        "analysis_mode": "current_index_projection",
        "analysis_head_sha": analysis_head,
        "index_tree_sha": index_tree,
        "head_tree_sha": head_tree,
        "target_head_sha": target_head_sha,
        "repository_matches": repository_matches,
        "head_matches": head_matches,
        "index_clean": index_clean,
        "authoritative": authoritative,
        "reason": reason,
    }


def impact_for_pull_state(
    pull: dict[str, object],
    *,
    repository: str,
    path: str | Path = ".",
    depth: int = 2,
    changed_file_limit: int = 200,
    symbol_limit: int = 200,
    dependent_limit: int = 200,
    test_limit: int = 100,
    error_limit: int = 50,
    visit_limit: int = 2_000,
    refresh: bool = False,
) -> dict[str, object]:
    """Analyze one already-fetched pull request against the current Git index."""
    number = int(pull.get("number") or 0)
    target = {"kind": "pull_request", "number": number}
    mapped, graph, cache_hit, error = _local_graph(path, refresh=refresh)
    if mapped is None or graph is None:
        return _unavailable(
            repository=repository,
            target=target,
            reason=f"local_analysis_unavailable: {error}",
        )
    if mapped.get("repository") != repository:
        return _unavailable(
            repository=repository,
            target=target,
            reason="local_repository_does_not_match_target",
        )

    result = _impact_from_records(
        mapped,
        graph,
        _pull_files(pull, number),
        target=target,
        basis=_basis(
            mapped,
            path,
            repository=repository,
            target_head_sha=_pull_head_sha(pull),
        ),
        graph_cache_hit=cache_hit,
        depth=depth,
        changed_file_limit=changed_file_limit,
        symbol_limit=symbol_limit,
        dependent_limit=dependent_limit,
        test_limit=test_limit,
        error_limit=error_limit,
        visit_limit=visit_limit,
    )
    result["source_complete"] = not bool(pull.get("files_truncated"))
    return result


def _issue_source_complete(links: dict[str, object]) -> bool:
    if links.get("pull_requests_truncated"):
        return False
    summary = links.get("summary")
    if not isinstance(summary, dict):
        return True
    return not bool(summary.get("timeline_truncated"))


def _issue_impact(
    issue: int,
    *,
    repository: str,
    path: str | Path,
    depth: int,
    pr_limit: int,
    event_limit: int,
    file_limit: int,
    changed_file_limit: int,
    symbol_limit: int,
    dependent_limit: int,
    test_limit: int,
    error_limit: int,
    visit_limit: int,
    refresh: bool,
) -> dict[str, object]:
    links = issue_pull_requests_state(
        issue,
        repository=repository,
        path=str(path),
        limit=pr_limit,
        event_limit=event_limit,
    )
    linked = links.get("pull_requests")
    if not isinstance(linked, list):
        raise TypeError("issue pull-request discovery returned invalid data")

    mapped, graph, cache_hit, error = _local_graph(path, refresh=refresh)
    if mapped is None or graph is None:
        return _unavailable(
            repository=repository,
            target={"kind": "issue", "number": issue},
            reason=f"local_analysis_unavailable: {error}",
            pull_requests=[item for item in linked if isinstance(item, dict)],
        )
    if mapped.get("repository") != repository:
        return _unavailable(
            repository=repository,
            target={"kind": "issue", "number": issue},
            reason="local_repository_does_not_match_target",
            pull_requests=[item for item in linked if isinstance(item, dict)],
        )

    per_pr: list[dict[str, object]] = []
    file_groups: list[list[dict[str, object]]] = []
    all_heads_match = True
    source_complete = _issue_source_complete(links)

    for linked_pr in linked:
        if not isinstance(linked_pr, dict):
            continue
        number = linked_pr.get("number")
        pr_repository = linked_pr.get("repository")
        if not isinstance(number, int) or not isinstance(pr_repository, str):
            continue
        if pr_repository != repository:
            per_pr.append(
                {
                    **linked_pr,
                    "available": False,
                    "reason": "cross_repository_impact_not_supported",
                }
            )
            source_complete = False
            all_heads_match = False
            continue

        pull = pull_request_state(
            number,
            repository=repository,
            path=str(path),
            body_limit=0,
            file_limit=file_limit,
        )
        files = _pull_files(pull, number)
        file_groups.append(files)
        head_sha = _pull_head_sha(pull)
        head_matches = mapped.get("head_sha") == head_sha
        all_heads_match = all_heads_match and head_matches
        if pull.get("files_truncated"):
            source_complete = False

        one = _impact_from_records(
            mapped,
            graph,
            files,
            target={"kind": "pull_request", "number": number},
            basis=_basis(
                mapped,
                path,
                repository=repository,
                target_head_sha=head_sha,
            ),
            graph_cache_hit=cache_hit,
            depth=depth,
            changed_file_limit=changed_file_limit,
            symbol_limit=symbol_limit,
            dependent_limit=dependent_limit,
            test_limit=test_limit,
            error_limit=error_limit,
            visit_limit=visit_limit,
        )
        per_pr.append(
            {
                **linked_pr,
                "available": True,
                "head_sha": head_sha,
                "head_matches_analysis": head_matches,
                "files_truncated": bool(pull.get("files_truncated")),
                "impact": one["summary"],
            }
        )

    basis = _basis(
        mapped,
        path,
        repository=repository,
        target_head_sha=None,
    )
    if not all_heads_match:
        basis["authoritative"] = False
        basis["reason"] = "one_or_more_pr_heads_differ_from_analysis_head"
    basis["all_pr_heads_match"] = all_heads_match

    result = _impact_from_records(
        mapped,
        graph,
        _merge_file_records(file_groups),
        target={"kind": "issue", "number": issue},
        basis=basis,
        graph_cache_hit=cache_hit,
        depth=depth,
        changed_file_limit=changed_file_limit,
        symbol_limit=symbol_limit,
        dependent_limit=dependent_limit,
        test_limit=test_limit,
        error_limit=error_limit,
        visit_limit=visit_limit,
    )
    result["pull_requests"] = per_pr
    result["pull_requests_truncated"] = bool(links.get("pull_requests_truncated"))
    result["source_complete"] = source_complete
    result["issue_links"] = links.get("summary")
    return result


def impact_state(
    pr: int | None = None,
    issue: int | None = None,
    file: str | None = None,
    repository: str | None = None,
    path: str | Path = ".",
    depth: int = 2,
    pr_limit: int = 20,
    event_limit: int = 300,
    file_limit: int = 100,
    changed_file_limit: int = 200,
    symbol_limit: int = 200,
    dependent_limit: int = 200,
    test_limit: int = 100,
    error_limit: int = 50,
    visit_limit: int = 2_000,
    refresh: bool = False,
) -> dict[str, object]:
    """Return bounded impact for exactly one PR, issue, or local file."""
    targets = sum(value is not None for value in (pr, issue, file))
    if targets != 1:
        raise ValueError("pass exactly one of --pr, --issue, or --file")
    _validate_limits(
        depth=depth,
        pr_limit=pr_limit,
        event_limit=event_limit,
        file_limit=file_limit,
        changed_file_limit=changed_file_limit,
        symbol_limit=symbol_limit,
        dependent_limit=dependent_limit,
        test_limit=test_limit,
        error_limit=error_limit,
        visit_limit=visit_limit,
    )

    if file is not None:
        normalized = file.replace("\\", "/")
        mapped, graph, cache_hit, error = _local_graph(path, refresh=refresh)
        if mapped is None or graph is None:
            return _unavailable(
                repository=repository,
                target={"kind": "file", "path": normalized},
                reason=f"local_analysis_unavailable: {error}",
            )
        basis = _basis(mapped, path, repository=None, target_head_sha=None)
        basis["authoritative"] = True
        basis["reason"] = None
        return _impact_from_records(
            mapped,
            graph,
            [
                {
                    "path": normalized,
                    "statuses": ["local"],
                    "previous_paths": [],
                    "pull_requests": [],
                }
            ],
            target={"kind": "file", "path": normalized},
            basis=basis,
            graph_cache_hit=cache_hit,
            depth=depth,
            changed_file_limit=changed_file_limit,
            symbol_limit=symbol_limit,
            dependent_limit=dependent_limit,
            test_limit=test_limit,
            error_limit=error_limit,
            visit_limit=visit_limit,
        )

    repo = resolve_repository(repository, str(path))
    if pr is not None:
        number = int(pr)
        pull = pull_request_state(
            number,
            repository=repo,
            path=str(path),
            body_limit=0,
            file_limit=file_limit,
        )
        return impact_for_pull_state(
            pull,
            repository=repo,
            path=path,
            depth=depth,
            changed_file_limit=changed_file_limit,
            symbol_limit=symbol_limit,
            dependent_limit=dependent_limit,
            test_limit=test_limit,
            error_limit=error_limit,
            visit_limit=visit_limit,
            refresh=refresh,
        )

    return _issue_impact(
        int(issue),
        repository=repo,
        path=path,
        depth=depth,
        pr_limit=pr_limit,
        event_limit=event_limit,
        file_limit=file_limit,
        changed_file_limit=changed_file_limit,
        symbol_limit=symbol_limit,
        dependent_limit=dependent_limit,
        test_limit=test_limit,
        error_limit=error_limit,
        visit_limit=visit_limit,
        refresh=refresh,
    )
