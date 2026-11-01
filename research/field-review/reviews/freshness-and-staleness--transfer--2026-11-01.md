---
field: freshness-and-staleness
angle: transfer
date: 2026-11-01
status: discarded
owner: senses
---

## What we read

**HTTP Cache-Control Extensions for Stale Content** — Mark Nottingham (Yahoo! Inc.).
RFC 5861, Independent Submission, Informational, May 2010.
<https://www.rfc-editor.org/rfc/rfc5861.txt> — read §1 (Introduction), §3
(`stale-while-revalidate`) and §3.1 (Example) in full, from the rfc-editor text.
The §4 `stale-if-error` definition was read only as stated in §1; I did not read
§4.1 or §5 (Security Considerations), so no claim below rests on them.

Three passages carry the transfer:

1. **§1 (p. 1):** a cache may return a stale response "while it revalidates it in the
   background, thereby hiding latency"; `stale-if-error` returns a stale response when
   the origin errors, "rather than returning a 'hard' error."
2. **§3 (p. 2):** a stale response served under this extension "implies that the response
   will have a non-zero Age header and a warning header" — the staleness is labelled on
   the response itself, not silently passed off as fresh. The extension also says that if
   delta-seconds passes without revalidation, the response "SHOULD NOT continue to be
   served stale, absent other information."
3. **§3.1 (p. 3):** with `max-age=600, stale-while-revalidate=30` the stale window is
   finite; after it "the cached response will be 'truly' stale (i.e., the next request
   will block and be handled normally)."

**Limits the source states and this review keeps.** The RFC is a cache-freshness rule for
HTTP responses whose consumers accept a labelled old answer. It does not decide whether a
health verdict may be answered from an old observation.

## What this plant already does

The transfer target is the top-pane lease reader, `pane_lease_lines`
(`src/mishe_tauftauf/wall_view.py:42`). It already has the three properties §3 asks for:

* **The age is labelled on the verdict.** A stale renderer produces
  `PANE LEASE: RED stale=<window>(age=<N>s>limit=<M>s)` (`wall_view.py`, the `if stale:`
  branch after line 100), so the staleness and its bound are visible in the line itself.
* **The window is finite and named.** `limit = max(120.0, 6 * refresh)` is the "truly stale"
  point of §3.1. Past it the verdict is RED, not an extended grace.
* **Unreadable, missing and malformed leases are not served as old answers.** They go to
  `unreadable` or `missing`, and the reader returns `PANE LEASE: UNKNOWN` for them
  (`wall_view.py:105-106`), after the stale check. Doctrine already requires this: "Missing, failed or stale
  evidence remains UNKNOWN or RED."

The consumers of the lease also already refuse to treat the pane as healthy by default:
`wall.tick` (`src/mishe_tauftauf/wall.py:1004`) and `_leases` (`coordination/site_sync.py:282`)
each compare the stamp to a literal and report stale instead of carrying the last good value.

## What is new here

Nothing that changes the plant. The two behaviours the RFC offers are:

* **`stale-while-revalidate`** — serve the last good value, labelled, while a fresh read runs
  in the background. The plant's reader is already synchronous and bounded by a 2-second
  `tmux capture-pane` timeout, so background revalidation would add a second clock and a
  second place a stale value can hide, while the verdict it would serve is already labelled
  with its age.
* **`stale-if-error`** — serve the last good value when the read fails. This is the
  transfer that would be wrong here. The plant's doctrine says a failed read is UNKNOWN or
  RED, and that a stale verdict must not be used as present evidence. Serving an old lease
  when `capture-pane` fails would convert an unreadable renderer into a green-looking
  carry-over, which is the masking failure the doctrine names. The RFC's own use of
  `stale-if-error` is to hide an origin error from a client that accepts an old answer; the
  plant's clients are the operator and the minds, and they must see the error.

The one thing the RFC does say that the plant lacks is a *published* stale count across the
window. That is a metric, not a freshness rule, and it is covered by the `metric` and
`misread` reviews of this field (2026-10-29 and 2026-10-22).

## Discard

The transfer is already embodied: `pane_lease_lines` labels stale age and limit, bounds the
window, and returns UNKNOWN for unreadable leases; `stale-if-error` would mask the failure
the doctrine forbids, so it is not applied.
