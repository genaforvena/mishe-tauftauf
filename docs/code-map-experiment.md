# Code map experiment

This optional, read-only experiment finds Python functions and classes and Rust
functions whose name has no other syntactic identifier in the selected files.
Install with `pip install -e ".[code-map]"`, then run, for example:

```sh
python3 tools/code_map_experiment.py src/mishe_tauftauf tests tools rust
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
Directory traversal does not follow symlinked directories; supply their targets
explicitly when they belong in the scope.

The existing `tools/check_dangling.py` uses Python AST and symbol tables to
find unresolved names and imports; it answers a different question. In a
three-file pilot (`wall_patch.py`, `check_dangling.py`, and the Rust
`mishe-signal-freshness/src`), this experiment returned four candidates. Two
were Rust tests registered through attributes; `verified_delivery` had callers
outside that small scope; `_changed_names` had no other textual hit in the
repository at the time of the scan. None of these observations proves a safe
removal. This pilot supports trying cross-language syntax indexing, but does not
establish that Tree-sitter outperforms the existing AST tools or a text search.

## Rooted name-reference experiment

To explore a supplied entry point, pass its exact printed file and definition
line as a root (repeat `--root` for more roots):

```sh
python3 tools/code_map_experiment.py --root rust/mishe-signal-freshness/src/main.rs:31 rust/mishe-signal-freshness/src
```

For each definition, the experiment follows identifiers inside it to every
same-named definition in the scanned files of the same language. It prints one
predecessor for each `POSSIBLE` path; this is a name match, not a resolved call.
An `UNKNOWN` definition has no path found from the supplied roots, not a finding
that it is unreachable. Roots are assumptions: configuration, imports, module
initializers, test discovery, dynamic dispatch, Rust macros, and external or
unscanned consumers can add entry points or references. Nested definitions,
unrelated attributes, and same-named symbols can add false paths. This is not
a sound or complete call graph. Unreadable files, parse errors, missing roots,
or ambiguous root labels produce UNKNOWN and a nonzero exit. Working-tree bytes
can differ from the displayed commit. The command-line decoder is isolated in
`tools/code_map_args.py`, the only newly registered parsing boundary; it reads
only this experiment's options and paths and grants no runtime authority.

On the 4b0c52dd checkout, the Rust example produced possible name paths from
`main` to `parse_float` and `expired`; its two attribute-registered tests stayed
UNKNOWN, as they should when only `main` is supplied. With
`--root tools/check_dangling.py:74 tools/check_dangling.py src/mishe_tauftauf/wall_patch.py`,
it found the names of the three helpers called by `main`, but also followed
`ast.walk` to the unrelated `wall_patch.walk` and onward to `target`. That
counterexample shows why name paths require inspection. Python's standard AST
and symbol tables already expose scopes, imports and syntax without optional
dependencies; `check_dangling.py` uses them to check unresolved Python names,
not reachability. Tree-sitter's demonstrated advantage here is a common syntax
interface for the Rust and Python samples, not better resolution or a measured
speed or accuracy advantage over AST or `rg`.

The `_changed_names` wrapper remains UNKNOWN. Repository search found only its
definition, and commit `1c62d1f6` replaced its visible `symbol_context` call
with `_definition_diff`; that is a plausible leftover, not proof that reflective
or external use is absent. Do not remove it on this experiment alone.
