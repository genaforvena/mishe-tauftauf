---
field: pin-promotion-caller-and-socket
angle: recent
date: 2026-11-03
status: discarded
owner: health
---

## What we read

Read the current tmux primary manual at https://github.com/tmux/tmux/blob/master/tmux.1 (OpenBSD revision 1.1195, 2026-10-29), specifically its DESCRIPTION and `-L`/`-S` option sections. It states that clients and server communicate through a socket; `-L` chooses a server socket under `TMUX_TMPDIR` or `/tmp`, while `-S` names an explicit full socket path and bypasses the default socket directory.

## What this plant already does

`src/mishe_tauftauf/tmux.py` resolves a socket associated with this site, and the already-landed critique review `pin-promotion-caller-and-socket--critique--2026-11-01.md` identifies ambient `TMUX_TMPDIR` selection and proposes an explicit socket designator for pin promotion. The selector's keyword sweep also finds these socket and promotion boundaries.

## What is new here

The current manual confirms the existing distinction between an environment-selected socket directory and an explicit socket path. It introduces no distinct application to pin promotion beyond the already-landed reviews of this cell.

## Discard

No distinct recent result remains to apply: the current tmux manual restates the explicit `-S` versus environment-derived `-L` distinction already covered by the landed pin-promotion reviews.
