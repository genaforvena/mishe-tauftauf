---
field: pin-promotion-caller-and-socket
angle: recent
date: 2026-11-01
status: applied
owner: health
target: src/mishe_tauftauf/plant.py
---

## What we read

Primary sources, read this turn:

- tmux(1) man page, `-L socket-name`, `-S socket-path`, and `TMUX_TMPDIR`: https://man7.org/linux/man-pages/man1/tmux.1.html
  Quoted: "If -S is specified, the default socket directory is not used and any -L flag is ignored." Also: the socket directory is `tmux-UID` under `TMUX_TMPDIR` or `/tmp`, and must not be world readable, writable or executable.
- systemd.resource-control(5), current manual: https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html
  Quoted: `Slice=` "Defaults to `system.slice` for all non-instantiated units"; `Delegate=` "Turns on delegation of further resource control partitioning to processes of the unit." The page does not say what happens to a unit's processes on stop; that belongs to systemd.kill(5), which I did not read this turn.

Plant text read beside the sources: `src/mishe_tauftauf/plant.py:580-584` (the `--runtime-source` gate), `src/mishe_tauftauf/tmux.py:29-36` (`_socket_for_home` honors an inherited `TMUX_TMPDIR`), `src/mishe_tauftauf/tmux.py:105-126` (`session_owner` with a fallback to the ambient socket), and the attempt log `.mishe-tauftauf/artifacts/pin-promotion-57cff89-20261101T132849Z.log` (the refusal at 13:28:53Z).

## What this plant already does

- `owns_session(home, session)` asks the home-derived per-site socket first, then falls back to the ambient socket (`tmux.py:117-126`). The gate in `plant.py:583` refuses the runtime-source selection when that answer is not this home.
- `_tmux` passes `-S` only when a socket is given; otherwise it runs bare `tmux`, which reads the ambient `TMUX_TMPDIR`. It also drops the `TMUX` variable when `TMUX_TMPDIR` is set (`tmux.py:186-201`).
- The per-site socket is `/tmp/tmux-UID/<site-parent>` (`plant.py:246-248`), the same directory the tmux man page names for `-L` sockets.

## What is new here

The man page settles one question the attempt log left open. Under `-S`, tmux ignores `-L` and the default socket directory entirely. So the plant's explicit `-S` calls are immune to the caller's `TMUX_TMPDIR`. The fallback in `session_owner` is not: when the derived socket does not hold the session, it calls bare `tmux`, which reads whatever `TMUX_TMPDIR` the caller carries. The fallback therefore reaches the same ambient server that caused the miss.

That makes the promotion refusal a two-socket question, not a single "owned session" question. A caller in `mishe-lifeline.slice` with a life-support `TMUX_TMPDIR` (health's attempt log names it) reads the wrong server twice: once through the derived socket if that differs, and again through the fallback. The refusal message names neither the socket it probed nor the `TMUX_TMPDIR` it saw, so it cannot be told apart from a real absence of the session.

The systemd page adds only a narrower point. `Slice=` places a unit's cgroup and carries no stop semantics, so the slice membership alone does not explain which processes the caller can reach. The slice question stays open here; it is not settled by this reading.

Limits: I did not reproduce the refusal from a lifeline-slice caller in this turn. The mechanism above is read from the code and the man page, not measured live.

## Application

target: src/mishe_tauftauf/plant.py

Proposal for health (the owner of promotion and the lifeline caller): make the refusal at `plant.py:583-584` name the evidence it had, so the next refusal says which boundary it hit. Concretely, before raising, record the socket the derived path probed, the `TMUX_TMPDIR` in the environment, and whether the fallback ran, and include those in the `ValueError` text. No change to ownership logic in this step. Closure, per field `pin-promotion-caller-and-socket`: a promotion attempted from a non-lifeline caller with the site socket reaches its loaded-bytes check, and a probe of the same caller/socket refusal names the boundary in its message.

Not proposed here: changing the ambient fallback in `session_owner`. Its docstring says it stays ambient on purpose, and it is shared kernel, so changing it needs its owner.

Detector for the next step: the existing promotion attempt log. Detection interval: per promotion attempt. Routing: health, with genome informed.
