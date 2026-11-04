---
name: cultivate-mishe-tauftauf
description: Use in a clone of mishe-tauftauf when someone names real work they want a durable observation → woken Mind → checked result loop to do. Cultivate only the needed workspace, signal and role; do not plant the separate mortal baby-mesh demo.
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

The runtime has no third-party dependencies and needs no install: `./bin/mishe` runs from the clone. Let `CORE` be this clone and `WORKSPACE` the named, preferably isolated repository; a site under the workspace stays out of commits. Choose a new slug from the real responsibility.

```sh
CORE="$(pwd)"                         # run in the mishe-tauftauf clone
WORKSPACE=/absolute/path/to/authorized/workspace
SITE="$WORKSPACE/.mishe-tauftauf"
export MISHE_SEED_HOME="$SITE"
"$CORE/bin/mishe" init
"$CORE/bin/mishe" hold on --reason "cultivating"
```

The observation is a **producer**: a command that reads the *actual* source and exits 0 for the desired state, 2 for a specific RED, and anything else when it cannot tell (a crash or timeout leaves the signal to age to UNKNOWN; there is no hard-coded success). Declare it in `$SITE/producers.json` with its `every`, `max_age` and the one role that resolves it, then register the role by writing `$SITE/charters/<slug>.md` (the real responsibility, its limits and the frozen contract's path).

Record the failing-before state **before** asking a Mind to act:

```sh
"$CORE/bin/mishe" signal run <slug> --timeout 60 -- <real-check-command> [args...]
"$CORE/bin/mishe" signal show <slug>
```

If the real state is already satisfied, do not fabricate a repair. The engine is per role in `$SITE/engines/<slug>.json` (`claude` or `omp`); probe the harness CLI you will use and bound its tools in that file. An engine with a shell can write outside the workspace.

## 3. One Mind, one result

Run once by hand, not as a daemon, with the hold released only for this role:

```sh
"$CORE/bin/mishe" inbox touch <slug>
"$CORE/bin/mishe" hold off
"$CORE/bin/mishe" wake <slug> --repo "$WORKSPACE"
"$CORE/bin/mishe" signal show <slug>
```

A wake that ends without `mishe yield` is an unknown effect; read `$SITE/wakes/` and `$SITE/dispatch/<slug>.json` before another attempt. Re-run the *same consumer check* against the actual output, inspect the artifact and its exact source identity, then compare it with the user's acceptance. If there is no useful result, say what failed rather than enlarging authority. A local patch is not a landed or published change: the domain's single writer reviews and lands it, and reconciles commit and effect before any retry after a crash.

Check that unchanged input wakes nothing (a refreshed `.seen` is not an edge), that changed input gets its own wake, and that a missing or torn reading stays UNKNOWN. Test a crash between the effect and its receipt *in the relevant domain sink* before claiming exactly-once effects.

## 4. Keep, grow, or stop

Show the real result where the person looks and ask whether it was useful or wrong; record their response verbatim, or say feedback is pending. Only after a successful bounded loop and a decision to keep it, `mishe sync --repo "$WORKSPACE" --pythonpath "$CORE/src"` installs the path and timer units, at a cadence justified by the observation's age and with one owner per effect. Prove they really run: stop the producer and watch the signal age to UNKNOWN and wake its resolver.

To stop, `mishe hold on` and, if installed, `mishe sync --uninstall`; the latter removes only generated units. Preserve source code and evidence. Removing the site or undoing a landed effect is a separate, scoped action with its own custody and rollback check. Report the checked artifact, the signal states, the model and engine used, the cost recorded in `$SITE/wakes/`, and anything unverified.
