---
field: recovery-observations-and-sessions
angle: mechanism
date: 2026-10-31
status: applied
owner: health
target: src/mishe_tauftauf/tmux.py
---

## What we read

**Crash-Only Software** — George Candea and Armando Fox, Stanford University. *Proceedings of HotOS IX*, USENIX Association, May 2003, pp. 67–72.

- <https://www.usenix.org/legacy/events/hotos03/tech/full_papers/candea/candea.pdf> (the PDF was read page by page from a local copy; the web fetch of it returned raw bytes, so the quotes below come from the page images, not a summary)

The operational mechanism is in Section 2 and Section 3. Section 2 defines the stop and start equations: "a crash-only system is defined by the equations stop=crash and start=recover". Each crash-only component "has a single idempotent 'power-off switch' and a single idempotent 'power-on switch'", and "a component's power-off switch implementation is entirely external to the component, thus not invoking any of the component's code and not relying on correct internal behavior of the component." Examples the paper gives are `kill -9` sent to a UNIX process, or turning off a virtual machine.

Section 3 states the enabling conditions: "all important non-volatile state be kept in dedicated state stores", the stores "be crash-only", and components "tolerate the crashes and temporary unavailability of their peers". The recovery code is kept honest by being run on every start: "recovery code is exercised every time the system starts up, which should ultimately improve its reliability."

**Stated limits, from the paper itself.** Section 3 says "some of these properties may not be necessary for crash-only behavior", so the properties are a sufficient set, not a proven minimum. The paper is a position paper: its only numbers are Table 1 reboot timings on three commodity systems, with no measurement of a session supervisor. It addresses component crashes in a stateless-request Internet service, not long-running interactive processes that must survive their own supervisor.

## What this plant already does

The stop switch is external and idempotent in the common path. `stop()` at `src/mishe_tauftauf/tmux.py:300` returns early when `has-session` fails and otherwise runs `kill-session` after `owns_session()`. The start path is `session_runner.raise_session()` (`src/mishe_tauftauf/session_runner.py:110`). Recovery state lives in files and locks under the site home, not inside the mind process.

The plant has already handled the one gap the paper's external switch leaves open. A mind's process tree can outlive `kill-session`, so the reaper walks it before killing and then `SIGKILL`s the survivors: `_pane_descendants()` (`tmux.py:316`) reads the tree while the pane still lives, and the reaper at `tmux.py:386–392` kills the session and then each child. The reaper only covers sessions named `mishe-tauftauf-test-<pid>-…`; the production `stop()` does not kill descendants at all.

The design is deliberate. The docstring at `tmux.py:318–322` says a seed mind "is started in its own session so closing a pane cannot SIGHUP an in-flight mind, so that child outlives a leaked `kill-session`." So "a power-off switch that kills everything" is the opposite of what the plant chose on purpose, and this review does not propose reversing that.

## What is new here

The paper's contract is that the external switch is sufficient: after `stop`, nothing the component started is still running. The plant's contract is weaker and undocumented. `stop()` ends the session, and whatever the mind spawned may keep running as an orphan. The reaper enforces full reaping only for test sessions, so a leaked production child can survive a stop with no sensor reporting it.

Proposed application, to be read by health and not applied from here: make the survivor count observable, not the kill. After `stop()` in production, `_pane_descendants()` read before the kill should be compared with the surviving pids after it, and the difference reported as a named observation (for example `sense.session.stop-survivors`) with count and pids. Live kill is not proposed. This follows the paper's own check, that the switch be external and its effect verifiable, while keeping the in-flight-child design at `tmux.py:318–322`.

Prediction: on the current host, the survivor count after a normal stop is zero, because minds exit on the session hangup. If it is not zero, the paper's sufficiency condition does not hold here, and the surviving children are the thing to study. Keep/revise/revert: keep the observation if it reads zero over a week of stops, revert if it adds a sense that fires on the deliberate detached child.

Limits: the observation is read-only and does not change any stop outcome. The paper does not show the survivor count is the right health measure; it is a hypothesis to measure.

**Routing caveat.** `src/mishe_tauftauf/tmux.py` currently has an uncommitted 19-line insertion from another role's checkout. This review does not touch that file. Health should coordinate with the owner of that diff before any edit.

## Application

target: src/mishe_tauftauf/tmux.py
