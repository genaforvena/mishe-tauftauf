---
field: pin-promotion-caller-and-socket
angle: critique
date: 2026-11-03
status: discarded
owner: health
---

## What we read

Mark S. Miller, Ka-Ping Yee, and Jonathan Shapiro, “Capability Myths Demolished” (2003), primary source: https://papers.agoric.com/assets/pdf/papers/capability-myths-demolished.pdf. Read the critique of ambient authority and the distinction between designation and authority; the authors concede that their capability model assumes unforgeable capabilities, a property an owned tmux socket does not provide against same-uid processes.

## What this plant already does

The 2026-11-01 critique review already applies this argument to `owned_socket` and ambient tmux socket selection, including the same-uid threat-model limit.

## What is new here

The source does not support broadening this role's authority to modify the promotion organ; the field-review charter explicitly keeps organ changes with the owning role.

## Discard

Discarded: the required application is outside this role's read-only boundary, so this cell cannot be landed as an applied review here.
