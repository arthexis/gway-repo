"""Reuse the installing user's existing GitHub identity."""
import os
from pathlib import Path
import subprocess


def github_token():
    """Use existing environment or gh login; never maintain a second token."""
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    executable = Path.home() / ".local/bin/gh"
    if not executable.is_file():
        return None
    try:
        result = subprocess.run(
            [str(executable), "auth", "token", "-h", "github.com"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    token = result.stdout.strip()
    if result.returncode != 0 or not 10 <= len(token) <= 4096:
        return None
    return token
