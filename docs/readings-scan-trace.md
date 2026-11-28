# Reading a readings scan's stage trace

The readings scan writes one timestamped line per stage while it runs. When a scan is
slow or cut, these lines say where it got to. Set `site` to the absolute path of your
site home.

## Where the files are

Each scan has its own id, and its files are keyed by that id under `$site/signals/`:

- `readings-scan-trace-<scan_id>.txt`: the stage trace, described below.
- `readings-scan-stack-<scan_id>.txt`: the thread stacks, written only if the scan
  is still running 270 s in. The readings producer cuts a scan at 300 s.

A scan that finishes cleanly removes its trace, unless its snapshot stage took more than
30 s (`SLOW_SNAPSHOT_TRACE_S`). A trace is also kept when the producer kills the scan,
or when the stack dump fired, because that trace is the only stage evidence of the slow run.

A clean scan does not leave its timing behind, so it also appends one JSON line to
`signals/readings-scan-timing.jsonl`, with `scan_id`, `snapshot_s` (seconds the
snapshot stage took), `total_s` and the host lines at the snapshot's start and end.
Use it to see the snapshot cost of ordinary scans; use the trace only for slow or
cut ones.

## One line per stage

```
1747391234.517 +0.004s enter | wbytes=18432 psi_mem=0.00 psi_io=3.21
```

(The values above are illustrative.) The fields, in order:

- **Epoch seconds** (three decimals). It lines up with the producer's
  journal `start=` time, so you can split time before the scan started from time
  inside it.
- **`+N.NNNs`**: seconds since the scan began, on a monotonic clock.
- **Event**: the stage name (below).
- **`wbytes`**: bytes this scan process has written to storage so far. A stall that
  moves `wbytes` is the scan's own writes.
- **`psi_mem` and `psi_io`**: the host's memory and I/O pressure, the PSI `avg10`
  values at that moment. A value shown as `?` means that file could not be read; the
  scan does not fail for it.

Each line is opened and closed when written, so a killed scan still keeps every stage
it reached.

## The stages, in order

1. `enter`: the scan started.
2. `sample:start` and `sample:done`: the host sample for the readings. Between them the
   sample writes its own stages, in this order: `stage:snapshot:start` and
   `stage:snapshot:done` (the one tape snapshot), then `stage:tape-readings` and
   `stage:tape-readings:done` (the whole-tape readings), with the evidence stages —
   `stage:evidence:named-records`, `stage:evidence:census` and `stage:evidence:hash`
   (each `:start` and `:done`) — reported inside the tape-readings window, then
   `stage:patch-invariant`, `stage:patch-dv-binding`, `stage:local-readings` and
   `stage:body-network-readings` (each `:start` and `:done`), then
   `stage:observed:release-retention:start` and `stage:observed:release-retention:done`
   (the release-retention walk over `releases/`). A cut between
   `sample:start` and `sample:done` is named by the last of these lines.
3. `publication`: the receipt was written.
4. `notification`: the scan's notification was sent. A failure here does not roll back
   the receipt.

If the scan raises, the trace ends with `failed <ErrorType> at <stage>`, naming the
last stage the attempt reached.

## Reading a cut scan

- A trace that stops after `sample:start` with no `sample:done` means the cut landed
  inside the host sample.
- A trace that stops at `enter` means the scan never reached the sample.
- Compare `wbytes` across the lines: a large jump inside one stage points at that
  stage's writes.
- If `psi_io` is high on the same lines, the host was under I/O pressure; if
  `psi_mem` is high, memory pressure. Both high at once is not enough to name a cause
  on its own. Check the stack dump for which call was blocked.

The stage trace records what happened, not why. The stack dump names the blocked
call when it exists.
