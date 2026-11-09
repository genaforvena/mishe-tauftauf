# Per-component deploy

Status: design, approved in direction by the operator on 2026-11-08. This document makes no code, unit or pin change. `components.py` and the `mishe component` commands exist (see "Who promotes"); the duty detector and first promotion are not yet observed. Promotion touches the delivery mechanism and needs fresh review.

## Problem

Every delivery moves one pin, `SITE_HOME/runtime/current`, that serves all code at once. A delivery holds and freezes all wakes for roughly 8 to 20 minutes, so a change to one leaf tool stalls every mind. The pin is one release, so a leaf change cannot ship without the core.

## Layout

Leaf components (first cut: the Rust executables and `tools/` scripts) get their own pointer:

```
SITE_HOME/components/NAME/
  releases/SHA/          immutable bytes: bin/NAME (or tools/NAME), CONTRACT.md, check.py
  current -> releases/SHA   one symlink, swapped by atomic rename of a temp link
```

- Consumers call `components/NAME/current/bin/NAME`, a stable path, never a release path.
- The core (scheduler, dispatch, settlement, locking, delivery) stays on `runtime/current` with the strong gate and fresh review. It is not a component.

## Rules

1. **One way only.** Once `components/NAME/current` exists, `runtime/current` stops serving NAME's files. No shadow copy, no fallback to the pin for that NAME.
2. **Promotion** is atomic: build the release dir, run its check against those bytes, create `current.tmp` beside `current`, then `rename(2)` over `current`. No global hold; wakes keep running.
3. **Rollback** is re-pointing `current` at the previous release SHA with the same rename. The previous SHA is recorded in the promotion record before the swap.
   If post-swap verification is RED and no previous SHA is known, the new pointer remains in place and the result is `unknown`, not a rollback. With a previous SHA, automatic rollback is reported as `rolled-back` only if the restored pointer verifies GREEN; otherwise it is `unknown`. Inspect the recorded problems and live pointer before recovery.
   If an existing release fails its manifest check, promotion builds and checks replacement bytes first, then preserves the invalid release under `releases/.corrupt-SHA-TIMESTAMP` before installing the replacement. The release path may briefly be unavailable during replacement; a failed installation attempts to restore the invalid directory.
4. **Contract.** Each component's `CONTRACT.md` names its inputs, outputs and exit codes. Its check runs against the release dir, not the working tree.
5. **Docs-only commits** need no promotion.

## Detector

A component is RED when its `current` pointer and its files disagree: pointer missing, target missing, target's bytes not matching the recorded sha256 of `bin/NAME`, or a file for NAME still served from `runtime/current` after the pointer exists. The intended detector runs with the duty loop and reports through a `components` signal. As of 2026-11-09 no `components` signal is registered in source and `mishe component verify` is the only check. Missing or stale detector output is UNKNOWN, never GREEN.

## Who promotes

Genome owns the promotion command and runs the check. The command exists in
`src/mishe_tauftauf/components.py`:

```
mishe component promote NAME SHA --check-file FILE [--repo PATH]
mishe component rollback NAME
mishe component verify [NAME]
```

`promote` builds from the committed SHA (a full commit in the repository),
checks the release and only then moves `current`. A failed build or check leaves
`current` untouched. Authors (senses for `mishe-tape-facts`, and others) ask
genome to promote, or run the command themselves. Authors do not write `current`
by hand.

## Order of work

1. Fresh review of the promotion code and the detector (delivery-class).
2. Promote `mishe-tape-facts` at 0da08885 or later as the first component (no live consumer yet: low risk, good first exercise).
3. Promote `mishe-tape` and switch its consumer from `runtime/current/bin` to `components/mishe-tape/current/bin`; observe one wake end to end through the real caller.
4. Roll back once on purpose to prove the revert path, then re-apply.
5. Then `tools/` scripts such as `camera_signature`.

## Open limits

- Rollback restores the previous bytes only; it does not undo side effects a component already wrote.
- `mishe-tape` is read by `feed.py` through `$SITE/bin/mishe-tape` and its install record today. Moving that path is part of step 3, not a separate change.
