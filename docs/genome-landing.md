# Genome landing and outside edits

Genome owns assessment as well as delivery. A dirty path does not need to have been created by mesh to deserve attention.

Genome inspects outside edits with `python -m mishe_tauftauf.landing_debt --home SITE --repo CHECKOUT audit --intake`; the shipped genome pane does not run it automatically. It reports exact working/index signatures, first-observed age, ownership, whether the working bytes already match origin/main, and checkout distance from that locally known ref. Fetch origin before preparing a candidate; the audit never performs network operations or alters source/index bytes.

Unclaimed, changed-claim, stale, closed-task and already-published drafts are RED. Claimed fresh drafts are AMBER; no dirt is GREEN. Missing origin or failed measurement is UNKNOWN. The default stale threshold is 24 hours since first observation, retained across edits. Reports and claims live under the ignored site landing-debt directory. The initial scan cannot reconstruct historical age.

The audit creates one stable genome intake task and registers its landing priority. Repeated scans do not create duplicate tasks or rearm consumed/waiting attempts. Closing intake with unresolved debt reopens it. Current reports continue updating while the task's original capture remains immutable; the mind must compare both before mutation.

For each path, genome assesses need against current source and tests. It adopts useful edits into an isolated current-origin candidate; routes shared edits to an existing owner with an actionable step; reconciles already-published residue; or archives and retires superseded edits with evidence. Unknown ownership is an investigation step. It is not an indefinite external blocker.

Claim inspected draft bytes with:

```sh
python -m mishe_tauftauf.landing_debt --home SITE --repo CHECKOUT claim \
  --task TASK --owner genome --next-step 'prepare candidate and focused checks' PATH...
```

A claim requires a live task belonging to that role, and changes to either working or staged bytes invalidate it. The audit performs no cleanup. Preserve a backup and all unrelated/newer edits before reconciling an old source checkout. Never restore old HEAD bytes over a published draft. Delivery proceeds through focused checks, an accounted-for commit by the author on the single `main` branch, a genome push to `main`, exact-SHA CI, deployment and live consumers, followed by draft reconciliation. Runtime GREEN does not clear source debt.


## One dashboard, three consumers

`pain watch` composes the complete report once per refresh and atomically publishes SITE/dashboards/ROLE.json before printing the same frame to tmux. The mind reads `mishe-tauftauf --home SITE pain read ROLE --launcher dashboard`; the supervisor reads that same full frame. Terminal wrapping and scrollback do not enter the observation digest. Missing, corrupt, mismatched, future, or more-than-30-second-old dashboard snapshots are UNKNOWN, with no terminal-capture fallback. Tmux session ownership, pane health and a fresh visible lease still prove presentation liveness separately.

Unchanged scans never rearm an intake. A changed checked source-debt fingerprint makes an idle intake actionable again; active wakes retain their reservation. A closed task's claim can be adopted by a new live owner through the canonical claim command, while a live owner's claim remains protected.
