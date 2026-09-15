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

For the common case, `context` packages the GitHub state and optional local
impact analysis into one bounded result:

```console
gway repo context --pr 916
gway repo context --issue 917
gway repo context --pr 916 --no-include-workflows
gway repo context --pr 916 --no-include-impact
gway repo context --pr 916 --file-limit 25 --thread-limit 20 --run-limit 10
```

Exactly one of `--pr` or `--issue` is required. PR context contains the
normalized pull request plus optional `reviews`, `checks`, `workflows`, and
`impact` sections. It resolves the PR once, reuses the head SHA for checks and
workflows, and passes the already-fetched PR payload into impact analysis rather
than making another PR metadata request.

The top-level PR summary surfaces mergeability, review decision, unresolved
threads, failed/pending checks, failed/running workflows, and compact impact
counts. Issue context uses the same envelope and can add aggregate impact across
PRs linked to that issue.

Live sections can be disabled independently with GWay's boolean flags. Use
`--no-include-impact` when a recipe only needs GitHub state or no suitable local
checkout is available. Local impact unavailability does not erase the ordinary
PR/issue context packet.

## Repository map

`map` provides a deterministic structural view of the local repository without
walking untracked files or build output:

```console
gway repo map
gway repo map --file-limit 100
gway repo map --refresh
gway repo files --kind test
gway repo files --kind workflow
gway repo files --extension py --package gway_repo
```

The map is built from Git's tracked index. Each file is classified as Python,
test, workflow, configuration, documentation, script, migration, asset, or
other. Python package roots are detected for conventional `src/` and flat
package layouts, Python module names are inferred from those roots, and simple
test-to-source relationships are reported when a filename has one unambiguous
match.

Results include HEAD and Git index tree SHAs, package/layout metadata, useful
repository roots, summary counts, bounded file details, blob sizes, and a
conservative generated-file flag. `files` queries the same structural data but
returns a bounded filtered view, avoiding the cost and output size of returning
the full map to recipes that only need one category.

Maps are cached locally in `.git/gway-repo/map.sqlite3` using the HEAD SHA and
index tree SHA. A staged structural change therefore invalidates the cache even
before it is committed, while untracked files remain intentionally invisible.
Use `--refresh` to rebuild the map explicitly. Cache failures are non-fatal, so
read-only or unusual Git environments can still build a map when possible.

## Python symbols

`symbols` adds AST-derived structure for the Python files identified by the
repository map:

```console
gway repo symbols
gway repo symbols --file src/gway_repo/context.py
gway repo symbols --kind function
gway repo symbols --name context_state
gway repo symbols --name gway_repo.context.context_state
gway repo symbols --package gway_repo --limit 100
gway repo symbols --no-include-imports
```

The result contains bounded symbol, import, and parse-error sections. Classes,
functions, and methods include qualified names and source ranges; callables also
include normalized signatures, return annotations, decorators, and an `async`
flag. Classes report bases and decorators. Import records preserve aliases,
relative-import levels, lexical scope, and their source module.

The Git index is authoritative for symbol parsing. `symbols` reads the tracked
blob with `git cat-file` instead of reading a possibly different working-tree
copy. An unstaged edit therefore does not silently change indexed symbols,
while staging a changed file produces a new blob SHA and reparses only that
blob. Parsed blobs are cached in the same local SQLite database used by the
repository map, keyed by immutable blob SHA and a symbol-cache schema version.

A syntax error in one tracked Python file does not abort the repository index;
it is returned in the bounded `parse_errors` section and other files continue
to index normally. `--refresh` bypasses both map and symbol cache reads.

The symbol index supplies definitions, imports, and cached AST call expressions
to the relationship-analysis layer described below. Dynamic Python behavior is
not guessed at the symbol-index boundary.

## Relationship analysis

`relations` turns the repository map and symbol index into a bounded, queryable
relationship graph:

```console
gway repo relations
gway repo relations --file src/gway_repo/context.py
gway repo relations --symbol gway_repo.context.context_state
gway repo relations --kind import
gway repo relations --kind call
gway repo relations --kind inheritance
gway repo relations --kind tested_by
```

The graph contains `defines`, `import`, `call`, `inheritance`, and `tested_by`
edges. Nodes represent tracked files, Python modules and symbols, plus external
imports when useful. Edges carry source/target paths, source lines, resolution
method, and confidence metadata.

Resolution is intentionally conservative. Direct lexical references and import
bindings that map unambiguously to indexed modules or symbols are marked exact.
Calls or inheritance targets that cannot be established statically remain
explicit unresolved edges instead of being guessed. This keeps downstream
agents and recipes able to distinguish knowledge from uncertainty.

Test relationships use two evidence levels. Imports and calls from test files
produce exact `tested_by` edges to production symbols. The repository map's
unambiguous filename convention can additionally produce file-level
`tested_by` edges with `likely` confidence.

`--symbol` returns both incoming and outgoing edges touching a symbol and puts
the corresponding counts in `summary`. `--file` returns relationships touching
a repository-relative file; `--kind` selects one relation type. `--edge-limit`,
`--node-limit`, and `--error-limit` bound output independently.

The relationship graph follows the Git index just like `map` and `symbols`.
Unstaged worktree edits remain invisible, staged changes invalidate the graph,
and completed graphs are cached in the existing local SQLite database by Git
index tree SHA. Use `--refresh` to bypass map, symbol, and relationship cache
reads.

## Issue-linked pull requests

`prs` discovers pull requests connected to an issue from bounded GitHub issue
timeline evidence:

```console
gway repo prs --issue 917
gway repo prs --issue 917 --limit 10 --event-limit 200
```

Closing references such as `Fixes #917` are reported as `closes` with exact
confidence. Explicit timeline connections are `connected`; ordinary PR
cross-references are retained as weaker `mentions` evidence. Duplicate evidence
for the same PR is collapsed to the strongest relationship. Cross-repository
PRs are preserved in the result rather than silently discarded.

## Change impact

`impact` consumes the map, symbol index, and relationship graph to answer what a
change can affect. Exactly one target is required:

```console
gway repo impact --file src/gway_repo/context.py
gway repo impact --pr 916
gway repo impact --issue 917
gway repo impact --pr 916 --depth 1
gway repo impact --issue 917 --pr-limit 10 --depth 2
```

A changed file seeds the file/module/symbol nodes represented in the current Git
index. Impact then walks resolved incoming `call`, `import`, and `inheritance`
edges up to `--depth`, reports dependent symbols, and collects `tested_by`
evidence plus test-origin dependency edges. Cycles are deduplicated and a visit
limit bounds traversal independently from changed-file, symbol, dependent,
test, and parse-error output limits.

PR impact uses the PR's bounded changed-file list and records the analysis basis:
the local analysis HEAD, index tree, HEAD tree, target PR head, and whether the
repository, head, and index match. If the checkout matches the PR head and the
index matches HEAD, the result is authoritative. A different PR head can still
be projected onto the current graph, but the packet explicitly marks that
projection non-authoritative instead of pretending it describes that historical
revision exactly. Deleted or otherwise absent paths are returned as
`unmapped_files`.

Issue impact first discovers connected PRs with `prs`, fetches bounded changed
files for same-repository PRs, and aggregates them into one deduplicated impact
packet. Changed symbols, dependents, and tests keep `pull_requests` provenance so
a caller can see which PRs contributed each result. Per-PR summaries are also
retained. Because one checkout usually cannot equal several historical PR heads,
issue impact explicitly reports whether every linked PR head matches the current
analysis graph. Cross-repository links are surfaced but are not guessed against
the local graph.

`context --pr` and `context --issue` include this impact packet by default when
requested local analysis is available. Use `--no-include-impact` for the cheaper
GitHub-only context path.

The project roadmap is tracked in
[issue #1](https://github.com/arthexis/gway-repo/issues/1).
