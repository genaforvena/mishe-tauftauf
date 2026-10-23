# Functional recovery

[Mesh](mesh.md) · [Operating the plant](operating.md) · [Wall coordination](wall-coordination.md)

A recovery incident belongs to a failing function. Investigation, a message,
a settlement and a restart do not close it. Its executable probe must pass
repeatedly across the declared stability interval; a missing sample breaks that
interval. Temporary relief is **MITIGATED**, with the causal issue still open.
Failed, missing and stale observations remain visible.

The optional recovery runtime samples Linux memory and I/O pressure without
reading the conversation or taking its shared locks. It also checks immediate
seed-lock availability and an independently scheduled freshness guard. The lock
probe is narrow: acquiring the lock does not prove an actual wake can settle.
Additional read-only functional probes can be configured with an argument list,
timeout, owner and backup. Removing existing coverage is refused.

Both plants can share a host-capacity controller and workload slice. On this
31 GiB, 16 CPU host the combined workload uses MemoryHigh=20 GiB,
MemoryMax=24 GiB, MemorySwapMax=2 GiB and at most eight CPUs. Resident
supervisors and the tmux servers that fork model processes belong to that
slice. Existing tmux servers outside it are refused; a keeper declaration alone
does not establish containment. Recovery sampling, its independent guard and
publication run outside the workload slice with their own bounded budgets.

The controller samples host pressure and cgroup memory/events every five
seconds. High usage reduces optional observer services and renderer admission;
critical usage or a new cgroup OOM closes rendering. Reopening requires two
minutes of fresh, continuously clear samples. Historical OOM counters remain
in evidence without permanently asserting current pressure. Missing, stale or
malformed producer/guard evidence remains UNKNOWN. An action failure cannot
reopen rendering during critical pressure.

Both sites configure the same absolute `host_root` in their private recovery
configuration. The shared policy admits at most four optional renderer children
normally, one under reduced or unknown capacity, and none during critical
pressure. Role cooldowns avoid repeated immediate renders. Locks remain held
by each child's independent supervisor until its process group is cleaned up,
including when the calling wrapper dies. Deferred or failed rendering returns
UNKNOWN with retained content and its original timestamp.

Optional service stop intents and results are journaled independently of chat.
A queued job is not a completed stop or start. The controller restores only
services whose stop it confirmed; uncertain interrupted stops require
reconciliation. Restoration ownership persists until the service is observed
active, with a 30-second deadline for missing restoration. Failures route to
Health and Genome rather than disappearing behind a fresh sampler.

The host sampler can be inspected through its reviewed package:

```sh
python -m mishe_tauftauf.host_capacity --root "$MISHE_SEED_HOME/recovery" status
python -m mishe_tauftauf.host_capacity --root "$MISHE_SEED_HOME/recovery" guard
```

Feed parsing avoids whole-tape Unicode copies. Witness verifies all referenced
records but keeps only fields used by its anomaly checks; ordinary full-evidence
projections retain their complete records. These changes address measured memory
amplification, while the resource budget contains future growth.

At an incident's configured deadline the controller requests its backup. That
request is not acceptance: the backup must explicitly acknowledge before the
resolver changes:

```sh
python -m mishe_tauftauf.recovery_io --home "$MISHE_SEED_HOME" accept \
  --function host-pressure --owner genome
```

Acknowledgement names responsibility, not repair. The backup must inspect the
retained observations and effects, take a bounded action within existing scope,
and check the function. Never replay an unknown mutation or remove a live lock
to clear an alert.

Immutable transition evidence and a durable outbox live under
`SITE_HOME/recovery/`. Publication runs separately with a 45-second command deadline, a 60-second
service deadline, a 1536 MiB budget and idempotent event content. A blocked chat path cannot prevent sampling or admission, and one
failed notification cannot starve later events. Diagnosis receipts do not
suppress functional incidents.

Install from a reviewed snapshot:

```sh
python tools/recovery-control.py --home "$MISHE_SEED_HOME" prepare
python tools/recovery-control.py --home "$MISHE_SEED_HOME" activate
python tools/recovery-control.py --home "$MISHE_SEED_HOME" observe
python tools/recovery-control.py --home "$MISHE_SEED_HOME" verify
```

Recovery activation wraps the selected site's top-pain executables. Shared host
containment additionally requires both sites' declared controller configuration,
workload-unit drop-ins and verified tmux descendant cgroups. Source and main
runtime pins remain separate. Exact-byte backout refuses changed peer files;
`revert` stops only the new recovery services/timers and restores the original
renderers. Stopped recovery remains in existing detector coverage. Original
evidence and snapshots are retained.

Evaluation runs after one hour and then every 24 hours. It compares pressure and
seed-lock availability with the captured baseline and publishes an artifact to
Health, Genome and Witness through the outbox. Missing current monitoring or
interrupted samples makes its verdict UNKNOWN. A comparison is uncontrolled
before/after evidence, not proof of causation or of actual settlement recovery.
Commit, message and token counts are not recovery scores.

The initial controller covers pressure, lock availability and guard freshness.
Publication, deployment and provider restoration need their own functional
probes and reviewed actions; this runtime never stages peer work, advances the
main runtime pin or changes models automatically.
