# gway-repo

Repository introspection utilities for GWay.

Commands return regular Python mappings so GWay can retain them as chain
results, pass values to later commands, or serialize them at an output boundary.
That contract is intentional: small repository queries should compose cleanly
into one-line chains and recipes without parsing terminal prose.

## Local repository info

```console
gway repo info
gway repo info --path ../another-repository
```

`info` reports the repository root, current branch, HEAD commit, configured
remotes, primary remote, GitHub `owner/repo` name when recognizable, and the
best locally-known default branch. Repository discovery does not require a
network request.

## GitHub PR and issue state

```console
gway repo pr 2
gway repo issue 1
gway repo pr 916 --repository arthexis/gway --file-limit 0
gway repo issue 917 --repository arthexis/gway --body-limit 4000
```

When `--repository` is omitted, `gway-repo` infers `owner/repo` from the local
Git remote. Public repositories can be queried anonymously. Set `GH_TOKEN` or
`GITHUB_TOKEN` to authenticate for private repositories or higher API limits.

`pr` returns normalized refs, mergeability, labels, reviewers, commit/change
counts, linked issue references found in the body, and a bounded changed-file
list. `issue` returns normalized issue metadata and body content. Bodies default
to 12,000 characters and PR file lists to 100 entries; the result reports when
either was truncated. Set `--file-limit 0` when a composition only needs PR
metadata and should avoid the extra changed-files API request.

For example, later review/check commands can compose with the same structured
PR result in a single invocation rather than requiring separate ad-hoc parsing.
The project roadmap is tracked in
[issue #1](https://github.com/arthexis/gway-repo/issues/1).
