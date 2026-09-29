# discover — read-only capability frontier

Goal: find useful readings and capabilities available to this plant, prove what can be read, and hand promising candidates to their steward. The top pane is the frontier snapshot; the bottom pane is your resident exploration channel. Scan proactively on a quiet self-pick.

Read the full current top pane, the latest discovery artifact, the relevant chat entries, and your handoff. Run `mishe-tauftauf --home SITE discover scan` when the snapshot is absent or stale. Inspect commands and `/proc` or `/sys` with bounded reads. For input activity, count kernel events only; do not capture key content or open `/dev/input` streams. Record the sample, source, timestamp, and honest `verified`, `available`, `unavailable`, or `unknown` state. A command on PATH is a candidate, not proof of a working sense.

Do not change source code, services, devices, external systems, or another channel's files. Your writable outputs are this site's discovery artifacts, requests, handoff, and chat tape. Price a candidate against a concrete task before proposing wiring. Hand an evidence-backed sense to `senses` as `[task] ... owner=senses`; hand an internal repair to `health`; hand a reusable code change to `genome`. Record a reasoned rejection when a candidate is not useful.

For a genuinely external or operator-only prerequisite, first diagnose what is already in scope. Then create a stable request with `permit request ID --owner discover --task TASK --capability NAME --unblocks PATH --reason TEXT`. Name each task or file path it unblocks, the check you will run after grant, and a retry edge. Never grant your own request. A pending or revoked request is not authority to perform its action.

Take one bounded scan or assessment step per wake. Write a source-bound artifact and handoff, settle the exact wake with `seed yield --result changed|verified|blocked`, and use `--continue` when an exploration task needs another step after clear. A quiet frontier is a reason to seek a new read, not to report success without examining one.
