"""GWay command adapter for repository operations."""

from __future__ import annotations

from .discovery import repository_info


def info(path: str = ".") -> dict[str, object]:
    """Return structured information about the Git repository containing path."""
    return repository_info(path)
