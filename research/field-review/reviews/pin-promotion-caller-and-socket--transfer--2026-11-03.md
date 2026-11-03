---
field: pin-promotion-caller-and-socket
angle: transfer
date: 2026-11-03
status: discarded
owner: health
---

## What we read

Mark S. Miller, Ka-Ping Yee, and Jonathan Shapiro, “Capability Myths Demolished” (2003), primary paper: https://papers.agoric.com/assets/pdf/papers/capability-myths-demolished.pdf. Read the sections “Achieving Confinement” and “Keys versus Objects”; Property F says object-capability delegation requires an access-controlled channel between the delegator and recipient.

## What this plant already does

`src/mishe_tauftauf/plant.py` gates runtime-source selection on session ownership, while the promotion shell invokes that CLI and checks the runtime result. The related critique review already identifies caller and socket selection as the promotion boundary.

## What is new here

Property F is a concrete limit on transferring the paper's object-capability model: the paper's confinement argument depends on inter-object messages traveling only along capabilities. A shell process invoking a CLI with a tmux socket path is not such a channel; the path is an OS resource designator, and the process boundary does not inherit object-capability delegation semantics.

## Discard

Discard: Property F's object-message confinement mechanism does not transfer to this shell/CLI boundary, so applying it as a promotion fix would claim a guarantee the transport does not provide.
