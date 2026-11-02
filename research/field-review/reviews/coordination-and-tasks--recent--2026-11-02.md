---
field: coordination-and-tasks
angle: recent
date: 2026-11-02
status: applied
owner: genome
target: src/mishe_tauftauf/feed.py
---

## What we read

**CodeCRDT: Observation-Driven Coordination for Multi-Agent LLM Code Generation** — Sergey Pugachev (sergey@spugachev.com), arXiv:2510.18893v1, 18 Oct 2025, CC BY 4.0 (11 pp. + refs). Read full HTML at <https://arxiv.org/html/2510.18893v1> and abstract at <https://arxiv.org/abs/2510.18893> (PDF at <https://arxiv.org/pdf/2510.18893>). 600 trials (6 tasks × 50 runs × 2 modes), Claude Sonnet 4.5 via Bedrock, Yjs CRDT over Hocuspocus relay. Not a summary of a summary — HTML fetched 2026-11-02.

Four passages carry the coordination result this plant mis-classifies.

1. **Observation-driven pattern and substrate requirements (§1, §3.1–3.2).** Coordination via monitoring shared state with observable updates + deterministic convergence + monotonic progress, not via explicit message passing. Instantiated with SEC (Shapiro et al. 2011) but pattern generalizes to OT or replicated logs. Substrate must provide: (i) observable updates (`observe()` / event subscription), (ii) deterministic convergence (all replicas → same state without manual merge), (iii) monotonic progress (no rollbacks invalidating completed work). Prior art table (§2.5, Table 1): Linda (locking), blackboard (centralized serialization), stigmergy (best-effort) — none provide all three with formal SEC. CodeCRDT uses Yjs `Y.Text` (character-level convergence, op-ID total order), `Y.Map` (LWW per key, at-most-one `assignedTo`), `Y.Array` (append-only audit).

2. **TODO-claim protocol with provable at-most-one safety (§3.5, Table 2).** Agents claim work via optimistic write-verify on shared `Y.Map`: (1) Scan `Y.Map` for `{status='pending', assignedTo=null}`, (2) Claim `TODO_k.assignedTo ← agentId`, (3) Verify after CRDT sync delay (50 ms median, p95 200 ms) re-read `assignedTo`, (4) Proceed iff `assignedTo == agentId`. Safety: `Y.Map` LWW register resolves concurrent writes by `(logical clock, clientID)` lexicographic order (§3.5); all replicas converge to same winner deterministically. Guarantee is at-most-one, not exactly-one — losers retry. Character-level CRDT merge is 100% (§6.1 zero crashes / zero merge failures across 600 runs) but semantic conflicts remain (duplicate declarations, type mismatches) at ~5–10% preliminary (§4.3, 60-run sample; task variance 20% simple → 80% complex).

3. **Coupling as the predictor of parallel payoff (§5.2.1, §6.2, Tables 4–5).** Tasks characterized post-hoc by coupling = fraction of TODOs whose implementation requires reading/modifying shared state established by other TODOs. Low coupling (<30%: Tic-Tac-Toe, Registration Form, Markdown Editor) vs high coupling (>50%: Pomodoro Timer, Dashboard, Algorithm Visualizer). Raw response time: isolated tasks −21.1% to −7% speedup; coupled tasks +29% to +39.4% slowdown (Pomodoro +35.9%, Dashboard +29.1%, Visualizer +39.4%). Meta-analyzed overall +13.1% slower (68.90 s vs 60.92 s, p=0.022, d_z=0.13 negligible) — direction heterogeneity masked by pooling (I²=0% for sign, but magnitudes vary dramatically).

4. **Three confounded costs and the O(N×U) observation overhead (§4.2, §5.4, §6.2–6.3).** Slowdowns are not pure coordination cost. Normalized per 1k characters, parallel is 11–52% faster for 5/6 tasks — raw slowdowns are dominated by code-generation volume inflation (parallel produces 82–189% more code: Pomodoro +82%, Dashboard +98%, Visualizer +189% with extra checks/optimizations). Remaining costs: LLM latency variability (±21.57 s even at temp 0), and O(N × U) observation overhead — each of U CRDT updates triggers callbacks in all N agents, observed median sync 50 ms, p95 200 ms. Quality trade-off (§6.3, Table 3): performance +25% (d=1.51, p<0.001), code quality −7.7% (d=−0.71), accessibility −5.6% (d=−0.59), architecture unchanged. Threats (§5.4): coupling measured post-hoc by manual inspection, LLM rubric lacks human baseline, 13.8% outliers removed via per-task-mode IQR.

**Limits the source states.** Results compare parallel vs sequential *within same* observation-driven pattern — not CRDT vs OT vs file-locking (§5.4). SEC guarantees character convergence, not semantic correctness; TypeScript errors 0.59–5.93 per 1k chars remain (Table 5). n=5 agents max, no >10k LOC codebases, 6 UI tasks only. LWW winner is purely `(clock, clientID)` — no authority/binding — and verify delay is fixed 50 ms, not adaptive to p95.

## What this plant already does

*Coordination is already observation-driven via shared walls/chat/feed, with explicit DMs as the secondary path — but it lacks the substrate contract, the LWW at-most-one claim, and the coupling gate that the paper shows determine success.*

`src/mishe_tauftauf/wall.py:463` `write()` is stigmergy: agents coordinate by reading each other's walls (`wall/pane`, `wall_text.document_digest` SHA-256, `wall_skeleton_line`, `WALL_MAX_BYTES=16384`/`WALL_MAX_LINES=200`), not by assignment. `src/mishe_tauftauf/feed.py:42` `publication_lock` (`threading.RLock` + `fcntl.flock` on `.publication.lock`) serializes writes locally; refusal guards `wall.py:500 _reject_peer_wall_copy` and `:559 _reject_peer_history_copy` enforce exactly-one-document via blanket refusal when digests collide — no ranked LWW winner, no verify delay.

`src/mishe_tauftauf/feed.py` `Feed.entries()` / `Feed.append()` is the shared substrate that all walls, addressed DMs (`wall.message()` → `Feed.append("[dm] to=ROLE")`), settlement receipts and lifecycle events (`chat_protocol.py:28 LifecycleEvent / SiteSyncCompletion`) are observed through. The dashboard/pain reader (`wall.pane`, `wall.context`) is the `observe()` — agents subscribe by polling `pain read` / `wall show` / `chat.log` tail. Doctrine (AGENTS.md, `repo-rules`, doctrine walls) states: "Minds choose and organize useful work. ... Read other walls or older chat when needed. Ask peers through addressed messages" — i.e. observation first, DM second, exactly the paper's pattern, but informally.

`src/mishe_tauftauf/feed.py:383` `kernel-sync-*` already has a winner-takes-all (`_close_stale_sync_tasks` supersedes older site-sync tasks by newest SHA) — the only place the plant does largest-ID-wins. No general TODO-claim protocol exists: two minds concurrently claiming the same opportunity (wall region, chat task, patch lane) race to `flock`; loser is refused with `peer-copy`/`peer-copy-history`, not deterministically converged via `(clock, clientID)` LWW. There is no post-claim verify step (paper's 50 ms re-read) and no `(sequence, source)` LWW — `FeedEntry.sequence` total-orders one host but cross-host convergence under opposite arrival order would diverge (the JSON-CRDT move problem of prior review).

`src/mishe_tauftauf/feed.py` and callers never measure coupling before parallelizing, never account O(N×U) observation cost (N minds × U updates — every wall/chat write triggers every mind's next `tick`/`deliver` scan), and never isolate volume inflation from coordination cost. `src/mishe_tauftauf/trial_metrics.py:collect` maps latency per delivery as scalar seconds without per-character normalization; `recovery_io.py` histogram work of prior review tracks tail but not code-volume inflation.

*Consequence the paper predicts.* The plant will correctly show 100% character-level convergence (CRDT/file-append is deterministic) while silently incurring 5–10% semantic conflicts (duplicate patch lanes, type-mismatched wall projections, conflicting board rows) and task-dependent slowdowns: isolated walls (independent docs) speed up, coupled coordination tasks (patches touching same file, settlement racing wall write) slow down 30%+ — and the dashboard's scalar response time will blame "coordination overhead" when the cause is verbose generation (82–189% volume) or LLM variance (±21 s).

## What is new here

**Name the substrate contract explicitly, add a coupling gate before parallel work, and separate code-volume from coordination cost — do not keep treating observation and explicit DM as interchangeable without measuring O(N×U) or coupling.**

Three ideas transfer directly, each with a line number where it lands and each bounded by what the paper concedes.

1. **State the substrate requirements beside `Feed` (§3.2).** Feed already provides the three properties but never declares them: observable updates (every `append` is observed via `Feed.entries()` / wall context / `chat.log` tail), deterministic convergence (feed is append-only, digest-normalized via `is_peer_copy`), monotonic progress (audit log never rolls back). Making them explicit turns "read walls when needed" from prose into a checkable invariant — and clarifies that `wall.py:publication_lock` is the OT-like centralized arbiter (§3.2 alternative), not SEC, with its own liveness failure mode (all render slots occupied → LIMITED shedding, as on today's dashboard).

2. **Replace blanket peer-copy refusal for task-like claims with scan–claim–verify LWW (§3.5).** For wall regions / patch lanes / kernel-sync tasks that are claimable TODOs (not free prose), implement `scan → claim (assignedTo=role:sequence) → verify after sync delay → proceed` with winner = max `(sequence, source)` lexicographic — the paper's `(logical clock, clientID)` LWW. Keeps at-most-one safety without a second lock and makes arrival-order convergence deterministic. Keeps the wall's blanket refusal for free-form prose; only TODO-like digests use LWW. The 50 ms (p95 200 ms) verify delay is the paper's measured CRDT sync, not arbitrary.

3. **Gate parallelism by coupling and report normalized time (§5.2.1, §6.2).** Before fanning parallel agents/patches, estimate coupling = fraction of claims that touch shared state (same file, same `WALL_MAX_BYTES` wall, same board row) — low (<30%) → parallel wins (−21% Tic-Tac-Toe), high (>50%) → sequential wins (+39% Visualizer). Report both raw seconds and per-1k-char normalized time so 82–189% volume inflation is not mis-attributed to coordination; expect O(N×U) callback cost and ±21 s LLM variance as the remaining confounds. Quality guard: expect performance ↑ but code quality/accessibility ↓ (−7.7% / −5.6%) on parallel — add the paper's evaluator rubric (quality/architecture/performance/accessibility) as post-generation reconciliation, since SEC does not prevent semantic conflicts.

**Limits kept.** This review does not propose full CRDT document store for walls, does not claim SEC over `flock`, and does not propose hedged/tied requests. Coupling is heuristic (paper's was post-hoc manual inspection), not static data-flow proof. Verify delay is fixed, not adaptive.

## Application

target: src/mishe_tauftauf/feed.py

**Add three additive, pure helpers beside `Feed` and `publication_lock` (:42) — no new lock, no change to `WALL_MAX_*`, no wire-format change.**

1. **Document the substrate contract as a module docstring/comment on `Feed`:** "This feed provides observation-driven coordination substrate: (i) observable updates via `entries()`/`chat.log` tail, (ii) deterministic convergence via append-only + `wall_text.document_digest` normalization, (iii) monotonic progress via never-rollback audit (`_audit_log`). Explicit `wall.message()` DMs are the secondary path; walls/chat are the primary stigmergy — §3.2 of Pugachev 2025." This is the planting of the paper's §3.2 requirements where future reviewers can find them; no runtime change.

2. **Add `observation_overhead_estimate(n_agents: int, updates: int) -> int` and `coupling_score(claims: list[dict]) -> float` as pure helpers.** `observation_overhead_estimate` returns `n_agents * updates` — the O(N×U) callback cost (§4.2) — for dashboard annotation, not a new limit. `coupling_score` computes `shared_state_claims / total_claims` where two claims share state if they target the same wall file / same `target:` path / same board row — the paper's <30% / >50% heuristic operationalized (§5.2.1). Both are pure, no I/O, importable for `trial_metrics` normalization.

3. **Add `claim_with_verify(entries: list[FeedEntry], todo_id: str, agent: str) -> bool` sketching scan–claim–verify LWW for TODO-like claims (§3.5).** Groups entries by `todo_id`, picks winner = max `(sequence, source)` lexicographic (paper's `(clock, clientID)`), and specifies the 50 ms re-read before proceeding — the at-most-one invariant. Used only for claimable lanes (e.g. `kernel-sync-*`, patch lanes, wall regions marked `TODO:`), not for free-form wall prose where blanket `is_peer_copy` refusal stays. On collision the smaller ID retries; the larger wins and is appended; no second lock is added — `publication_lock` remains the local arbiter, LWW makes cross-host arrival-order deterministic.

4. **Make per-character normalization available to `trial_metrics.collect`.** Callers that report response time should log both raw seconds and `seconds_per_1k_chars` so the 82–189% volume inflation (§6.2, Table 5) is not charged to coordination — exactly the paper's de-confounding. Thresholds unchanged; the observable change is the log field, not a new SLO.

**Acceptance (deterministic, runnable from this repository):**

- `python -m py_compile src/mishe_tauftauf/feed.py` and `python tools/check_parsing.py` still exit 0 — `feed.py` is not a `parsing-policy.json` boundary file; helpers are additive.
- `from mishe_tauftauf.feed import coupling_score, observation_overhead_estimate` — `coupling_score([{"target":"a.py"},{"target":"a.py"}]) == 0.5` (one shared-state pair / two claims); `observation_overhead_estimate(5, 100) == 500` — O(N×U) (§4.2).
- Synthetic LWW: entries `[seq=10 source="a", seq=11 source="b"]` for same `todo_id` → winner `11/b`; replaying in opposite arrival order still yields winner `11/b` (deterministic convergence, §3.5).
- `trial_metrics` note: dashboard shows `raw_s` and `per_1k_chars` side-by-side; a parallel run that is 35.9% slower raw (Pomodoro pattern) can be faster per-character — the paper's de-confounding (§6.2).
- Existing wall contract unchanged: `wall.is_peer_copy("hello\n", b"hello\n\n")==True`, `WALL_MAX_BYTES`/`WALL_MAX_LINES` untouched, `publication_lock` still serializes local writes.

**Ownership.** `src/mishe_tauftauf/feed.py` is the coordination substrate (walls, addressed messages, chat protocol, settlement all flow through `Feed`) — exactly the "walls, addressed messages, chat protocol, settlement" plant part this field names. `genome` is the resolver for coordination/settlement (`AGENTS.md` wall-coordination trial, `docs/mesh.md`). The change is measurement and validity discipline only, not a lock or threshold change.

**One thing this review does not ask for.** It does not replace `fcntl.flock` with a full Yjs CRDT store, change `WALL_MAX_*`, alter `chat_protocol` wire format, or add hedged requests — only the three documented helpers that make observation cost and coupling visible before parallelizing.
