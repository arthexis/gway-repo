#!/usr/bin/env bash
# Provider-owned immutable build. Execution/receipts belong to GWay Remote.
set -euo pipefail

if [[ $# != 2 || "$1" != "--expected-sha" || ! "$2" =~ ^[0-9a-f]{40}$ ]]; then
    echo "expected --expected-sha and a full lowercase commit SHA" >&2
    exit 2
fi
if [[ "$(id -u)" == 0 ]]; then
    echo "run as the enrolled installation user, never root" >&2
    exit 2
fi

sha="$2"
checkout="$(cd -- "$(dirname -- "$0")" && pwd -P)"
if [[ "$(git -C "$checkout" rev-parse --show-toplevel)" != "$checkout" ||
      "$(git -C "$checkout" rev-parse HEAD)" != "$sha" ]]; then
    echo "checkout does not match the pinned revision" >&2
    exit 2
fi

root="$HOME/.local/share/gway-repo"
releases="$root/releases"
release="$releases/$sha"
umask 022
mkdir -p "$releases"
chmod 0700 "$root" "$releases"

verify() {
    /usr/bin/python3 - "$1" <<'PY'
import json
import subprocess
import sys

result = subprocess.run(
    [sys.argv[1]],
    input='{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n',
    capture_output=True,
    text=True,
    timeout=8,
    check=True,
)
lines = result.stdout.splitlines()
if len(lines) != 1:
    raise SystemExit("invalid MCP stdio response")
tools = json.loads(lines[0])["result"]["tools"]
names = {tool["name"] for tool in tools}
required = {
    "pr", "issue", "checks", "workflows",
    "github_workflow_list", "github_workflow_runs",
    "github_workflow_dispatch", "github_workflow_cancel",
}
if not required.issubset(names):
    raise SystemExit("missing tools")
PY
}

if [[ -e "$release" ]]; then
    if [[ ! -f "$release/bin/gway-repo-mcp" ||
          ! -x "$release/bin/gway-repo-mcp" ]]; then
        echo "existing release is incomplete" >&2
        exit 2
    fi
    verify "$release/bin/gway-repo-mcp"
    printf '{"schema":1,"state":"unchanged","revision":"%s"}\n' "$sha"
    exit 0
fi

tmp="$(mktemp -d "$releases/.stage.XXXXXXXX")"
trap 'rm -rf -- "$tmp"' EXIT
mkdir -p "$tmp/build" "$tmp/bin"
git -C "$checkout" archive --format=tar "$sha" src/gway_repo |
    tar -xf - -C "$tmp/build"
/usr/bin/python3 -m zipapp "$tmp/build/src" \
    -m "gway_repo.mcp_stdio:main" -p "/usr/bin/python3" \
    -o "$tmp/bin/gway-repo-mcp"
chmod 0755 "$tmp/bin/gway-repo-mcp"
verify "$tmp/bin/gway-repo-mcp"
mv -- "$tmp" "$release"
printf '{"schema":1,"state":"staged","revision":"%s"}\n' "$sha"
