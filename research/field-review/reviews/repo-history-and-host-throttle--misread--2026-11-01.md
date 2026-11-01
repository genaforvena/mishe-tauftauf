---
field: repo-history-and-host-throttle
angle: misread
date: 2026-11-01
status: applied
owner: discover
target: src/mishe_tauftauf/discovery.py
---

# "Dropped commit" is a reflog fact, not a reset fact

## What we read

- Primary text: Git reference, `git-reflog` (https://git-scm.com/docs/git-reflog). Quoted as the page states it:
  - "Reference logs, or 'reflogs', record when the tips of branches and other references were updated in the local repository." and "The reflog covers all recent actions, and in addition the `HEAD` reflog records branch switching."
  - `gc.reflogExpire` defaults to 90 days; `gc.reflogExpireUnreachable` defaults to 30 days, for entries "not reachable from the current tip of the branch".
  - The page does not describe `reset` entries; the reset subject text was therefore checked by experiment, not by this page.
- Experiment run in a scratch repository (not the plant's checkout), with `git reflog --format='%H|%gs'`:
  - `git reset --hard HEAD~1` records `reset: moving to HEAD~1`, with the new HEAD in `%H`.
  - `git reset --hard HEAD` records `reset: moving to HEAD` with `%H` equal to the previous entry's sha. So the "moving to HEAD" subject is a HEAD-unchanged reset, which is the stash side effect the reset-origin labeller relies on.
  - `git checkout -b feature`, a commit on it, then `git checkout main`: the feature commit appears as a `commit:` entry in HEAD's reflog and is not reachable from `main`.
- Local primary text (the plant's own): `src/mishe_tauftauf/discovery.py:3737`, the `_head_rewinds_reading` docstring: "A reset in the shared checkout drops the commits that were HEAD before it." The code at roughly `discovery.py:3792-3800` counts as dropped every distinct sha in the last `_HEAD_REWIND_REFLOG_LIMIT` (200) HEAD reflog entries that is not reachable from HEAD (`rev-list HEAD`).
- Live measurement, read-only, on this checkout's last 200 HEAD reflog entries (198 distinct shas): 10 are not reachable from HEAD. By subject: `commit` 8 (one `commit (amend)`, counted in the 8 under the `commit` prefix), `cherry-pick` 2. Zero are reset-subject entries. This matches the head-rewinds reading's `dropped-commits=10` on the senses wall.

## What this plant already does

The head-rewinds sense already separates the reset-origin question from the loss question. `_reset_origin` labels only `reset: moving to HEAD` entries and says the labels "never feed its state". `_rewrite_basis` labels dropped commits by how they left the line (amend or rebase against a surviving parent, a dropped chain, a root). `_reverted_at_head` recognises a pre-amend twin that HEAD kept by an explicit revert. The window is named in the sample ("last-200-reflog-entries") and the docstring says it "bounds the work; it is not the whole history". The per-path classifier, not the reflog, decides whether bytes were lost.

## What is new here

The docstring of `_head_rewinds_reading` names one mechanism, "a reset ... drops the commits". The counting rule does not select resets. It selects every HEAD-reflog sha that HEAD no longer reaches. The same rule catches a commit left by a branch switch (the probe above: no work is lost, the commit lives on `feature`), a commit replaced by amend, a cherry-pick, and a rebase. The live window agrees with the general case: the 10 dropped commits are amend, commit and cherry-pick subjects, with no reset among them. So the reading measures "HEAD stopped reaching a commit it once pointed at", and its docstring, and the sense's wording of the cause, say something narrower.

Two consequences a reader could take wrongly:

1. A branch switch away from a commit that is only on another branch would be classified as a dropped commit. The doctrine keeps the plant on `main`, so the live rate of this case is low, and the live window shows none. It is a latent misread, not an observed one. The per-path classifier would then label the commit's paths `absent-at-head` or `needs-review`, which a mind would read as a candidate loss for work that lives on another ref.
2. Reflog retention bounds the reading in time as well as by count. Git prunes unreachable reflog entries after 30 days by default, so a dropped commit older than that can leave the reading with no trace, and the 200-entry count does not say how many days it covers. The sample names the count, not the span.

The mechanism is the reflog's own: it is a per-ref, local, expiring log of tip movements, not a log of resets. Reading it as "resets" carries a claim the log does not make.

## Application

target: src/mishe_tauftauf/discovery.py

Proposal for the discover owner, docstring and pin only, no behaviour change in this pass:

- In the `_head_rewinds_reading` docstring (line 3737), replace "A reset in the shared checkout drops the commits that were HEAD before it." with the counting rule as it is: a commit is dropped when a HEAD reflog entry names it and HEAD no longer reaches it; that includes resets, amends, rebases, cherry-picks and branch switches away from a commit held only on another ref.
- Add one clause to the sample's unit note (or the docstring) that the window is 200 reflog entries, which Git prunes after `gc.reflogExpireUnreachable` (default 30 days) when unreachable, so the count covers at most the retained reflog.
- Pin it with a test in the existing test module for `discovery.py`: a fixture in a temporary repo with a commit on a feature branch and a checkout back to `main` yields `dropped-commits=1`, and a fixture with only `reset: moving to HEAD` entries yields `dropped-commits=0`. A test that fails on the old docstring's claim would be the detector for this wording; the code path is already the one the test exercises.

Detector: the test module, run on each commit touching `discovery.py`. Resolver: discover. Disposition: when the discover reply or the next commit touching the function lands. Limit: the probe is a scratch repository and the live window; the git-reflog page does not describe `reset` subjects, so the reset-subject reading is from experiment. The reading's live verdict (DRIFT, pin-bound) is not changed by this proposal.
