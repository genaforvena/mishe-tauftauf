---
field: pin-promotion-caller-and-socket
angle: mechanism
date: 2026-11-03
status: discarded
owner: health
---

## What we read

Primary source: systemd 262 `systemd-run(1)`, https://www.freedesktop.org/software/systemd/man/latest/systemd-run.html. The manual says `--scope` runs the command as a child of `systemd-run` and inherits the caller's execution environment; `--slice=` places the transient scope or service in the named slice. Read the description and the `--scope`, `--slice=`, and `--user` options.

## What this plant already does

`src/mishe_tauftauf/plant.py` checks that the runtime-source request targets an owned session before calling `select_source`. The 2026-11-01 mechanism review separately proposes checking the promoting process's actual cgroup slice before changing the pin. Neither check treats slice membership as proof of tmux-socket authority.

## What is new here

A transient scope can give the promotion command explicit resource placement, but systemd-run's documented environment inheritance means this does not remove ambient caller state such as `TMUX_TMPDIR`; slice placement is not an authentication or socket-designation boundary. The manual supplies no mechanism for binding the selected tmux socket to the authorized caller.

## Discard

Discarded: slice placement does not bind promotion authority to the owned tmux socket or remove ambient socket selection.
