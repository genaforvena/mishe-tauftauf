# Roles

A role is a charter in the site's `charters/` directory plus a wall the role keeps edited. It is a
standing identity with a wall and a history, not a running process. In the site this repository is
developed in, the roles are:

| Role | Owns |
| --- | --- |
| `genome` | The runtime and reusable core, release snapshots, and CI and live-consumer checks. |
| `health` | Incidents, local reliability, and recovery when a unit or signal fails. |
| `senses` | Sensors and their readings: what is measured, how valid it is, and when it is UNKNOWN. |
| `docs` | The reader's book: introductions, reference pages, and removing stale explanations. |
| `pub` | Public editorial work and reader relations, under its existing account grant. |
| `field-review` | External ideas and small experiments, taken through to a checked application. |
| `rust-plan` | Moving selected runtime pieces from Python to Rust (a compiled systems language), one path at a time. |
| `steward` | Cross-area priorities, work nobody owns, and how the model budget is split. |
| `network` | Seeing the home network clearly: reachability, latency, loss and the router's own state. |
| `system1` | The ladder of work: moving each recurring decision to the lowest rung that can carry it — deterministic code, then a typed decision model, then a mind. |
| `operator` | The human owner's standing delegate: it answers the requests addressed to `operator`. It is not an area. |

They divide attention, not file ownership, and any of them may fix a bounded problem it finds.
Sites can differ. A role exists when its charter file is in the site's `charters/` directory, so a
site can keep, edit or drop the starter charters and add its own. `mishe init` installs starters
for `genome`, `witness`, `senses`, `discover`, `health`, `docs` and `research-methods`; the site
this repository is developed in has no `witness`, `discover` or `research-methods` charter.
Messages to a role are visible to everyone, not private. **The operator is you**, the human owner;
the `operator` shell is a convenience for you, never a place an agent waits.
