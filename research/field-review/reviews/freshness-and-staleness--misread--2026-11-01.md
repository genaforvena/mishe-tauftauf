---
field: freshness-and-staleness
angle: misread
date: 2026-11-01
status: applied
owner: docs
target: docs/research-methods.md
---

# Freshness, revisit: a lease check guards liveness, the wake number guards safety

## What we read

**How to do distributed locking** — Martin Kleppmann, blog post, published 08 Feb 2016.
<https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html>. Read as the full
article fetched 2026-11-01. The passages that bear on this plant:

1. **Process pauses and lease expiry.** A client holding a lease "is paused for an extended
   period of time while holding the lock". If the pause outlasts the lease, the client may not
   realise the lock has expired and can make an unsafe change. The article's key sentence:
   "You cannot fix this problem by inserting a check on the lock expiry just before writing back
   to storage." A GC pause can land "at _any point_", including after the last check. The
   article's conclusion on pauses is "Your processes will get paused."

2. **Fencing tokens.** "A fencing token is simply a number that increases", issued by the lock
   service each time a client acquires the lock. The article is explicit about who must enforce
   it: "requires the storage server to take an active role in checking tokens." The storage
   server has already processed a write with token 34, so it rejects a late write carrying
   token 33.

3. **Why the lock alone is not enough.** Without a token, "you cannot prevent the race condition
   between clients in the case where one client is paused or its packets are delayed." Only the
   resource that receives the write can see that a token is stale.

This is a second reading in the same field. The first review in this field (2026-10-22, misread
angle) read Gray & Cheriton, 1989, for the server-issued lease term and proposed replacing
wall.tick's hard 30 s gate. That proposal is not yet applied: `src/mishe_tauftauf/wall.py:1226`,
`:1317` still read `if not 0 <= age <= 30`. This review does not repeat it.

## What this plant already does

* **The plant has a fencing token, and it is the wake number.** `wall.settle`
  (`src/mishe_tauftauf/wall.py:900`) receives `wake`, takes the seed lock, and compares it with
  the pending wake: `if pending != wake:` (line 914). A settle for a wake that is already settled
  returns `already settled` (line 930), and a wake that is not pending raises `is not pending`
  (line 931). The resource that accepts the write, here the seed state, is the party that checks
  the number. That is Kleppmann's rule (point 2), applied to the settle path.

* **The seed lock is a local `flock`, not a lease with an expiry.** `seed._lock`
  (`src/mishe_tauftauf/seed.py:256`) takes `fcntl.flock(LOCK_EX)` on `.seed.lock`. The kernel
  releases it when the holder dies, and nothing expires while the holder is alive. The
  expiry-versus-pause race in point 1 has no lock-side analogue here.

* **The pane lease is used as a liveness gate, not as a lock.** `wall.tick` (line 1317) and
  `wall._live_sensor` (line 1226) check the pane footer's age before a wake or an observation.
  The check is followed by `read(home, role)`, a digest, a feed append and the delivery. That is
  check-then-act with a gap, which is the shape Kleppmann says cannot be made safe by checking
  expiry alone. The gap is harmless here, for a reason the article does not cover: what the gate
  protects is not a shared resource that a late write could corrupt. The consequence of a pause
  inside the gap is an observation that describes a pane a few seconds older than the check, or
  a wake delivered to a pane that stopped a moment earlier. The plant does not rely on the gate
  for safety. Safety rests on the wake number at the settle.

* **The tracked docs already state the liveness-only reading.** `AGENTS.md:13` says "tmux and its
  lease establish presentation liveness"; `docs/research-methods.md:25` says "The window's lease
  proves presentation liveness, not scientific acceptance." (The local glossary at
  `docs/how-it-works.md:27` says the same, but that file is gitignored and not on the branch.)
  Neither sentence says which mechanism carries safety.

## What is new here

**The misread is not that the plant treats its lease as a lock. It does not.** The misread is
narrower: a reader of `wall.tick` could take the 30 s pane-lease gate for a safety guard, because
it sits on the wake path and it refuses work, the way a lock refuses a second writer. The
mechanism that prevents a stale action from landing is the wake number checked by the resource
(`settle`, line 914), not the lease age checked by the holder-side gate. Kleppmann's point 3 is
the test: the lease gate cannot stop a paused caller, and the plant's safety does not depend on it
being able to.

Nothing in the docs names this split. A future change could move a safety decision onto the pane
lease, for example by letting a settle or a clear trust the footer age instead of the wake number,
and the existing prose would not flag it.

This review does not claim the plant is unsafe. It does not claim that wall.tick's 30 s gate is
correct; that is the 2026-10-22 review's question and stays open there.

## Limits

* The article is an argument plus one worked example, not a measurement. The fencing argument
  assumes a resource that can compare tokens; the plant's settle does, because it reads its own
  state under the flock.
* The plant's writes are local-host writes under one flock. Kleppmann's distributed case, with
  clocks and network delay between separate lock service and resource, does not exist on one
  host. This review does not extend the argument to the cross-host `site_sync` path.
* Whether the docs should say this is a judgement about what a reader needs to know. The
  proposal is one sentence.

## Application

target: docs/research-methods.md

Add one sentence after the existing lease sentence (`docs/research-methods.md:25`), stating the split:

> A lease gate is a liveness check: it can refuse a stale pane before a wake, but it cannot stop a
> paused action from landing after the check. Safety comes from the wake number that `settle`
> checks under the seed lock, so a settle for a wake that is not the pending one is refused by the
> resource, not by the holder's own clock.

Acceptance: the sentence names `settle` and the wake number as the safety mechanism, and the lease
gate as liveness only. No code changes. The text is checked against `wall.settle` at
`src/mishe_tauftauf/wall.py:914` before it lands.

Routing: this is a docs change, so the owner is the docs role. The 30 s gate question stays with
the 2026-10-22 review and its owner, genome.
