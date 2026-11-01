---
field: pin-promotion-caller-and-socket
angle: transfer
date: 2026-11-01
status: applied
owner: health
target: src/mishe_tauftauf/tmux.py
---

## What we read

Primary source, read this turn:

- Linux manual page `openat2(2)`, the kernel interface for resolving a path relative to a held directory handle with resolution limits: https://man7.org/linux/man-pages/man2/openat2.2.html

Quoted from that page:

- `dirfd`: the path "is interpreted relative to the directory referred to by the file descriptor dirfd". The name is resolved against a handle the caller already holds, not against whatever the ambient environment points at.
- `RESOLVE_BENEATH`: "Do not permit the path resolution to succeed if any component of the resolution is not a descendant" of the starting directory. This is containment checked during resolution, so a symlink or `..` cannot step out of the root.
- `RESOLVE_NO_MAGICLINKS`: "Disallow all magic-link resolution during path resolution."
- `RESOLVE_IN_ROOT`: "Treat the directory referred to by dirfd as the root directory while resolving path."

A second source was tried and not used: the Capsicum paper (Watson et al., USENIX Security 2010), at its usenix.org PDF URL. The fetch returned only the compressed binary, so no quotation from it is claimed here.

Plant text read beside it, this turn:

- `src/mishe_tauftauf/tmux.py:40-57`: `_socket_for_home` builds the socket path from `TMUX_TMPDIR` when the caller sets it, otherwise from `/tmp`. The value is a plain string. It is not checked for containment.
- `src/mishe_tauftauf/tmux.py:57-75` (`_socket_candidates`): the candidates are the ambient-derived socket, the default socket, the site socket, and then every subdirectory of `/tmp/tmux-UID` that holds a `tmux-UID/default`. The list is built from names and `iterdir()` entries. Nothing is resolved to a canonical path or checked to stay beneath the uid's tmux root.
- `src/mishe_tauftauf/tmux.py:83-101` (`owned_socket`): a candidate is returned only when its recorded owner equals this home. The write-side docstring already says writers act on the returned socket and never on the ambient one.

## What this plant already does

The plant already has the designation rule that the misread and critique reviews asked for. `owned_socket` returns the socket that records this home as owner, and writers such as `engine_rotation.py:389`, `model_change.py:246` and `view.py:75` act on that return value. The plant also already tests the foreign-`TMUX_TMPDIR` case (`tests/test_tmux.py:783-830`).

What the plant does not do is check where a candidate path lives. The owner test is a string match on a value read from the server. Whether the socket is under the uid's tmux root is never asked. The `openat2` semantics are about exactly that second question: a name is only accepted if its resolved location stays inside a root the caller already trusts.

## What is new here

A containment rule, borrowed from `RESOLVE_BENEATH` and `RESOLVE_NO_MAGICLINKS`, applied at the point where `_socket_candidates` admits a path. Concretely:

1. Canonicalise each candidate with `Path.resolve()`, which plays the role of `RESOLVE_NO_MAGICLINKS` here: a symlink planted under `TMUX_TMPDIR` or under `/tmp/tmux-UID` cannot redirect the probe to another tree.
2. Admit a candidate only when the resolved path is a descendant of `/tmp/tmux-UID` (the uid's tmux root). Drop it otherwise, and record the dropped path in the probe's reason so the refusal is visible rather than silent.
3. Do not let the ambient `TMUX_TMPDIR` widen the root. The root is fixed by the uid, not by the caller's environment.

Prediction: on a host with a symlink under the uid tree, or a caller `TMUX_TMPDIR` outside it, `owned_socket` currently may return a socket outside the tree and then the writer acts on it. After the change it returns None and the caller reports the drop. The observation that would refute the prediction is a test where an in-tree socket with a matching owner is still refused, or a case where the current code returns the socket and the new code does not, in a setup a real site produces.

The claim is narrow. This does not by itself fix the 12:54 promotion refusal. That refusal comes from `owns_session` at `plant.py:583`, which the critique and misread reviews already address. The containment rule is a second, independent guard on the socket that writers are handed.

## Application

target: src/mishe_tauftauf/tmux.py

Suggested shape for the owner: in `_socket_candidates`, canonicalise each path with `resolve()` and keep it only if `resolve()` stays under `/tmp/tmux-UID`. Add tests for (a) a symlink under the uid tree pointing outside it, (b) a foreign `TMUX_TMPDIR` outside the uid tree, and (c) an in-tree owned socket that is still found. Run the existing `test_owned_socket_*` and `test_session_owner_*` tests unchanged. The change is in the owner's organ, so health should land it, with genome checking the exact-SHA CI.

Routing: this review is addressed to health with the commit as evidence. It is a proposal only. Field-review did not edit `tmux.py`.
