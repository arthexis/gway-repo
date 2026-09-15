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
Git remote. Public REST endpoints can be queried anonymously. Set `GH_TOKEN` or
`GITHUB_TOKEN` to authenticate for private repositories, higher API limits, and
GraphQL review-thread resolution state.

`pr` returns normalized refs, mergeability, labels, reviewers, commit/change
counts, linked issue references found in the body, and a bounded changed-file
list. `issue` returns normalized issue metadata and body content. Bodies default
to 12,000 characters and PR file lists to 100 entries; the result reports when
either was truncated. Set `--file-limit 0` when a composition only needs PR
metadata and should avoid the extra changed-files API request.

## Reviews, checks, and workflow state

```console
gway repo reviews 3
gway repo reviews 3 --unresolved-only
gway repo checks 3
gway repo workflows 3
```

`reviews` returns review submissions and a compact review decision. When a token
is configured it also queries GitHub GraphQL for authoritative review-thread
resolution state; `--unresolved-only` requires that authenticated GraphQL path.
Thread and review bodies are bounded independently.

`checks` resolves the PR head SHA and returns normalized check runs, legacy
commit statuses, failed check names, pending check names, and bounded check
summaries. Pass `--sha` when a previous command in a chain already resolved the
head and the extra PR metadata request should be avoided.

`workflows` reports Actions runs for the PR head plus bounded job state. Failed
jobs include the names and conclusions of failed steps without downloading or
dumping full workflow logs. `--run-limit` and `--job-limit` bound the result;
`--sha` similarly avoids another PR lookup when the head is already known.

The low-level operations remain independently composable:

```console
gway repo pr 916 --file-limit 0 - repo reviews 916 --unresolved-only - repo checks 916 - repo workflows 916
```

## Unified context

For the common case, `context` packages those primitives into one bounded
result:

```console
gway repo context --pr 916
gway repo context --issue 917
gway repo context --pr 916 --no-include-workflows
gway repo context --pr 916 --file-limit 25 --thread-limit 20 --run-limit 10
```

Exactly one of `--pr` or `--issue` is required. PR context contains the
normalized pull request plus optional `reviews`, `checks`, and `workflows`
sections. It resolves the PR once and reuses the head SHA for checks and
workflows, avoiding redundant PR metadata requests.

The top-level `summary` surfaces the fields most likely to need attention:
mergeability, review decision, unresolved-thread count, failed/pending checks,
and failed/running workflows. Each detailed section stays available underneath
for callers that need more than the summary.

The three live PR sections can be disabled independently with GWay's boolean
flags when a recipe needs a cheaper packet. All underlying limits remain
available on `context`, so callers can control bodies, changed files, reviews,
threads, checks, workflow runs, and jobs.

Issue context uses the same stable envelope but only includes the normalized
issue section. File and symbol context are intentionally reserved for the later
repository-map and symbol-index milestones.

The project roadmap is tracked in
[issue #1](https://github.com/arthexis/gway-repo/issues/1).
