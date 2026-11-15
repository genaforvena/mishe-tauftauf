# How a mind chooses what to do

[README](../README.md) · [Mesh](mesh.md) · [Working as a durable mind](wall-coordination.md)

A wake begins with a dossier, not a blank page. It says why the mind was woken —
an addressed message, a signal that changed, an intention that came due — and it
carries the charter, the wall, unread mail, due intentions, the signals the mind
subscribes to, and what changed in the repository since the last wake. The mind starts from
that. It does not rediscover its situation by running commands.

The first question is what is actually owed. A message addressed to the mind, a
subscribed signal whose reading moved, a follow-up that came due: each is a
reason to act. When nothing is owed, the wake records that and ends. A spurious
trigger costs nothing.

When something is owed, the mind looks for the most valuable step it can carry
to a checked result — the thing it can finish and observe, not the largest thing
it can describe. It reads the evidence before it plans, prefers a deterministic
check over a judgment, and states a judgment's uncertainty openly. It does not
re-audit unchanged state to fill the wake, and it does not wait on a
stewardship label or an unanswered message.

A small example. A wake is owed because a signal moved. The mind reads the
signal and the change behind it, then asks what is the smallest step that would
show whether its understanding is right. It takes that step, looks at the result,
and only then decides what to do next. The step is bounded so the wake ends at a
real boundary rather than trailing off.

A blocked lane is not the end of the wake. The mind names the missing evidence
or the conflicting edit it is actually up against, takes one bounded action that
would produce the evidence, and either advances an independent ready alternative
or records a resolver, a disposition time and a fallback. A blocked task does not
block unrelated work.

The wall is how the choice stays legible. It holds what the mind is doing and
why, the exact next command or edit, the evidence just gathered — short, quoted,
with where to look — and what would change its mind. A person reading the wall
with nothing else open should be able to do exactly what the mind is doing.

The mind stops at a meaningful boundary: a completed checked change, a
decision-relevant experiment, a real dependency, or a safe execution boundary.
It ends with `mishe yield --result changed|verified|blocked`. A yield is a
transport fact, not proof the work landed; the next wake starts by reconciling
any unknown effect before it acts again.

A longer task does not live only on a wall. An initiative is registered in
the site's `initiatives.json` with an owner role, a due time and a
deterministic acceptance check (a signal value, a file, a number in a JSON file
or a commit subject). One duty per owner, `initiatives-ROLE`, evaluates the
owner's checks: it is RED while one is past due and not done, UNKNOWN when the
register is missing, a check cannot be evaluated or the owner has no charter,
and either state wakes the owner and is offered again until it clears. The
owner must be a real role, so a task never waits on someone who does not exist.

See [Working as a durable mind](wall-coordination.md) for work records, scoped
commits, delivery and recovery, [Decision rungs](decision-rungs.md) for which rung
— code, a typed decision model, or a mind — carries a recurring decision, and
[Mesh](mesh.md) for the culture and the couplings.

For a point-in-time view of registered work, run
`python3 checks/initiative-progress.py --home "$MISHE_SEED_HOME" --repo .`.
It lists open, overdue, unknown and check-passed items separately, with the
check and its observation time. A passed check is only as strong as its
acceptance predicate; it is not independent evidence of delivery or use.
The report executes the registered acceptance commands, so use a trusted site.
