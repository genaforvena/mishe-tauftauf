---
field: engine-model-routing-and-usage
angle: misread
date: 2026-11-02
status: applied
owner: genome
target: src/mishe_tauftauf/engine_policy.py
---

# Reserve as tail-drop: what Floyd & Jacobson RED says the plant misreads about "threshold"

## What we read

- Floyd, S. and Jacobson, V., "Random Early Detection Gateways for Congestion Avoidance", IEEE/ACM Transactions on Networking, Vol.1 No.4, August 1993, pp.397-413. Primary source read as the author-hosted PDF https://www.icir.org/floyd/papers/early.pdf (312 KiB, sha256 6d0243cdccb96646f57d1f8af4e2e8ef60e9f0b267aa61a98634ddcc57bfe263, fetched 2026-11-02) plus the index HTML https://www.icir.org/floyd/papers/red/red.html (1964 B, sha256 b3c690564aab6cbbcb65c79de26bd1b27474af8d9b8a8d375bf517f2180367fe). Text via `pdftotext` 92149 B retained at /tmp/red-floyd.pdf, /tmp/red.html, /tmp/red.txt.
- Points used (paper's own wording via pdftotext, §1/§4/Fig.1-2, §6-7):
  - Abstract p.1: RED "detects incipient congestion by computing the average queue size" and "When the average queue size exceeds a preset threshold, the gateway drops or marks each arriving packet with a certain probability, where the exact probability is a function of the average queue size." Keeps average low "while allowing occasional bursts".
  - Algorithm Fig.2 p.7 (Detailed algorithm): EWMA `avg <- (1-wq)*avg + wq*q` if queue nonempty else `m <- f(time - q_time); avg <- (1-wq)^m * avg` (idle decay by packet-time count), saved `avg`, `q_time`, `count` (packets since last marked). Thresholds `minth`/`maxth`, `max_p`. Inside band `minth <= avg < maxth`: `pb <- max_p*(avg - minth)/(maxth - minth)` (linear in avg) and uniform-spacing `pa <- pb/(1 - count*pb)` with `count` increment and reset on mark. Above band `maxth <= avg`: mark every arrival, `count <- 0`. Below band: `count <- -1`.
  - §6-7 parameter discussion: `wq` sets low-pass time constant (bounds via burst size/duration; power-of-two choice for efficiency), `minth`/`maxth` set desired average queue (tradeoff throughput vs delay left open), `pb` linear in `avg`, `pa` ensures inter-mark gaps uniform rather than geometrically clustered. Bytes-vs-packets variant: `pb <- pb*PacketSize/MaximumPacketSize`.
  - Stated limits in same text: FIFO assumption ("assumption that it will continue to be useful to have queues at the gateway where traffic ... is multiplexed together, with FIFO scheduling"), cooperation assumption ("RED gateways are intended for a network where the transport protocol responds to congestion indications", single marked/dropped packet sufficient), and evaluation is for long-lived FTP/Tahoe TCP with large windows against Drop Tail (§5 Fig.3 params `wq=0.002, minth=5, maxth=15, max_p=1/50, buffer >> maxth`).
- Quoted to separate from Tail Drop: §8 Fig.5 contrasts Drop Tail (queue fills to buffer, throughput rises with larger buffer, average queue tracks buffer) vs RED (average controlled independent of buffer, burst-tolerant). The critique of instantaneous-queue gateways is that they either keep queue full or drop only on overflow — the plant's current policy is the latter.

Distinct from predecessor 2026-10-31 misread (Hespanha & Morse 1999 CDC, https://web.ece.ucsb.edu/~hespanha/published/avedwell.pdf, dwell-time `S[τD]/S_ave[τD,N0]` vs hysteresis margin `(1+h)·π_q <= π_σ` and `τD=log(1+h)/(2λm)`): different venue/year (ToN 1993 vs CDC 1999), different equations (EWMA/avg/minth/maxth/pb/pa vs dwell/hysteresis), different figure family (Fig.1-2 RED gateway vs Fig.3 hysteresis supervisor), different failure addressed (burst bias/global sync vs chatter bound). Source bytes/shas disjoint (early.pdf 312 KiB 6d0243... vs avedwell.pdf).

## What this plant already does

- `src/mishe_tauftauf/engine_policy.py:19-36` header: admission from `effective = remaining_fraction - reserved` (via `_effective_reserved` :93-117) compared to fixed `reserve_pct` 0.15 (`_classify_account` :119-172: `eff <= 0` depleted, `0 < eff < reserve_pct` reserve, else healthy; provider `depleted` status also gates). `UNKNOWN`/`STALE`/`UNATTRIBUTED`/`NOT_CONFIGURED` in `_INELIGIBLE_STATES` cause `hold`, not exhaustion. Hysteresis branch :401-448 returns `stay` for compatible healthy current with `next_reconsider_ms = now + min_hold_ms` (600000 ms) and comment "irrespective of alternative headroom".
- The decision is deterministic on one snapshot: first healthy same-provider alias, then first healthy in list order. No queue history, no averaging, no early probabilistic fallback. `queue` is produced only when the eligible set is empty or `reserve` under `fail-closed`/`auto`.
- `src/mishe_tauftauf/engine_usage.py:151-160` `_save_last_good` overwrites `engine/last-good.json` with the latest `samples` (`{"version":1,"accounts":samples}`) — one sample per alias, no time series. `checks/shipped-book-*` and `research/field-review/reviews/engine-model-routing-and-usage--metric--2026-10-31.md` already note a burn-rate needs history the file does not keep.
- Tests `tests/test_engine_policy.py:253-265` pin `stay` at `+1s`; the metric/mechanism reviews note the rate path is unmeasured. The quota read is instantaneous `remaining_fraction` in `[0,1]`, not a queue length `q` in packets.

## What is new here

The plant misreads "reserve threshold" as a tail-drop condition. Against RED the misread is exact:

1. **Instantaneous level vs average.** The plant gates on instantaneous `effective` (`q` analog), the case RED diagnoses as Drop Tail: bursts force either needless fallback or late fallback only at overflow. RED's correction is to filter with `avg <- (1-wq)avg + wq q` (and idle decay `avg <- (1-wq)^m avg`), comparing `avg` to bands, not `q`. The plant has the quantity to filter (polls via `engine_usage.status`) but never forms `avg`; bursts in consumption (several wakes spending quota between polls) are invisible until the next snapshot crosses 0.15.

2. **Hard cut vs graded early signal.** The plant's reserve branch is binary. RED's inside-band signal is graded `pb = max_p*(avg-minth)/(maxth-minth)` rising linearly from 0 at `minth` to `max_p` at `maxth`, so fallback probability anticipates exhaustion. `pa = pb/(1-count*pb)` then uniformises inter-fallback gaps so successive `queue`/`fallback` decisions are not clustered. The plant's hard `0 < eff < 0.15` cannot produce that graded anticipation; the critique review's Goodhart boundary (eff == reserve_pct) is the point where Drop Tail's single threshold is most brittle.

3. **Bias and synchrony the paper proves.** RED's simulations show Tail Drop biases against bursty sources and synchronises flows that happen to hit the threshold together. With deterministic same-provider-first ordering, several roles sharing one snapshot (transfer review's herd, latent with single `default` role today) would herd onto the same fallback alias once its instantaneous level recovers — the exact pattern `pa`'s uniform spacing is designed to break.

The analogy's limits are stated in the source and apply here: RED assumes a FIFO aggregate queue where `q` multiplexes many flows over time; the plant's "queue" is a provider-side entitlement with `reset_at`, not packet backlog, so `q` is `1 - effective` in fraction units and the transport that "backs off" is the plant's own `decide` (no TCP window to halve). RED's single-drop sufficiency and throughput/delay tradeoff depend on cooperative window reduction; the plant's `hold`/`queue` distinction on `UNKNOWN/STALE` has no RED counterpart except idle decay.

## Application

target: src/mishe_tauftauf/engine_policy.py

Proposed to genome as additive docs-only header note (no logic/threshold/hysteresis change), recorded under a `RED (Floyd & Jacobson 1993) — misread note` heading in the module docstring (after the hysteresis paragraph, before `Inputs`):

- Cite Floyd & Jacobson, "Random Early Detection Gateways for Congestion Avoidance", IEEE/ACM ToN 1(4) Aug 1993, author PDF https://www.icir.org/floyd/papers/early.pdf (312 KiB sha256 6d0243cd..., HTML index b3c690...), Fig.1-2 and §6-7, with the four instruments verbatim: `avg <- (1-wq)avg + wq q` else `avg <- (1-wq)^m avg` via `m=f(time-q_time)`, `minth`/`maxth`/`max_p`, `pb <- max_p*(avg-minth)/(maxth-minth)`, `pa <- pb/(1-count*pb)` with `count` resets on mark/else branch, plus the bytes-variant `pb <- pb*PacketSize/MaximumPacketSize` as the analogue of weighting a fallback by estimated spend.
- Record the receiver gap explicitly: `effective` is today an instantaneous poll (`engine_usage.last-good.json` keeps one sample/alias, no `avg` history), so no `wq`/`minth`/`maxth` comparison is computable in this change; graded early fallback (`pb`/`pa`) and idle decay (`q_time`/`m`) require per-window time series that the metric review flags as missing persistence, not one-line arithmetic.
- Retain current semantics (`UNKNOWN/STALE` → `hold` ineligible; `eff <=0` depleted, `0<eff<reserve_pct` reserve else healthy; stickiness irrespective of headroom) — this review does not turn the hard reserve into `pb` nor rewire `engine_watch.py:264` reservations. Future work that introduces a quality/time signal and a window time series should evaluate a RED-style `avg` band and uniform-spacing `pa` vs the single 15% point, measuring burst tolerance and herd/fallback clustering rather than asserting them.
- Do not import RED's throughput/delay claim: note the paper leaves `minth`/`maxth` sizing open and evaluates only long-lived Tahoe TCP; any `wq`/`minth` sizing for entitlement fractions needs its own justification and live `decisions[role].decision`/`next_reconsider` observation.

## Limits

- No live average was computed; no `wq`/`minth`/`maxth` fitted to quota resets; no `pb`/`pa` simulated or run through `engine_watch`.
- The plant's `remaining_fraction` is not `q` in packets, so numeric `minth=5, maxth=15, wq=0.002, max_p=1/50` from §5 do not transfer; only the structure (EWMA, bands, linear `pb`, uniform `pa` via count, idle decay) is the instrument.
- Source covers Drop Tail vs RED gateways buffering packets; adverse analysis for entitlement depletion windows is an analogy with the FIFO and cooperation limits above.
