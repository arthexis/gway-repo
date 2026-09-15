"""Local Git repository discovery."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_GITHUB_SCP = re.compile(r"^(?:[^@]+@)?github\.com:(?P<repo>[^/]+/[^/]+?)(?:\.git)?$")
_GITHUB_URL = re.compile(
    r"^(?:https?|ssh|git)://(?:[^@/]+@)?github\.com/(?P<repo>[^/]+/[^/]+?)(?:\.git)?/?$"
)


def _git(path: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode:
        message = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise ValueError(message)
    return result.stdout.strip() if result.returncode == 0 else ""


def find_root(path: str | Path = ".") -> Path:
    """Return the root of the Git repository containing path."""
    candidate = Path(path).expanduser().resolve()
    if candidate.is_file():
        candidate = candidate.parent
    root = _git(candidate, "rev-parse", "--show-toplevel")
    return Path(root).resolve()


def github_repository(remote_url: str) -> str | None:
    """Extract ``owner/repo`` from a github.com remote URL when possible."""
    remote_url = remote_url.strip()
    for pattern in (_GITHUB_SCP, _GITHUB_URL):
        match = pattern.match(remote_url)
        if match:
            return match.group("repo")
    return None


def _remotes(root: Path) -> dict[str, str]:
    names = _git(root, "remote", check=False).splitlines()
    return {
        name: _git(root, "remote", "get-url", name) for name in names if name.strip()
    }


def _default_branch(root: Path, remote: str | None, branch: str | None) -> str | None:
    if remote:
        symbolic = _git(
            root,
            "symbolic-ref",
            "--quiet",
            "--short",
            f"refs/remotes/{remote}/HEAD",
            check=False,
        )
        prefix = f"{remote}/"
        if symbolic.startswith(prefix):
            return symbolic[len(prefix) :]

    for candidate in ("main", "master"):
        if (
            _git(
                root,
                "show-ref",
                "--verify",
                "--quiet",
                f"refs/heads/{candidate}",
                check=False,
            )
            == ""
        ):
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "show-ref",
                    "--verify",
                    "--quiet",
                    f"refs/heads/{candidate}",
                ],
                check=False,
            )
            if result.returncode == 0:
                return candidate
    return branch


def repository_info(path: str | Path = ".") -> dict[str, object]:
    """Return compact local repository state suitable for GWay composition."""
    root = find_root(path)
    branch = _git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    branch = branch or None
    head = _git(root, "rev-parse", "HEAD")
    remotes = _remotes(root)
    primary_remote = "origin" if "origin" in remotes else next(iter(remotes), None)
    remote_url = remotes.get(primary_remote) if primary_remote else None

    return {
        "root": str(root),
        "branch": branch,
        "head": head,
        "remotes": remotes,
        "primary_remote": primary_remote,
        "repository": github_repository(remote_url) if remote_url else None,
        "default_branch": _default_branch(root, primary_remote, branch),
    }
