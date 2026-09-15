# gway-repo

Repository introspection utilities for GWay.

The first capability is a compact, structured description of the current Git
repository. The return value is intentionally a regular Python mapping so GWay
can retain it as a chain result, pass values to later commands, or serialize it
at an output boundary.

```console
gway repo info
gway repo info --path ../another-repository
```

`info` reports the repository root, current branch, HEAD commit, configured
remotes, primary remote, GitHub `owner/repo` name when recognizable, and the
best locally-known default branch. Repository discovery does not require a
network request.

The project roadmap, including one-line composition and recipes for gathering
PR/issue context, is tracked in
[issue #1](https://github.com/arthexis/gway-repo/issues/1).
