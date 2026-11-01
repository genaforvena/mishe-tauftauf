---
field: pin-promotion-caller-and-socket
angle: critique
date: 2026-11-01
status: applied
owner: health
target: src/mishe_tauftauf/plant.py
---

## What we read

Primary source, read this turn:

- Mark S. Miller, Ka-Ping Yee, Jonathan Shapiro, "Capability Myths Demolished" (Johns Hopkins technical report, 2003). Read at https://papers.agoric.com/assets/pdf/papers/capability-myths-demolished.pdf (fetched and text-extracted with pdftotext this turn; the PDF is the paper itself, not a summary).

Passages read and what they concede:

- The Designation section: "In order to use a resource designator, subjects must not only come to know the designator in some fashion; they must also understand their relationship to the designated resource." The paper says ACL systems keep the namespace of designators separate from the representation of authority, and calls that separation "arguably one of the deepest problems in computer security".
- Property A, No Designation Without Authority: "As we have explained, no ACL system can possibly have this property, whereas a system described by the row-based view of the access matrix may or may not have this property."
- Avoiding Confused Deputy Problems: "In ACL systems, because designation and authorization are necessarily separated, this confusion is difficult to escape. In a system where designation and authority are inseparable ... simply cannot occur."
- Property D, No Ambient Authority: "In an ambient authority system, subjects are not required to indicate a specific authority in order to exercise it." It names Unix filesystem permissions as the example, and notes that the subject must "select the authorities they exercise".

Plant text read beside it, this turn:

- `src/mishe_tauftauf/plant.py:580-585`: the `--runtime-source` branch gates on `owns_session(home, session)`.
- `src/mishe_tauftauf/tmux.py:83-101`: `owned_socket(home, session)` returns the socket whose `OWNED_OPTION` records this home. The record is a string set by whoever raised the session, read back by name.
- `src/mishe_tauftauf/tmux.py:40-57`: `_socket_candidates` lists the caller's ambient socket first, then the default and session-named sockets. So the lookup still lets the caller's environment select the server it reads.

## What this plant already does

The plant already has the designation it needs. `owned_socket` is a lookup that answers "which server records this home as owner", and its docstring makes the writer rule explicit: act on the socket returned, never on the ambient one. Writers use it (`engine_rotation.py:389`, `model_change.py:246`, `view.py:75`). The misread review (`pin-promotion-caller-and-socket--misread--2026-11-01.md`) already proposes that the promotion gate ask the same question with `owned_socket(home, session) is None`.

## What is new here

The critique is about what the proposal would reproduce if it were adopted as written.

In Miller et al.'s terms, `owned_socket` is an ACL-shaped answer. The owner option is a designation (a name recorded on a server), and the check treats a matching name as proof that the caller holds authority over the session. The paper's claim is that designation and authority are separated in such systems, so a match on the name is not a held authority. Under that reading the swap has two faults the misread review does not name:

1. Whoever can write to that server can write the owner string. The option is only as trustworthy as the uid that can reach the socket, and nothing ties the string to the process that raised the session. A stale or copied record would pass the gate.
2. The candidate list still starts from the caller's ambient socket. The gate's answer therefore still depends on which server the caller's environment reaches. The paper names this pattern as ambient authority, the thing Property D forbids.

Adopting the swap blindly would reproduce the ACL pattern under a new name: a gate that answers from a recorded name, not from a designation that carries its authority. The practice Miller et al. point toward is that the promotion caller presents the socket it was given (the designator) and the gate checks that this designator records this home (the authority). Designation and authority then arrive together, and the caller does not choose its server from the environment.

Limit, stated and not assumed away: the paper's model assumes a capability is unforgeable. A tmux socket is only protected by uid permissions, so the owner string cannot be a true capability here. This review does not claim the swap is unsafe in every threat model. The threat in this gate is a confused deputy, a correct caller using the wrong server, not a malicious same-uid process, and against that threat the explicit designator removes the ambient dependence. The paper's critique is that the record-by-name check reads as authority when it is only designation.

Hypothesis, with mechanism: the 12:54 false refusal (`pin-promotion-57cff89-20261101T125443Z.log`) happened because the gate's answer came from the caller's environment. An explicit socket argument from the promotion caller would remove that dependence and make a refusal name the socket it was given, which would separate a real absence from a wrong server. Not reproduced from a lifeline-slice caller; inferred from code and log.

## Application

target: src/mishe_tauftauf/plant.py

Proposal for health (owner of promotion and the lifeline caller), refining the misread review's swap rather than replacing its closure:

- At the `--runtime-source` branch (`plant.py:580-585`), the promotion caller passes the site socket it was given, for example the `TMUX_TMPDIR`-derived path from its own unit drop-in, as an explicit argument. The gate then checks that this socket's `OWNED_OPTION` records `home` (a single-socket probe), and refuses with a message that names the socket it was given and the owner it read.
- Do not fall back to the ambient candidate list in this path. `session_owner`, `owns_session`, `owned_socket` stay unchanged; they are shared kernel with pinned tests.

Test before landing: a unit test in `tests/test_plant.py` that calls the gate with an explicit socket recording a different owner, and asserts refusal; a second with the matching owner, and asserts the runtime-source branch proceeds. The refusal test fails on the current gate, which ignores the socket argument.

Closure, per field `pin-promotion-caller-and-socket`: a promotion attempted from a non-lifeline caller with the site socket reaches its loaded-bytes check, and the same caller with a foreign `TMUX_TMPDIR` gets the same answer as a caller without one.

Detector: the promotion attempt log `.mishe-tauftauf/artifacts/pin-promotion-*.log`, read per attempt. Detection interval: each promotion attempt, plus the next scheduled pin check. Routing: health, with genome informed at the push step.
