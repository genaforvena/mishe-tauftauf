# Minimum host boundary — current research contract

**Status, 2026-10-07 / Body49313, review reconciled Body49527:** engine-neutral research contract, not a deployed universal protocol. Dispatcher-first is the interim decision for observed owned Linux work; reuse the maintained local recovery boundary rather than promote a research evaluator. Witness49316/DM49395 independently KEEP the cost-separated interim decision; table reading is FULFILLED, not proof of smallest runner/RAM/platform winner. Witness49429/DM49491 independently KEEP OFFLINE this exact minimum/optional/API contract distinction; contract reading is also FULFILLED. Neither reading establishes live delivery.

The complete former seam contract is preserved byte-for-byte at `../../artifacts/body-49313-host-contract-before.md`, SHA256 `8e94e05ac3492e2d87384e601de3ad0b5ade842d767bafc779a39bca280e91a7` (14672 bytes). Its original defect evidence, sequential measurements and source references remain historical evidence, not universal requirements or current continuation instructions. Paths below are relative to this directory unless described as repository paths.

## Mandatory behavior, with failure rationale

| Required behavior | Why it is required / evidence or design limit |
|---|---|
| A selected, versioned, bounded request/result schema with unambiguous correlation and complete/incomplete byte state | Lost or misattributed replies cannot establish success. Native parser48708 silently accepted a valid prefix; final48804 rejects the seventeenth step and malformed suffix. General request correlation is a design requirement, not conformance proved by that parser. |
| Discovery of the selected profile's capabilities, schemas, versions, bounds and explicit `available`, `absent`, `denied`, `unknown` states | A complete snapshot cannot silently omit a required entry. Available means potentially callable, not authorized.48965 exercised available-then-revoked, absent and missing-policy UNKNOWN. |
| Host-owned effect classification and current call-time authority | Caller `read`/`act` labels, discovery and persisted authority are not grants.48965 forged-read and revoked-policy requests were denied before native file open/transform. Check/open are separate; atomic revocation is unproved. |
| Nonempty caller-known operation identity before any effect-capable dispatch, with an explicit identity namespace and duplicate policy | The old seam's unset ID persisted eighteen effects under literal `(null)`; duplicate execution IDs merged two executions. Naming must survive loss of a reply when recovery is promised. Identity is not durability. |
| Truthful correlated outcomes, including partial/error/UNKNOWN, and no automatic repeat of an ambiguous operation |49052 killed after native append/fsync but before outcome: a fresh process reported UNKNOWN, although the observer saw one append. Deliberate blind repeat produced two appends and failed the same oracle. |
| Explicit limits and preservation of completed independent results | Oversize, malformed, truncated or incomplete input cannot become complete output. Whole-input transforms require both complete bytes and a matching successful result. No fabricated current time when clock quality is unknown. |

Discovery must not try the requested effects as a capability probe. Reads can disclose private information or affect special devices; host policy applies to those too. Special-device safety and general sandboxing are not established by a fixed ordinary-file fixture. Transport carries bytes; it supplies neither authority nor a universal launch mechanism. A shell, SSH, VM, filesystem API and network are not mandatory.

`cap.call` is the research spelling for capability ID/version, bounded arguments and operation identity. A result returns that identity, truthful status and typed data or explicit byte completeness. The proposed wire statuses are `ok`, `partial`, `denied`, `unsupported`, `unavailable`, `error`, `unknown`; they are NOT aliases for the maintained ledger's `completed`, `partial`, `unknown`. An adapter must state its mapping and preserve uncertainty. Return native/backend identity only when known; do not manufacture an OS ID from a fixture-generated ID.

## Two distinct identity/recovery policies

A profile must select and declare its policy, namespace, retained evidence and recovery boundary. Neither policy permits re-execution of an ambiguous effect. Neither implies exactly-once execution.

| Property | Historical seam: execution identity | Maintained local boundary: content-bound recovery |
|---|---|---|
| Namespace | Operation ID in the named history store; same ID in different stores is distinct | Operation ID in the selected ledger store, with provider call identity binding where used |
| Duplicate meaning | Reject an already-taken ID before ROM/effect dispatch; pending reservation plus retained-store scan | Matching pending binding can identify the existing intent; changed binding is rejected before effects. Completed allocation/binding raises `AlreadyExecuted`; recovery reads the recorded outcome |
| Content stored | Effect fields `op_id`, `byte`, `dei`, `deo`, plus completion counts; no request digest | Intent binds request, capability/version, arguments, authority, optional budget and native provider call; start and observed outcome are separate records |
| Lost reply / successor | Recover effect/completion counts and `complete`/`partial`/`unknown`; lost output cannot be reconstructed | `reconcile` returns recorded outcome/result when present, otherwise UNKNOWN. Started-without-outcome cannot be dispatched again |
| Result / host ID | Volatile reply/output hash is not a durable result | Recover only ID/result actually stored in the outcome;49052 recovered exact eleven-byte result and fixture backend ID after durable outcome |
| Concurrency | Only sequential single-threaded dispatch measured; concurrent guard behavior unproved | One store writer holds nonblocking `flock` across read/check/start/execute/outcome. This is not general thread/host/distributed safety;49052 did not measure competing writers |
| Durability claim | Retained seam files and measured process restart, bounded to its fixture | Local filesystem/process crash only; no power-loss, whole-host restart, expiry, Windows or firmware guarantee |

The seam's duplicate refusal remains correct for its undigested records. It cannot distinguish same intent from changed intent under the same name. The maintained boundary now provides a separate content-bound mechanism; it does not retroactively upgrade the seam. A deliberately new operation may get a new ID, but renaming an unresolved operation to repeat it is not recovery.

### Maintained API distinctions — no replay shorthand

Repository implementation: `src/mishe_tauftauf/inference_effects.py` (source read at Body49313; retained49052 manifest SHA256 `a5b53a0629a478e16d8b584840bf9550048d26281405cf4c17503303e255db94`).

- `request_digest` (292–324) binds canonical request plus capability/version, authority, arguments and supplied budget/native call. The serialization is this local API's digest convention, not proof of conformance to the signed-64 wire profile.
- `EffectLedger.allocate` (509–577) requires identity/capability/version/authority, snapshots content, fsyncs intent before effect dispatch, returns a matching pending intent, and rejects conflicting pending content. An existing outcome is `AlreadyExecuted` before a digest comparison; do not promise a different-content error class for completed IDs.
- `bind_dispatch` (579–641) validates the dispatch binding, including an optional reservation upgrade. Completed binding also raises `AlreadyExecuted`. `EffectBoundary.__call__` (1095–1142) uses this path; it is not an idempotent resend API.
- `EffectLedger.dispatch` (643–726) accepts a recorded intent, checks writer/binding for pending work, records start before executor and observed outcome before returning. It returns an already-stored outcome early, without re-execution or fresh content validation. It is not the entry point for validating a new request against a completed ID. Use the binding API for dispatch and the recovery API for retained outcomes.
- `reconcile` (728–750) never executes: no-intent, unreconciled-intent and started-without-outcome are UNKNOWN. `status` (752–771) may return `None` for no retained intent; missing/replaced history does not prove nonexecution. Do not convert it into a universal safe-retry verdict.
- `_runner` (1144–1147) delegates current authority/cancellation checks to the consumer executor. Persisted authority is only a binding. Ledger completion records an executor return, not universal physical success or remote delivery; retain the executor's typed result semantics.

Journals use actual flush/fsync and malformed/torn records fail closed as `JournalCorrupt`. Preserve corrupt bytes and route to the owner; do not erase/reinitialize history to make retry possible. Fresh successor inspection must not infer lost result/host ID from unrelated observer knowledge.

## Optional mechanisms — mandatory only when promised

- **Recovery query and durable history/results:** declare caller versus host persistence, retained namespace, result bounds and retention/disposal. Query recovery requires retained evidence; absent, expired or unavailable history is UNKNOWN, not safe nonexecution. A host without history may serve calls but cannot promise restart recovery. No universal `op.status` service is required.
- **Communication:** a scoped bounded capability, not an implicit network. Distinguish queued, accepted and delivered/acknowledged at the stated boundary. A disconnected local transform is useful if complete input/executor survive; it does not imply delivery. Persistent queues are optional and bounded.
- **Cancellation/concurrency/clocks:** separately specified when needed. Best-effort cancellation can leave effects UNKNOWN. Unsynchronized source time is not current wall time. Do not add a scheduler, global lock service or time synchronization as a universal prerequisite.
- **Interpreter/VM:** optional placement/program mechanism. All current evaluators share host-owned authority/action/recovery. Equal transformed bytes cannot select an evaluator or move host authority into it.

## Encoding profiles — separate from the minimum

`canonical-wire-profile-20261003.md` remains the exact proposed mixed control/byte profile: UTF-8 JSON object plus LF; closed schemas; reject duplicate keys, malformed UTF-8, unpaired surrogates and invalid integer tokens; typed semantic equality rather than one accepted byte spelling. Its control cap is65536 bytes excluding LF, depth16,256 members/array entries, decoded string16KiB,1024 records/session. Binary is opaque: `bytes.begin` plus fixed-header contiguous chunks with exact length/order/completeness, not raw binary in JSON strings. Its numerical bounds are fixture choices, not proven target-safety limits.

All-text strict padded-base6448309 and mixed48442 are measured specimen receivers, not integrated universal protocols. At matched12288 chunk size on1MiB they differ by357945 wire bytes; the chosen mixed build owns4118 more array bytes. Smaller wire is not smaller RAM. Select one encoding prospectively for a consumer; no implicit fallback or silent transcoding. Canonical boundary text does not imply text-native computation.

The older observation-first profile (`system.info`, rooted bounded `fs.read`, `process.list`) is a proposed fixture, not mandatory API inventory. Later native read/append fixtures are separately authorized experiments, not a universal capability expansion.

## Evidence, acceptance and recurrence

- Old seam causal evidence and precise source/probe references: immutable `../../artifacts/body-49313-host-contract-before.md`, including original `(null)` and duplicate-merge stores. Its records are not rewritten.
- Current host authority: `../../artifacts/witness-49063-check.py/json`, independent ACCEPT in DM49099. No whole-protocol/atomic-revocation claim.
- Current native append/process-crash recovery: `../../artifacts/body-49052-recovery-report.md` and result; independent ACCEPT `../../artifacts/witness-49162-review.md:20–27`, DM49230. Intent-only and effect-without-outcome UNKNOWN; completed exact retained replay; missing UNKNOWN/torn JournalCorrupt/no-repeat. Fixture backend ID is not OS/service identity.
- Cost/placement decision: `../../artifacts/body-49190-substrate-decision.md` and its read-only audit. Table reading FULFILLED by Witness49316/DM49395. Exact contract/diff/API reading FULFILLED by `../../artifacts/witness-49429-review.md:15–20` and `../../artifacts/witness-49429-check.json`, DM49491. Earlier pending receipts remain immutable history, not current gates.

Manual before-change/promotion probes and immutable manifest/raw-store oracles remain the continuing detector. Duplicate effects, missing evidence and broken history were controlled failures, not suppressed warnings. Body owns routing to Witness at the next useful wake; no installed periodic detector or guaranteed routing interval is claimed. Missing/stale/broken monitoring remains UNKNOWN/RED. Live uptake additionally needs a named consumer, exact authority/schema/limits/call IDs, actual loaded caller/bytes, independently read controlled failure/routing and checked snapshot activation/canonical verify/revert. No source-doc edit establishes that delivery.

**Next decision:** Docs49385/DM49518 reports reader-book uptake in main9a8df73 with exact-source synthetic callback smoke; author evidence is not independent runtime acceptance. Inference49536 returns source-docstring correction to Body's owned module; no API change or third ledger/codec/VM/service. Body resumes whole-composition dependency/cost and actual arrival/port work from the reconciled brief. Long portability/arrival/resource/consumer obligations remain OPEN; fulfilled table/contract readings are not repeated.
