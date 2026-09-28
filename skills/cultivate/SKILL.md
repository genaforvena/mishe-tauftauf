---
name: cultivate-mishe-tauftauf
description: Use in a clone of mishe-tauftauf when someone names real work they want a durable observation → disposable Mind → checked result loop to do. Cultivate only the needed workspace and windows; do not plant the separate mortal baby-mesh demo.
---

# Cultivate one responsibility

This is the **full-core deployment** path, not `mishe-mishe-to-tauftauf`'s no-clone, disposable demonstration. The former grows around a named piece of real work; the latter must still be removable with `mishe burn`. Do not replace its mortality promise with this persistent core or run both boards on an existing mesh. An LTE installation is one possible deployment, not a prerequisite or a template to copy.

## 1. Contract before machinery

Use the existing human channel. Ask for only facts unavailable in the workspace: **what result would be useful**, which repository/data may be used, and which effects the person authorizes. Read the named repository and current state rather than asking for its contents. Freeze a short `$SITE/plans/<slug>.md` containing:

- their request verbatim, repository path and immutable base revision (or source digest), permitted read/write paths, and who owns landing;
- one consumer-observable desired state and the command that checks it, including failure/UNKNOWN behavior and its data freshness;
- engine actually available, tool and network/data boundaries, expected spend, and whether any send, publish, deletion, account, credential, or external action is **separately authorized**;
- one rollback/retry edge and a human-readable result artifact path.

Do not invent a problem or acceptance. If a provider or shell can write outside the workspace, say so: `--cwd`, a prompt, and a disposable Git worktree are **not an OS sandbox**. For untrusted code or real consequences use an enforced sandbox with only the intended writable mounts, or keep the Mind tool-free and have the authorized owner apply the reviewed result. Ambiguous ownership, privacy, irreversible effects, or a missing real check means UNKNOWN and no live dispatch. Do not touch another repository because it happens to be reachable.

Compare three options before building: a deterministic script with no Mind, a bounded one-shot Mind, and a continuous loop. Choose the first that meets the named acceptance. Do not write a rule, hook, schedule, task ledger, or second channel for a fact a script can decide. No pretrained model is an authorization gate.

## 2. Plant just this observation

Use the cloned core's stdlib entry point; no `mesh-*`, LTE card, Ollama, credential path, cron, or package-manager action is required. Let `CORE` be this clone and `WORKSPACE` the named, preferably isolated repository. A site under the workspace makes the core's launcher-supplied `MISHE_TAUFTAUF_WORKSPACE` equal that workspace; keep it out of commits. Choose a new slug from the real responsibility (lowercase letters, digits, hyphens), not a global fixed roster.

```sh
CORE="$(pwd)"                         # run in the mishe-tauftauf clone
WORKSPACE=/absolute/path/to/authorized/workspace
SITE="$WORKSPACE/.mishe-tauftauf"
PYTHONPATH="$CORE/src" python3 -m mishe_tauftauf --home "$SITE" init
```

Create **one** executable `$SITE/top-pains/<slug>` that reads the *actual* source and reports the desired state, evidence identity/freshness and a specific RED or UNKNOWN. No hard-coded success or default 0 on failed reads. Create `$SITE/projectors/<slug>` only after inspecting the pane's private fields: its output is bounded, redacted public feed text; the full live pane can still reach the selected judge transiently. If no safe projection exists, do not connect a hosted judge. An optional filter may quiet identical observations but may never hide RED/UNKNOWN. Test both a real failure and a real success on the same check; a `--test` of the hook alone is not a real-world artifact.

Record the failing-before state **before** asking a Mind to act:

```sh
PYTHONPATH="$CORE/src" python3 -m mishe_tauftauf --home "$SITE" check <slug> -- <real-check-command> [args...]
PYTHONPATH="$CORE/src" python3 -m mishe_tauftauf --home "$SITE" pain render <slug>
```


Select an explicit judged route before live dispatch: use a probed `--judge /absolute/path/to/executable` or a working local Laya installation, exercise positive and negative startup controls, and inspect their receipts. With neither available the core returns UNKNOWN judgments; that is not a calibrated eligibility decision. A deterministic judge is appropriate only where its predicates actually decide the named state; otherwise supply a real semantic adapter with an approved data boundary. Use the same judge on subsequent runs; changing the adapter changes the decision contract.

`check` returns the observation path, not the probed command's status; inspect that file's `exit`, stdout and stderr. If the real state is already satisfied, do not fabricate a repair. If no tmux exists, use headless mode; do not install tmux silently. When visual windows are wanted, `tmux start --session <unique-owned-name>` creates only windows for installed Top Pains plus the core's observability pane and refuses an unowned session. Never attach to or kill a pre-existing shared tmux session.

## 3. One Mind, one result
Create executable `$SITE/minds/<slug>` for the **probed** harness CLI. It consumes invocation context on stdin, operates only under the frozen contract, and exits. `examples/omp-mind.py` is a starting adapter for an installed OMP, not a sandbox; probe `omp --help` and bound its tools/cwd/time for this deployment. Other engines need their own checked one-shot invocation rather than guessed flags. The Mind must read its current Top Pain and mission, write a checked artifact, and before exit call `mishe-tauftauf handoff <slug> <result-file>` with the launcher-provided invocation environment. A process exit, diff, or feed receipt does not satisfy the request.

First run once, not as a daemon:

```sh
PYTHONPATH="$CORE/src" python3 -m mishe_tauftauf --home "$SITE" run --once --slug <slug> --launcher headless --judge <checked-judge-path>
PYTHONPATH="$CORE/src" python3 -m mishe_tauftauf --home "$SITE" pain render <slug>
PYTHONPATH="$CORE/src" python3 -m mishe_tauftauf --home "$SITE" doctor
```

Re-run the *same consumer check* against the actual output, inspect the artifact and its exact source identity, then compare it with the user's acceptance. If no useful result, say what failed rather than enlarging authority. A local patch is not a landed or published change: the domain's single writer reviews and lands it, and must reconcile commit/effect and canonical task state before retry after a crash. A protected external effect requires point-of-risk confirmation from the human even when a model requested it. Never let the Mind complete somebody else's task or publish on its own.

Cold-start another coordinator process from the same site and handoff. Check unchanged input produces no redundant Mind/effect, changed input gets its own identity, and missing/torn evidence remains UNKNOWN. Test a crash between the effect and its receipt *in the relevant domain sink* before claiming exactly-once effects. A completed Mind with a future prediction must not self-wake just because it wrote a handoff; an overdue unresolved prediction must still wake. Count model invocations, elapsed time, missed work and duplicate effects, not only green tests.

## 4. Keep, grow, or stop
Show the real result where the person looks, with a live Top Pain. Ask whether it was useful or wrong; record their response verbatim, or honestly say feedback is pending. Only after a successful bounded loop and a decision to keep it, use a supervised `run --follow` at a cadence justified by the observation's age and one owner per effect. Prove the supervisor really runs and the pane ages into UNKNOWN when its source stops. No blind cron line. To add another named repository, freeze a new scope, slug, observation, owner and authorization; do not silently broaden the first Mind's filesystem or turn the public core into LTE's task ledger.

To stop, turn off **only** this site's owned coordinator and, if used, `tmux stop --session <unique-owned-name>`; that command refuses an unowned session. Preserve source code and evidence. Removing the site or undoing a landed effect is a separate, scoped action with its own custody/rollback check; there is deliberately no `mishe burn` promise here. Report the checked artifact, observed pane, model calls/cost, unresolved obligations and the exact next check. The first useful artifact is the work itself, never this skill or a setup receipt.
