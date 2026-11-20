# Immutable MCP provider deployment

`gway-repo` stages its own reviewed, immutable MCP stdio release. It never
creates a new HTTP service, SSH route, credential store, root policy, or timer.

For first-time staging, an operator invokes the checkout-local
`deploy.sh --expected-sha FULL_SHA`
as the enrolled node's installation user. Its output is an executable zipapp
at `.local/share/gway-repo/releases/FULL_SHA/bin/gway-repo-mcp` under that
user home. Only committed Git blobs from that SHA are packaged; no network
package installer or unaudited untracked sources are used. Repeated stages
verify the exact release and return an unchanged result.

GWay Remote continues to own the transport, discovery, execute/read scopes
and node-side deployment ledger. After staging and review, an operator-owned
Ansible provisioner uses its existing `become` boundary to publish a pinned,
root-owned `/etc/gway-mcp-providers/repo.json` manifest:

```json
{
  "source_id": "repo",
  "revision": "FULL_SHA",
  "executable": "ABSOLUTE_RELEASE_PATH/bin/gway-repo-mcp",
  "argv": ["ABSOLUTE_RELEASE_PATH/bin/gway-repo-mcp"],
  "deployment": {
    "schema": 1,
    "checkout": "ABSOLUTE_REVIEWED_CHECKOUT",
    "runner": "ABSOLUTE_REVIEWED_CHECKOUT/deploy.sh",
    "revision": "FULL_SHA"
  }
}
```

The approved `checkout` must be an immutable, commit-named Git working tree at
`/home/arthe/.local/share/gway-repo/checkouts/FULL_SHA`. The existing broker
invokes the runner with **zero arguments**; this installer derives the SHA only
from that canonical path and independently verifies Git HEAD. Calls from an
unrecognized path fail closed.

Existing `mcp_installed_providers` validates the registration, and
`mcp_deployment_broker` handles provider preview/run/status receipts.
No second deployment channel is needed; the first manifest provisioning
still requires explicit operator-reviewed bootstrap and live verification.

Only after read/write scope, GitHub operation parity, live health and rollback
are verified may GWay Remote's old GitHub builtins be retired.
