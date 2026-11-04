---
field: failure-and-recovery
angle: critique
date: 2026-10-29
status: discarded
owner: health
target: src/mishe_tauftauf/engine_rotation.py
---

## What we read

**Simple Testing Can Prevent Most Critical Failures: An Analysis of Production Failures in Distributed Data-Intensive Systems** — Ding Yuan, Yu Luo, Xin Zhuang, Guilherme Renna Rodrigues, Xu Zhao, Yongle Zhang, Pranay U. Jain, Michael Stumm (University of Toronto). *Proceedings of the 11th USENIX Symposium on Operating Systems Design and Implementation (OSDI '14)*, Broomfield, CO, Oct 6–8, 2014, pp. 249–265 — read full text at:

- <https://www.usenix.org/system/files/conference/osdi14/osdi14-paper-yuan.pdf> (PDF, 18 pp., USENIX open access)
- <https://www.usenix.org/conference/osdi14/technical-sessions/presentation/yuan> (abstract page)
- Code and detailed failure data: <http://www.eecg.toronto.edu/failureAnalysis/> (linked p. 261)

Four passages carry the critique this plant would reproduce if it adopted recovery without testing its error handlers.

1. **The 92%/58%/35% claim — the "last line of defense" is the most bug-dense (Abstract; §4, p. 258, Fig. 5).** Of 198 randomly sampled user-reported failures of Cassandra, HBase, HDFS, MapReduce, Redis (designed to tolerate component failures, Table 1): "in 58% of the catastrophic failures, the underlying faults could easily have been detected through simple testing of error handling code" and "in 35% … the faults fall into three trivial patterns: (i) the error handler is simply empty or only contains a log printing statement, (ii) the error handler aborts the cluster on an overly-general exception, and (iii) the error handler contains expressions like 'FIXME' or 'TODO'." Another 23% are system-specific but exposed by 100% statement coverage on the handler. The remaining 34% are complex.

2. **Trivial pattern in full — empty/only-log, over-catch abort, TODO (§4.1, p. 257–258, Figs. 7–8).** 25% catastrophic failures ignored an explicit error — Java `throw` that was caught and only logged, or Redis syscall error returns unchecked. Fig. 7 `OpenedRegionHandler`: `catch (KeeperException e) { abort(...) }` — wrong abort; Fig. 8 HDFS: `catch (Throwable t) { System.exit(-1) }` after `catch (RemoteException e) { retry }` — over-catch kills the cluster on a transient glitch; MapReduce Fig. 9: `catch (IOException e) { // TODO LOG }` then hang requiring ssh to 4000 nodes — `TODO` as shipped code. Locatable "without system specific knowledge" by pattern alone.

3. **Aspirator and its yield — a 3-rule checker the authors ran on 9 systems (§5, p. 258–260, Table 9).** 36,745 handler blocks scanned; 121 bugs + 379 bad practices flagged (115 false positives, 19%), 143 already fixed/confirmed by developers despite those projects already running 400-rule FindBugs + error injection (Hadoop [18]). All 4 abort-in-over-catch bugs found could bring down the cluster; 5 HBase/Hive ignore-errors could deadlock or silently lose data (Fig. 12). The point is not cleverness but coverage: a shallow AST rule over every handler catches the patterns code review misses under deadline pressure.

4. **What the paper concedes — so the transfer is not a theorem (§4, p. 257; §5.3, p. 260–261).** Findings are *skewed*: "our findings are skewed in that our study did not expose the many errors that are correctly caught and handled" — correctly handled errors are invisible to failure sampling. 19% false positives, chiefly missing inter-procedural analysis (`catch (E e) {} if (!set()) { handle }` looks empty but handles later) and reflection/`FileNotFound`/shutdown/close/cleanup exceptions that are often intentionally ignored — Aspirator is "further configured to exclude" them. Cassandra, with lowest bug-to-handler ratio, propagates most exceptions to top-level callers rather than handling them (§5.3). Some empty handlers carry comments "Will never happen" / "Why this can happen? Ask God not me." — the authors note feature pressure and belief errors are rare as causes. The prescription is three-pronged, not one-rule: (1) Aspirator-like trivial checker, (2) code-review on handler logic, (3) reverse-engineer tests from handler blocks via symbolic execution (§6). The paper does not claim all failures are shallow; it claims catastrophic ones disproportionately are.

## What this plant already does

*The plant has the bounded-retry shape the paper studies, and it already keeps evidence — unevenly.*

`src/mishe_tauftauf/native_recovery.py` classifies one failed wake from its own bytes: `classify_attempt()` (:374) reads `summary.json`, `journal.jsonl`, three ledger journals, checks `failure.txt` completeness (`_failure_record` :200, `TRACEBACK_HEADER` :112) and decides `SETTLED`/`RETRYABLE`/`RECONCILE`/`UNKNOWN` (:92–95) with `TRANSIENT_STATUS` (:99). `recovery_io.py` and `host_containment.py` are the same pattern at host granularity. The operator doctrine ("No fix without causal investigation … Preserve the original failure evidence", AGENTS.md) is enforced for *verdicts* — a malformed `blocked_state` stays `(None, reason)` not `(None, None)` so broken monitoring reads UNKNOWN (`native_recovery.py:687–698`, `observations.py:61`).

*But the same tree already contains the three trivial patterns the paper names, measured not inferred.* The field sweep for `failure/recovery/rollback` surfaces the opt-in and the checks, but `grep -n "except Exception" src/mishe_tauftauf` shows the density:

- **`src/mishe_tauftauf/engine_rotation.py` — 10 empty-or-pass handlers in one file:** `finally: except Exception: pass` on `flock UN` (:90) and `handle.close` (:94); `_store_for_alias` `except Exception: return None` (:151); `idle` probe `except Exception: is_idle=False` then `except Exception: pass` (:282, :287); journal/pane capture `except Exception: pass` twice (:393, :395); hint decode `except Exception: pass` (:450); launcher mode `except Exception: mode=0o755` (:516). Each swallows the exception that explicitly signals the operation failed and proceeds as if nothing happened — exactly Fig. 5's "Errors ignored (25%)" slice, here in the engine rotation path that mutates the launcher.

- **`src/mishe_tauftauf/observations.py:61` and the `Explorer` family:** `except Exception: pass` on the binding-verdict path — the one place where swallowing would corrupt the verdict, but there guarded by an immediate UNKNOWN return. Elsewhere the file does the opposite: `except (OSError, ValueError, UnicodeError) as exc:` returns `f'reason: {exc}'` so the reader sees the failure (`initiative_watch.py:518–524` names messages, `check_parsing.py` prints violations). The tree therefore already knows the non-ignored idiom — it just does not use it uniformly.

- **Over-catch:** `engine_rotation.py:82 except BlockingIOError` is precise; but `native_resident.py:502,525 except BaseException`, `inference_transport.py:93,220 except BaseException`, `native_recovery.py:676 except BaseException` on `temporary.unlink` and `observations.py:61 except Exception:` catch the over-general class the paper's Fig. 8 shows aborting a cluster. Here they do not abort, but they hide whether the failure was transient or not — the same loss of discrimination the paper traces to catastrophic mis-handling.

- **TODO/FIXME in handlers:** `grep TODO src/mishe_tauftauf` is clean today — this one the plant does not reproduce — but the _shape_ of an empty handler with a comment explaining it away is reproduced as `except Exception: pass` with no comment at all.

The exposure the paper uses is directly computable: of the last 10 `except` sites in `engine_rotation.py`, 6 handle the exception by `pass`/`return None` with no published reason, so a downstream `observe()` can return `settled`/`verified` while the store the reading claims to describe was never successfully read.

## What is new here

**This plant would reproduce the paper's central finding verbatim if it added more recovery before testing the handlers that guard it: the recovery code is present, the error-handler tests are not, and the trivial patterns are already in the path that mutates the launcher.**

Three ideas transfer directly, each with a line number in this repository where it would land, and each bounded by what the paper concedes.

1. **Empty is the bug — same three rules, Python spelling.** The Java/C `empty catch { }` / `catch { log }` is Python `except SomeError: pass` or `except SomeError: log(...)` with no state change and no propagated reason. The HBase `abort on KeeperException` is `except Exception: raise ValueError` or `except BaseException:` that widens the status class (`TRANSIENT_STATUS` :99 distinguishes 408/429/5xx; an over-catch collapses it). The `// TODO LOG` is `except ...: # TODO` / `pass  # will never happen`. Aspirator's heuristic — handler body contains only `pass`, `log`/`print`, or `TODO`/`FIXME` — translates mechanically to an `ast` walk over `ExceptHandler.body`.

2. **Cassandra's good practice is the plant's already-best handler — propagate or name the reason.** The paper notes Cassandra has the lowest bug-to-handler ratio because it "recursively propagates [exceptions] to callers, and they are handled by top level methods" (p. 261). The plant's `observations.py` / `initiative_watch.py` pattern does this: `except (OSError, ValueError) as exc: return ... f'{exc}'` or `return None, f'unreadable: {exc}'` so broken monitoring stays UNKNOWN rather than silently defaulting. The opposite pattern — `except Exception: return None` without a reason (engine_rotation.py:151) — is the one that lets a caller infer "no store" when the truth is "store unreadable".

3. **False-positive budget is a design input, not a defect in the check.** The paper's 19% false positives cluster on reflection, `FileNotFound`, shutdown/close/cleanup, and deferred `if (A_is_not_set())` handling — all cases where `except: pass` is intentional. The transfer must therefore ship with an explicit exclusion list (the same file already has one: `check_parsing.py`'s frozen `parsing-policy.json` budget) and with a per-site `reason` so a reviewer can tell product code from `finally: unlock` cleanup.

**Limits the proposal keeps from the source.** The paper's failures are long-running distributed systems; this plant's wake is single-node with cross-site docs publishing and host cgroups — over-catch here is less likely to bring down a cluster but more likely to silently stall a rotation (`is_idle=False` then `queued`, :282, hides the probe's exception kind). The study's sampling over-represents mishandled errors by design; most handlers in the tree are correct, and the 9-system yield (121 bugs / 36k blocks = 0.3%) cautions against reading an empty-handler count as a failure count. The 100%-statement-coverage detector for the 23% system-specific slice requires a test harness, not just a linter — this review proposes only the shallow lint the paper's Table 9 shows already pays for itself, plus naming the exclusion set so the remaining handlers are reviewable.

## Discard

Discarded: the target `src/mishe_tauftauf/engine_rotation.py` was deleted on purpose in commit 1a7e8ccd ("pane-era, unreferenced", no importers), so the handler-hardening application has no file to land in; the synthetic-fixture check is a separate idea that would need its own review.
