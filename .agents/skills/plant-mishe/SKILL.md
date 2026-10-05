---
name: plant-mishe
description: Use when planting or repairing a resident mishe development culture in this checkout or another owned Git worktree.
---

# Plant a development culture

Read this repository's `AGENTS.md`, `README.md`, and `src/mishe_tauftauf/seed_doctrine.md`. Before changing a target, confirm it is a direct owned Git worktree, separate from this checkout when planting an external project; inspect its site `.seed-raised` marker, tmux session ownership, and linked user-service `FragmentPath` values. Preserve any existing target work and unrelated services.

From this repository, run `python3 -m coordination.launcher --workspace TARGET --engine-command 'codex'`. Omit `--workspace` to tend this checkout. The command creates an ignored site inside the target, the `genome`, `witness`, `discover`, `senses`, `health`, `docs`, `research-methods`, `permissions`, and operator windows, an initial discovery and CI reading, and persistent user services. `--no-services` is for a supervised trial. Existing charters, mind launchers, handoffs, and top programs are preserved.
OMP and Codex idle prompts are detected directly. For another agent command, add an executable `SITE/checks/mind-ready/ROLE` for each resident role; it must exit zero only when the bottom pane accepts a full prompt. It receives `MISHE_SEED_SESSION`, `MISHE_SEED_ROLE`, and `MISHE_SEED_PANE`. An absent or failing probe holds wake delivery, so verify this gate with the actual launcher before claiming a plant is live.

The plantable core is `src/mishe_tauftauf/`. Host commands live together in `coordination/` and load this checkout's core without a separate launcher script or package install. Plant this core checkout first. Its persistent install adds a coordination service that follows fresh green CI readings. A later persistent external plant registers its owned site and session in the core site's ignored `health/linked-sites.json`; no coordinator service is installed in the target. Verify the core coordination unit is active and the registry row names the correct site, session, and applied SHA. The coordinator updates a target only when both worktrees are clean and CI is fresh and passing for the exact local core `HEAD`; it then verifies feed, panes, and services and records a result or retryable hold in core `chat.log`. Run `python3 -m coordination.site_sync --home CORE_SITE` to retry immediately. A generated target `AGENTS.md` change is a target genome task to review and land, never a reason to copy application code from the core.

Keep target-specific chat, requests, charters, plans, checks, handoffs, artifacts, drafts, and service units in the ignored site. Put reusable fixes and tests in the target's tracked source. Before a commit, inspect the exact diff and confirm `git ls-files SITE` is empty. Set target-specific top checks for the actual build, CI, deployment, and data surfaces; make missing or stale evidence `UNKNOWN`.

Inspect every live top and lower pane after planting. Verify required windows, active services, a fresh CI reading or an honest `UNKNOWN`, a real discovery sample, and an advancing top-pane lease. Follow one wake through artifact, exact yield, idle clear, and restored handoff. If an existing plant has obsolete windows, remove only identified superseded windows after replacements are live. If the current target has a failing CI run, record the run URL in `chat.log`, route a genome task, and verify the replacement run after landing a repair.

When handing back, record the target and site paths, session, window names, service states, open task IDs, and any unknown or failing check.
