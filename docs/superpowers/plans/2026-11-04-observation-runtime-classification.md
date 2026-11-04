# Observation-driven runtime — Phase 0 classification

Companion to `docs/superpowers/specs/2026-11-04-observation-driven-runtime-design.md`.
Recorded before any deletion, as the spec requires. Evidence: module docstrings,
the import graph (`ast`, in-repo imports only), the live unit files in
`~/.config/systemd/user` (copied to `SITE_HOME/backups/runtime-rewrite-20261104/units/`),
and targeted reads. **Depth limit:** `discovery.py`, `body_perception.py`,
`inference_effects.py` and `seed_culture_views.py` were classified from their
docstrings, importers and unit usage, not read line by line. Each carries a
"read before delete" flag; nothing flagged is deleted until read.

## Baseline (2026-11-04, old instance stopped)

| Measure | Value |
| --- | --- |
| Python modules in `src/mishe_tauftauf` | 150 |
| Python lines | 60,034 (TypeScript `native/`: 1,004) |
| Tests | 259 files |
| `mishe*` units loaded | 77 (`mishe-core-supervisor`, `mishe-restore-2147b` activating; 8 failed) |
| Unit files in `~/.config/systemd/user` | 248 entries, of which the `android-body-*` set belongs to another site and is **out of scope** |
| Resident Mishe processes | tmux server + `mishe-tmux-views` attach + `tail -f chat.log` |

## Hubs that constrain deletion order

`feed` (32 importers) and `observations` (29), `seed` (20), `wall` (15),
`chat_protocol` (15), `records` (14), `tmux` (14) are hubs. `wall` and `seed`
import `tmux`; `wall.settle` takes the seed lock and requires a pending tape wake.
So `tmux` cannot go before `wall`/`seed` are cut loose from it.

## Classification

| Module(s) | Decision | Reason |
| --- | --- | --- |
| `core/` (`state`, `dispatch_record`, `registry`, `interfaces`, `supervisor`, `restore_*`) | **keep**, adapt `supervisor` (public exclusive section; no polling loop) | Right invariants: declared roles, one dispatch, durable record, unknown never replayed |
| `core/adapters.py`, `core/__main__`, supervise loop | **delete/replace** | Delivers into a pane through `seed.tick`; 15 s resident poll |
| `live_signal`, `signal_projection`, `host_capacity_reading`, `memory_stat_*`, `cpu_stat_text`, `unit_psi_*`, `timestamp_text`, `unit_cpu`, `slice_throttle`, `scan_freshness` | **keep** | Pure reading algebra and decoders; producers reuse them |
| `feed`, `chat_protocol`, `records`, `outcome_events`, `outcome_publication`, `space_text`, `wall_text`, `post_check` | **keep** | Tape and outcome records are unchanged in meaning |
| `wall`, `wall_patch`, `wall_review`, `wall_goal`, `patch_text`, `delivery`, `landing`, `task_state` | **adapt** | Wall/outcome/patch records stay; cut `tmux` imports and `settle`'s pending-wake coupling |
| `seed.py` | **adapt then delete** | Hub; `yield_wake`/`message` semantics move to the new `yield`; tick/stall/follow/run go |
| `tmux`, `native_pane`, `omp_pane`, `window_manifest`, `viewport` | **delete** | Pane layer |
| `native_resident`, `native_recovery`, `native_gate`, `claude_gate`, `permission_menu`, `seed_permission_panel`, `session_runner`, `session_restarts`, `session_receipts`, `session_watch`, `engine_rotation`, `clear_journal`, `mind_loop`, `minimal_supervisor` | **delete** | Exist only to keep a TUI alive or tell whether it is |
| `mind_liveness` (+ uncommitted edits by another mind) | **delete last**, after checking the edit is still wanted | Pane liveness |
| `omp_mind` | **adapt** → omp engine adapter | Already `--print --no-session --no-extensions --cwd` |
| `inference_*`, `native/*.ts` | **read, then decide** | `inference_optiona` is a one-shot omp seam; if the adapters supersede the loop, delete |
| `initiative_watch`, `substrate_watch`, `steward_watch`, `ci_watch`, `engine_watch`, `observer_verdict_watch`, `follow_through`, `initiative`, `activity` | **replace by producer + gate, then delete** | See duty table |
| `discovery` (9,085), `seed_culture_views`, `seed_witness_view`, `operator_view`, `wall_view`, `view`, `dashboard`, `observations`, `body_perception` | **read before delete**; dashboards stay as a read view | Evidence input, not runtime |
| `recovery_io`, `recovery_policy`, `remediation_bands`, `render_admission`, `release_readiness`, `release_retention`, `retirement`, `host_*`, `proc_table` | **read before delete** | Recovery/host duties; host guard itself is external and untouched |
| `model_change`, `model_boundary`, `model_cli`, `engine_policy`, `engine_usage`, `engine_boundary`, `stats_boundary`, `token_usage` | **adapt**: keep model replacement and quota facts, drop rotation | Engines reduce to claude and omp |
| judges (`completions_judge`, `jev_judge`, `laya_*`, `codex_review`, `witness_analysis`, `external_view`, `control_cache`, `judges`) | **keep** (System 1 residual judgment, optional); codex dropped | Not part of liveness |
| `trial_metrics`, `multibody`, `predictions`, `stats` readers | **defer** | Unrelated to runtime; read when touching |

## Detection duties to replace (from live units)

Every row needs a named signal, `max_age`, subscribing role, and a controlled-failure test
before the old detector is removed.

| Old unit / module | Observes | New signal | Resolver |
| --- | --- | --- | --- |
| `ci` (`ci_watch`) | GitHub Actions result | `ci` | genome |
| `substrate-watch` | checkout vs live runtime pin | `substrate` | genome |
| `initiative`, `initiative-watch`, `follow-through` | follow-through and stalled roles | wake gate + `initiative` intentions | role itself / health |
| `silence` (`activity`) | no mind activity > threshold | the next-due timer (no resident silence monitor) + `runtime` meta-signal | health |
| `session-watch`, `tmux-*`, `permissions`, `operator-view` | pane liveness | **retired outright** (no panes) | n/a |
| `model-changes` (+guard, alert) | model registry change freshness | `model-changes` | senses |
| `omp-db-watch/alert` | omp stats DB integrity | `omp-db` | health |
| `settlement-integrity` | yield/settlement consistency | `settlement` (from dispatch store: open/unknown dispatch age) | genome |
| `pub-devto-watch/freshness/alert` | DEV.to publication freshness | `devto` | pub |
| `exit-node-egress(-alert)` | tailscale egress | `egress` | health |
| `host-capacity(-guard)`, `unit-psi-sampler`, `plants-anon-split-sampler` | PSI / anon pressure | `host-capacity`, `psi` | health |
| `guard-bytes-drift(-alert)`, `restore-readiness(-alert)`, `boot-watch` | guard integrity, restore readiness, boot | `guard-bytes`, `restore-readiness`, `boot` | health |
| `recovery*`, `core-supervisor`, `observer-checks(-alert)` | recovery outbox, supervisor, observer verdicts | `dispatch` (open dispatch age), `observer-checks` | genome |
| `budget-collector` | token budget | `budget` (gates spontaneous wakes) | senses |
| `health-orphan-sweep` | stray test tmux sessions | **retired outright** | n/a |
| `mishe-failed@` | any failed wake or producer | writes RED + inbox for resolver | per-unit |

Units not about Mishe's runtime (`mishe-ifm-shim`, `mishe-note3-tailscaled`,
`mishe-cleaner-recovered`, `mishe-host-guard`, `mishe-restore-2147b`) are left as they are.
