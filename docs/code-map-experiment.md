# Code map experiment

This optional, read-only experiment finds Python functions and classes and Rust
functions whose name has no other syntactic identifier in the selected files.
Install with `pip install -e ".[code-map]"`, then run, for example:

```sh
python tools/code_map_experiment.py src/mishe_tauftauf tests tools rust
```

It prints the scanned scope, checkout revision, candidates and incomplete reads.
It reads working files, which can differ from the printed revision. Include all
relevant source, tests and consumers in the scope. The result is **not a call
or import graph**, does not trace from entry points and cannot establish that a
definition is dead or safe to remove. Dynamic lookups, configuration, Rust
macros and test discovery can invoke names with no syntactic reference. A
same-name identifier anywhere in scope also suppresses a candidate even if it is
unrelated. Parse errors and unreadable paths are UNKNOWN and cause a nonzero
exit. Check each candidate with wider searches and actual usage evidence.

The existing `tools/check_dangling.py` uses Python AST and symbol tables to
find unresolved names and imports; it answers a different question. In a
three-file pilot (`wall_patch.py`, `check_dangling.py`, and the Rust
`mishe-signal-freshness/src`), this experiment returned four candidates. Two
were Rust tests registered through attributes; `verified_delivery` had callers
outside that small scope; `_changed_names` had no other textual hit in the
repository at the time of the scan. None of these observations proves a safe
removal. This pilot supports trying cross-language syntax indexing, but does not
establish that Tree-sitter outperforms the existing AST tools or a text search.
