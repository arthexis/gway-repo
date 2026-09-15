"""Bounded source retrieval from Git-index blobs."""

from __future__ import annotations

from pathlib import Path

from .discovery import find_root
from .mapping import _full_map, _index_entries
from .symbols import _blob_bytes, symbols_state


def _tracked_blob(root: Path, file: str) -> tuple[str, bytes]:
    normalized = file.replace("\\", "/")
    entries = {
        str(item["path"]): str(item["object_id"])
        for item in _index_entries(root)
        if isinstance(item.get("path"), str) and isinstance(item.get("object_id"), str)
    }
    object_id = entries.get(normalized)
    if object_id is None:
        raise ValueError(f"file is not tracked in the Git index: {normalized}")
    return object_id, _blob_bytes(root, object_id)


def _resolve_symbol(
    path: str | Path,
    *,
    symbol: str,
    file: str | None,
    refresh: bool,
) -> dict[str, object]:
    indexed = symbols_state(
        path,
        file=file,
        name=symbol,
        limit=3,
        import_limit=0,
        error_limit=10,
        include_imports=False,
        refresh=refresh,
    )
    matches = indexed.get("symbols")
    if not isinstance(matches, list) or not matches:
        scope = f" in {file}" if file else ""
        raise ValueError(f"symbol not found{scope}: {symbol}")
    if len(matches) > 1:
        names = [str(item.get("qualified_name")) for item in matches if isinstance(item, dict)]
        suffix = ", ".join(names)
        raise ValueError(f"symbol is ambiguous: {symbol}; matches: {suffix}")
    match = matches[0]
    if not isinstance(match, dict):
        raise TypeError("symbol index returned an invalid symbol record")
    return match


def _bounded_lines(
    lines: list[str],
    *,
    start: int,
    end: int,
    max_lines: int,
    max_chars: int,
) -> tuple[str, int, bool, bool]:
    selected = lines[start - 1 : end]
    truncated_lines = len(selected) > max_lines
    selected = selected[:max_lines]

    kept: list[str] = []
    used = 0
    truncated_chars = False
    for line in selected:
        if used + len(line) <= max_chars:
            kept.append(line)
            used += len(line)
            continue
        remaining = max_chars - used
        if remaining > 0:
            kept.append(line[:remaining])
        truncated_chars = True
        break

    returned_end = start + max(len(kept) - 1, 0)
    truncated = truncated_lines or truncated_chars or returned_end < end
    return "".join(kept), returned_end, truncated, truncated_chars


def code_state(
    path: str | Path = ".",
    file: str | None = None,
    symbol: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
    context: int = 2,
    max_lines: int = 400,
    max_chars: int = 30_000,
    refresh: bool = False,
) -> dict[str, object]:
    """Return bounded text from one Git-index file or Python symbol.

    The Git index is authoritative, matching ``map`` and ``symbols``. ``symbol``
    may be combined with ``file`` to disambiguate a simple name. Explicit line
    ranges are only accepted for file retrieval.
    """
    if file is None and symbol is None:
        raise ValueError("one of file or symbol is required")
    if symbol is not None and (start_line is not None or end_line is not None):
        raise ValueError("start_line/end_line cannot be combined with symbol")
    if context < 0:
        raise ValueError("context must be non-negative")
    if max_lines <= 0:
        raise ValueError("max_lines must be positive")
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if start_line is not None and start_line < 1:
        raise ValueError("start_line must be at least 1")
    if end_line is not None and end_line < 1:
        raise ValueError("end_line must be at least 1")
    if start_line is not None and end_line is not None and start_line > end_line:
        raise ValueError("start_line cannot be greater than end_line")

    root = find_root(path)
    mapped = _full_map(root, refresh=refresh)
    resolved_symbol: dict[str, object] | None = None
    normalized_file = file.replace("\\", "/") if file is not None else None

    if symbol is not None:
        resolved_symbol = _resolve_symbol(
            root,
            symbol=symbol,
            file=normalized_file,
            refresh=refresh,
        )
        symbol_file = resolved_symbol.get("path")
        if not isinstance(symbol_file, str):
            raise TypeError("symbol index returned a symbol without a path")
        normalized_file = symbol_file
        line = resolved_symbol.get("line")
        end = resolved_symbol.get("end_line")
        if not isinstance(line, int) or not isinstance(end, int):
            raise TypeError("symbol index returned an invalid source range")
        requested_start = max(1, line - context)
        requested_end = end + context
    else:
        if normalized_file is None:
            raise AssertionError("file selector unexpectedly missing")
        requested_start = start_line or 1
        requested_end = end_line

    object_id, raw = _tracked_blob(root, normalized_file)
    if b"\x00" in raw:
        raise ValueError(f"tracked file is binary: {normalized_file}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"tracked file is not UTF-8 text: {normalized_file}") from exc

    lines = text.splitlines(keepends=True)
    total_lines = len(lines)
    if total_lines == 0:
        total_lines = 1
        lines = [""]

    if requested_start > total_lines:
        raise ValueError(
            f"start line {requested_start} is beyond end of file ({total_lines})"
        )
    if requested_end is None:
        requested_end = total_lines
    requested_end = min(requested_end, total_lines)

    content, returned_end, truncated, char_truncated = _bounded_lines(
        lines,
        start=requested_start,
        end=requested_end,
        max_lines=max_lines,
        max_chars=max_chars,
    )

    return {
        "kind": "code",
        "repository": mapped.get("repository"),
        "root": mapped.get("root"),
        "head_sha": mapped.get("head_sha"),
        "tree_sha": mapped.get("tree_sha"),
        "path": normalized_file,
        "object_id": object_id,
        "selector": {
            "file": file.replace("\\", "/") if file is not None else None,
            "symbol": symbol,
            "start_line": start_line,
            "end_line": end_line,
            "context": context,
        },
        "symbol": resolved_symbol,
        "total_lines": total_lines,
        "start_line": requested_start,
        "end_line": returned_end,
        "requested_end_line": requested_end,
        "content": content,
        "truncated": truncated,
        "char_truncated": char_truncated,
    }
