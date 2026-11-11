# Per-component deploy

Status: the mechanism works and has been exercised on the live site. `components.py` and the `mishe component` commands exist (see "Who promotes"), and four components have been promoted and verified there. What is still missing is the duty detector that keeps them watched: no `components` signal is registered, so a component that goes RED after a promotion is not reported to anyone. Promotion touches the delivery mechanism, so it keeps its separate fresh review.

## Problem

Every delivery moves one pin, `SITE_HOME/runtime/current`, that serves all code at once. A delivery holds and freezes all wakes for roughly 8 to 20 minutes, so a change to one leaf tool stalls every mind. The pin is one release, so a leaf change cannot ship without the core.

## Layout

Leaf components (first cut: the Rust executables and `tools/` scripts) get their own pointer:

```
SITE_HOME/components/NAME/
  releases/SHA/          immutable bytes: bin/NAME (or tools/NAME), CONTRACT.md, check.py
  current -> releases/SHA   one symlink, swapped by atomic rename of a temp link
```

- Consumers call `components/NAME/current/bin/NAME`, a stable path, never a release path. This is the intended end state; no consumer calls it yet (see "Order of work").
- The core (scheduler, dispatch, settlement, locking, delivery) stays on `runtime/current` with the strong gate and fresh review. It is not a component.

## Rules

1. **One way only.** Once `components/NAME/current` exists, `runtime/current` stops serving NAME's files. No shadow copy, no fallback to the pin for that NAME. This is the rule the code enforces for the pin (`verify` reports RED when NAME is still served from `runtime/current/bin`), but the site's own `$SITE/bin/NAME` install is a second copy that the detector does not look at.
2. **Promotion** is atomic: build the release dir, run its check against those bytes, create `current.tmp` beside `current`, then `rename(2)` over `current`. No global hold; wakes keep running.
3. **Rollback** is re-pointing `current` at the previous release SHA with the same rename. The previous SHA is recorded in the promotion record before the swap.
   If post-swap verification is RED and no previous SHA is known, the new pointer remains in place and the result is `unknown`, not a rollback. With a previous SHA, automatic rollback is reported as `rolled-back` only if the restored pointer verifies GREEN; otherwise it is `unknown`. Inspect the recorded problems and live pointer before recovery.
   If an existing release fails its manifest check, promotion builds and checks replacement bytes first, then preserves the invalid release under `releases/.corrupt-SHA-TIMESTAMP` before installing the replacement. The release path may briefly be unavailable during replacement; a failed installation attempts to restore the invalid directory.
4. **Contract.** Each component's `CONTRACT.md` names its inputs, outputs and exit codes. Its check runs against the release dir, not the working tree.
5. **Docs-only commits** need no promotion.

## Detector

A component is RED when its `current` pointer and its files disagree: pointer missing, target missing, target's bytes not matching the recorded sha256 of `bin/NAME`, or a file for NAME still served from `runtime/current` after the pointer exists. The intended detector runs with the duty loop and reports through a `components` signal. That signal is still not registered: `mishe component verify` is the only check, and nobody runs it on a schedule, so a component that goes RED after its promotion stays RED until a reader looks. Missing or stale detector output is UNKNOWN, never GREEN.

The command itself is exercised. `mishe component verify` on the live site reports all four promoted components GREEN, and it fails closed: with no components it exits 3, and any component that is not GREEN also exits 3.

## What has actually happened

The promotions below ran on the live site, in the site's `components/` tree, and the ledger under each component records them. They are the evidence that the mechanism works, not a plan.

- `mishe-tape-facts` was promoted at `034db9df`, then at `a2b7d182`, then rolled back to `034db9df`, then promoted again at `0c0d7e6e`. The rollback and the re-apply are in the ledger, so the revert path has been walked on real bytes rather than only in a test.
- `mishe-tape` was promoted at `a2b7d182`, then at `2b5d7ea1`.
- `mishe-network-probe` was promoted at `6989d18f`, then at `2f1a2d4f`.
- `mishe-records` was promoted at `cc071b62`.

`mishe component verify` reports all four GREEN, and the bytes it checks are the bytes the site runs: the sha256 of `components/NAME/current/bin/NAME` equals the sha256 of the `$SITE/bin/NAME` install and the one recorded in that component's `manifest.json`.

The promotion code was reviewed independently before that, in two passes. The first found one real defect: a promotion that came out RED when there was no previous release reported `rolled-back` even though nothing had been rolled back. That was repaired, and the second pass, again against real bytes in a temporary site, found the failure and rollback semantics correct as written — including that a release which verifies RED after the swap with no previous release known is reported as `unknown`, with the pointer left where it is rather than moved.

## Who promotes

Genome owns the promotion command and the check it runs. The command exists in
`src/mishe_tauftauf/components.py`:

```
mishe component promote NAME SHA --check-file FILE [--repo PATH]
mishe component rollback NAME
mishe component verify [NAME]
```

`promote` builds from the committed SHA (a full commit in the repository),
checks the release and only then moves `current`. A failed build or check leaves
`current` untouched. Genome wrote the command; rust-plan has run it, with
genome's blessing, for the components above. Authors ask genome to promote, or
run the command themselves. Authors do not write `current` by hand.

## Order of work

Steps 1 and 2 are done: the promotion code was reviewed independently before the first promotion, and `mishe-tape-facts` was promoted. Step 3 is not.

1. ~~Fresh review of the promotion code and the detector (delivery-class).~~ Done: the review found one confirmed defect (a first promotion that came out RED claimed a rollback it had not performed) which was repaired before the next promotion, and a later pass found the failure and rollback semantics correct as written.
2. ~~Promote `mishe-tape-facts` as the first component.~~ Done, twice over, and the rollback path was walked on the live release between them.
3. **Open: switch a consumer to the component pointer.** The consumers do not use it yet. `feed.py` and `tape_facts.py` read `$SITE/bin/NAME` and check its sha256 against the install record beside it; no code path reads `components/NAME/current/bin/NAME`. Until one does, a promoted component and the binary the plant actually runs are two separate copies that happen to hold the same bytes today, and nothing keeps them equal.
4. **Open: prove the revert path through the real caller.** The rollback was exercised on the live release, but not through a consumer, so the end-to-end path is still unobserved.
5. **Open: wire the detector to a duty.** Without a `components` signal, a RED component is not routed to a resolver. That is the missing piece that makes the rest safe.
6. Then `tools/` scripts such as `camera_signature`.

## Open limits

- Rollback restores the previous bytes only; it does not undo side effects a component already wrote.
- `mishe-tape` is read by `feed.py` through `$SITE/bin/mishe-tape` and its install record today. Moving that path is part of step 3, not a separate change.
- The install record and the component manifest record the same digest in two places. Until step 3 lands, nothing checks them against each other, so a promotion and a later hand-installed binary can drift apart silently.
