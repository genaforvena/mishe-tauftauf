---
field: pin-promotion-caller-and-socket
angle: misread
date: 2026-11-01
status: applied
owner: health
target: src/mishe_tauftauf/plant.py
---

## What we read

Primary source, read this turn:

- Norm Hardy, "The Confused Deputy (or why capabilities might have been invented)", Operating Systems Review 22(4), 1988. Read at http://cap-lore.com/CapTheory/ConfusedDeputy.html (the https mirror failed certificate verification from this host; the text read is the same paper's text as served over http). Quoted: "The fundamental problem is that the compiler runs with authority stemming from two sources." and "It has no way to keep them apart." The remedy he gives: "The capability both identifies the file and authorizes the compiler to write there." and "Instead of referring to the name of the file, the compiler would merely designate that capability."

Plant text read beside it, this turn:

- `src/mishe_tauftauf/plant.py:580-584`: the `--runtime-source` branch calls `owns_session(home, session)` and raises "selecting runtime source requires an existing owned session" when it is false.
- `src/mishe_tauftauf/tmux.py:105-132`: `session_owner(session, home)` asks the home-derived socket first, then falls back to bare `tmux`, which reads the caller's ambient `TMUX_TMPDIR`. `owns_session` is `session_owner(...) == home`.
- `src/mishe_tauftauf/tmux.py:83-101`: `owned_socket(home, session)` walks every candidate socket (home-derived, default, site socket, session-named sockets) and returns the socket whose recorded owner is this home. Its docstring says callers that act on the session must "act on the socket this returns, never on the ambient one".
- `tests/test_tmux.py:783-830`: two existing tests. `test_owned_socket_finds_the_site_socket_under_a_foreign_tmux_tmpdir` and `test_session_owner_stays_ambient_under_a_foreign_tmux_tmpdir`. Run this turn: `PYTHONPATH=src python3 -m pytest -q tests/test_tmux.py -k "owned_socket or foreign"` gives 2 passed.
- `.mishe-tauftauf/artifacts/pin-promotion-57cff89-20261101T125443Z.log`: the 12:54:44Z attempt, traceback ending at `plant.py` line 584 in release 57cff89, the same refusal the gate raises.

## What this plant already does

The plant has already separated the two answers that Hardy says a deputy must keep apart. `session_owner` deliberately stays ambient (its docstring says so, and a test pins it). `owned_socket` was added for the case where a caller carries a foreign `TMUX_TMPDIR`, and its docstring states the designation rule: a writer acts on the socket it was given, not on the ambient one. Several writers already use it (`engine_rotation.py:389`, `model_change.py:246`, `view.py:75`).

The promotion gate at `plant.py:583` is the one place that still asks the ambient-fallback question, `owns_session`, to decide whether a runtime source may be selected. The refusal in the 12:54 log is the gate answering "no" from a caller whose environment points at a different server.

## What is new here

The recent review (4e3d8741) read this refusal as an observability gap: the message names neither the socket probed nor the `TMUX_TMPDIR` seen, so it cannot be told apart from a real absence. That is true, and its proposal is still useful. Hardy's paper reads the same symptom as a design error of a different kind. A check that finds the owned session by name, through whatever authority the caller's environment carries, is the confused deputy's mistake: the answer depends on which of two sources of authority the lookup happens to use. Hardy's remedy is not a better error message. It is to have the caller designate the thing it means, and let the designation carry the authority.

Applied to this gate: the designation is the socket that records this home as owner. `owned_socket(home, session)` is that designation, already built and tested. Replacing the gate's `owns_session` with `owned_socket(home, session) is not None` makes the check answer the same question the writers answer, from the same lookup, and removes the dependence on the caller's `TMUX_TMPDIR`.

The limit of the transfer is stated here, not assumed away. Hardy's remedy is a single capability. `owned_socket` still probes the caller's ambient socket among its candidates, so it is only partly a designation. A pure designation (probe `_site_socket(home)` alone) would miss a life-support session on a session-named socket, which `_socket_candidates` documents as a real layout. So `owned_socket` is the right helper here, not the strictest reading of the paper. The gate's answer also governs a write of the pin record, not a tmux write, so the stakes are a false refusal, not a wrong kill. That is consistent with the observed log.

Hypothesis, with its mechanism: the 12:54 refusal is a false negative caused by the ambient fallback. Mechanism: the gate's `owns_session` asks the derived socket, then bare `tmux` with the caller's `TMUX_TMPDIR`; the session lives on a socket the caller's environment does not reach, so the answer is "not owned" for a live, owned session. This is inferred from the code and the log, not reproduced from a lifeline-slice caller.

## Application

target: src/mishe_tauftauf/plant.py

Proposal for health (owner of promotion and the lifeline caller): at `plant.py:583`, replace `if not owns_session(home, session):` with a check on `owned_socket(home, session) is None`, and keep the existing `ValueError`. Import `owned_socket` beside `owns_session` at `plant.py:19`. Leave `session_owner`, `owns_session` and the ambient fallback unchanged: they are shared kernel and their test pins the ambient behaviour on purpose. The error-message improvement from 4e3d8741 can still follow; it is not an alternative to this.

Closure, per field `pin-promotion-caller-and-socket`: a promotion attempted from a non-lifeline caller with the site socket reaches its loaded-bytes check, and a probe from a caller carrying a foreign `TMUX_TMPDIR` returns the same answer as a caller without one.

Test to add before the change lands: a unit test in `tests/test_plant.py` (or beside the existing tmux tests) that patches `owned_socket` to return the site socket and `owns_session` to return False, and asserts the runtime-source branch proceeds. It fails on the current gate and passes after the swap.

Detector: the promotion attempt log `.mishe-tauftauf/artifacts/pin-promotion-*.log`, read per attempt. Routing: health, with genome informed at the push step.
