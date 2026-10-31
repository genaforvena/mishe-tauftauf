---
field: recovery-observations-and-sessions
angle: critique
date: 2026-10-31
status: discarded
owner: genome
---

## What we read

Martin Kleppmann — **"How to do distributed locking"**, blog essay, 8 February 2016, a critique of Redlock.

- <https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html> (fetched 2026-10-31; read by summary extraction of the page, not a saved copy)

The passages this review rests on:

1. **Lease expiry does not stop a paused client.** The essay says a lock with a timeout "is a lease, which is always a good idea", and that a client paused by a GC pause longer than the lease "may not notice its lease has expired" and writes anyway. Its conclusion: "You cannot fix this problem by inserting a check on the lock expiry just before writing back to storage."
2. **The repair is a fencing token checked by the storage.** "a fencing token is simply a number that increases", and the storage server must reject any write whose token is lower than one it has already processed.
3. **What it concedes.** "In a reasonably well-behaved datacenter environment, the timing assumptions will be satisfied most of the time." It still holds that "as soon as those timing assumptions are broken, Redlock may violate its safety properties."

Limits the source states and this review keeps: the essay is about mutual exclusion of writes to shared storage by clients that hold a timed lease. It says nothing about a read-only observation, a presentation lease, or a process that holds a kernel lock. It is a critique of one lock design, not a general theory of failure detection.

## What this plant already does

The concern is whether any *write* in this plant is gated by a *timed* lease that could expire under a paused holder. Three mechanisms were checked in the tree at `42168c1f`.

- **The seed lock has no expiry.** `src/mishe_tauftauf/seed.py:255-258` takes `fcntl.flock(handle.fileno(), fcntl.LOCK_EX)`. A kernel `flock` is released when its holder's process exits, not when a clock passes. A paused holder keeps the lock; it cannot write while another holder also writes. The Kleppmann hazard needs an expiring lease, and this lock does not expire.
- **The push guard is a compare-and-swap on the base SHA, not a timed lease.** `src/mishe_tauftauf/delivery.py:217` pushes with `--force-with-lease=refs/heads/main:<base>`. The remote refuses the push unless `main` is still at the recorded base. That is the same shape as a fencing check: a stale holder's write is rejected by the storage side, not by a timer in the client.
- **The timed lease gates a read, not a write.** `src/mishe_tauftauf/tmux.py:473-481` reads a pane's `-- pane live` lease twice in bounded windows and returns `HOLD pane-fallback ... has no owned lease` when the lease does not advance. The hold stops a presentation fallback. It writes nothing that a paused holder could later commit, so a lease that expires silently under a GC-like pause produces a stale *reading*, which the plant already marks UNKNOWN (`tmux.py:283`).
- **The containment latch is a single-sample verdict, not a lease.** This is the real open item, and it is already covered by the misread review of the same cell (`recovery-observations-and-sessions--misread--2026-10-31.md`, applied to `host_containment.py`). It concerns Chandra-Toueg suspicion, not fencing.

## What is new here

Nothing that this cell's critique adds to the plant. The essay's hazard is a write that trusts an expired lease. The plant's writes are either serialized by a kernel lock that cannot expire, or guarded by the remote's own compare-and-swap, and its timed lease only gates a read. Proposing a fencing token for `seed.lock` would add a counter where `flock` already gives mutual exclusion, and a fencing token for the tmux lease would protect a read that writes nothing. Both would be changes made because a text names the pattern, not because a fault was observed.

## Discard

The critique does not bind here: the plant's writes are not gated by an expiring lease (`seed.py:255-258` flock; `delivery.py:217` force-with-lease on base; the timed lease at `tmux.py:473-481` gates only a read). The one live question is the single-sample containment latch, already routed by the misread review of this cell.
