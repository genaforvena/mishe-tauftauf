# Personal multi-body reading

The human's standing objective is an analysis of themselves across all
authorized Mishe bodies and senses. This directory holds the instrument that
produces that analysis as a **reading**: something re-derivable from the same
bytes on the next wake, not a narrative.

## The instrument

`personal-multibody.py` reads the delivered perception state of every node in
an Android site's space view and prints, per node, what the evidence supports,
what it does not, and which readings are UNKNOWN.

```bash
python research/body/personal-multibody.py --space /home/mesh-home/android-body/.mishe-tauftauf
python research/body/personal-multibody.py --space <site home> --json
```

The tests are `tests/test_personal_multibody.py`:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/test_personal_multibody.py -q
```

The tool never writes, never mutates the site it reads, and never phones
anything. It only reads files that already exist.

`clock_bound.py` is the sibling instrument that measures what the delivered
lane can say about a phone's clock. The reading above uses it for the clock
bound; it can also be run on its own, and it emits a clock-domain document
that carries the bound and no offset:

```bash
python research/body/clock_bound.py --space <site home> --node redmi10
python research/body/clock_bound.py --space <site home> --node redmi10 \
    --emit-document <path>/clock-domain-redmi10.json
```

Its tests are `tests/test_clock_bound.py`.

## Sources

| source | what it carries |
|---|---|
| `<site>/body/space.json` | the node view: delivery state, clock domain, stillness verdict |
| `<site>/body/perception/<node>.json` | the delivered, bounded history window |
| `<site>/body/clock-domain-<node>.json` | the measured phone/host clock offset, or the reason there is none |
| `<site>/body/perception/events.txt` | dated channel-state changes |
| `<site>/body/queues/<node>.observations.log` | the delivered observation history: `SEQ <phone clock> <kind> ...` |
| `<site>/body/queues/writes.jsonl` | the host's accepted-POST journal, with the host receipt time of every delivery |

The last two are what make the reading longitudinal. The bounded window alone
is a few seconds wide; the delivered queue holds the whole history the body
produced, and the host journal holds when it arrived.

## Gates

A reading is only worth as much as the refusals it makes. These are stated so
a reader can check them rather than believe them.

- **Current window** - a node contributes to a current reading only when its
  channel delivery is `pass` and its rows are newer than `--max-age`.
- **Fusion** - a cross-body claim needs *every* node to hold a current window;
  otherwise the fused reading is UNKNOWN. A stale body is not a present one.
- **An unavailable channel is never a quiet channel** - rows that carry a
  reason instead of a value (the accelerometer `stale`, the wifi API
  `disconnected`) are reported as unavailable, with the reason. A channel that
  says why it has no value has not been observed to be quiet.
- **A clock document without a measurement is not an absent document** - its
  status and reason are reported, so the missing prerequisite is named instead
  of lost.
- **The delivered lane bounds the phone's clock from above, and only from
  above** - a node with no measured offset still gets a bound: the phone's
  clock is at most *N* seconds behind the host. How far it may be *ahead* is
  not bounded, so the reading says that too instead of implying a two-sided
  interval.
- **The lane's lateness is a distribution, not a number** - a row's age comes
  from that row's own receipt. When the lane's backlog moves, no single
  session-level figure can age the session, and the reading says so instead of
  publishing one number that reads as if it applied to every row.
- **Motion labels need gait resolution** - at 5 s per sample the 1-2 Hz gait
  band is aliased away, so no activity, motion or posture label is
  supportable however stable the magnitude is.
- **Stillness is not presence** - the composite stillness verdict is not a
  presence or occupancy claim, and it is UNKNOWN whenever one of its inputs is
  unusable.

## Continuity: which silence, and whose

A gap in the record has a cause, and the delivered lane holds two clocks that
separate it into two kinds of silence.

- A **body hole** is an interval across which *the body produced no
  observations*. It is measured on the body's own clock in the delivered
  queue. A hole is a forward jump *beyond one expected cadence step*, so an
  ordinary sampling interval is never a hole however the threshold is set.
- A **link hole** is an interval across which *the host received nothing*. It
  is measured on the host's clock in the accepted-POST journal.

The two are on **different clocks**, so they are only compared after an
alignment. The clock document's convention is `phone clock - host clock`, so
a phone reading `p` is the host instant `p - offset`:

- where the offset is **measured**, the body's readings are converted to host
  instants and the comparison is exact within the measured bounds;
- where it is **not measured**, there is no supported alignment at all. The
  comparison is widened by the body's own observed clock instability - the
  largest backward step its clock has taken - and every verdict names the
  clock difference that would overturn it
  (`verdict_holds_unless_clock_differs_by_s`).

The verdicts:

| the body | the link | verdict |
|---|---|---|
| produced nothing | kept receiving | **body-side**: the body itself stopped |
| produced nothing | received nothing across any part of the interval | **not separable**: the delivered lane cannot say which came first |
| produced nothing | *unmeasured* - the host journal cannot say | **UNKNOWN**: an unmeasured link is not a live link |
| kept producing at its cadence | received nothing | **link-side**: the observations were buffered and delivered afterwards, so the data is recovered and no consumer saw it live |
| produced fewer than its cadence | received nothing | **partial**: the body faltered as well as the link |

Two coverage numbers follow, and they answer different questions, each in its
own clock:

- `observation_coverage` - the fraction of the body's own span in which it was
  producing observations, counting every gap beyond one cadence step however
  short it is. A link outage does not reduce it, because those observations
  were recovered afterwards.
- `live_delivery_coverage` - the fraction of the host's own span in which it
  was receiving. UNKNOWN when the host journal holds no posts for the node.

A queue whose sequence is not contiguous is refused outright: rows are
missing, so continuity cannot be inferred from it.

## Clock identifiability

**The phone's clock offset cannot be recovered from the delivered lane.** A
delivered row carries the phone's own clock reading and the host's receipt
time, and a constant transport delay produces exactly the same bytes as a
constant clock offset. The two are degenerate. Only a third time reference -
the phone's clock read at a known host instant, which the clock-domain
document measures - can separate them, and that document needs a path to the
phone.

This is why the redmi10's clock document reads `unavailable` with a reason,
and why no reading here reports an absolute phone time for it. The instrument
reports the observed lag between the phone's sample clock and the host receipt,
and names it as a lag, not an offset.

## The one-sided bound the lane does support

A delivered row carries the phone's own clock reading and the host's receipt
time, and their difference is

```
observed = host_receipt - device_epoch = (how far the phone's clock is behind) + (transport delay)
```

The transport delay cannot be negative, so **every row bounds the phone's
clock from above**: at that row, the phone's clock was at most `observed`
behind the host. The lane does not bound the other side - how far the phone
may be *ahead* - and a device-side clock read is what would.

Two figures are published, and they answer different questions:

- the bound at the **freshest delivered sample**, which holds with no premise
  at all - and is withheld when the lane cannot establish which row was
  produced last;
- the **session-wide** bound, which holds at every row of the session that
  carries the phone's own monotonic reading, and which carries the premise it
  rests on: that monotonic clock is assumed to track true time, and that the
  rows carrying the reading share one monotonic offset. Neither is testable
  from the lane, so both are published with the bound.

The premise-free reading is the one to trust. The session-wide figure is
tighter, and the reason it is published with its premise is that a reader who
does not see the premise will read it as a fact. When the lane cannot
establish the order of the rows, both figures are withheld and the tightest
per-row bound the session's rows support is published instead, because that
one needs no chronology.

### What the lane's own shape does to the reading

Three things about the delivered lane are easy to mistake for clock events,
and the instrument measures them instead of assuming them:

- **A delivery batch is written newest first.** The last row of a batch is
  the *oldest* row of that batch, so the freshest sample is the row with the
  greatest phone uptime, not the last row in the file. A batch also makes the
  phone's uptime counter *decrease* from row to row; a decrease that leaves
  the counter large is a reordering, not a reboot.
- **A reset and a reordering look alike in the delivered order.** A reboot
  resets the uptime counter to a fresh-boot value and re-reads the wall clock,
  so the rows on either side of it do not share one monotonic offset - but a
  reverse-order batch of the device's first minutes produces the same bytes.
  A fall that lands on a fresh-boot value is therefore recorded as
  *ambiguous* and withholds the session-wide bound, rather than being
  resolved by assuming one reading. A fall that shows up only when the rows
  are ordered by the phone's own clock is a reset the delivered order would
  have hidden, and it withholds the bound too.
- **A late lane is not a wrong clock.** `observed` is the clock error plus the
  delivery delay, and the two cannot be separated from the delay alone. The
  phone's own monotonic reading is what separates them: if the wall clock
  tracked it, the staleness of the timestamps is the lane's lateness. So the
  instrument publishes the drift of the wall clock against the monotonic
  clock, and the lower bound on the delivery delay that follows from the
  clock bound - on the redmi10's live lane, a wall clock that moved at most
  1.305 s against its own monotonic clock, and rows delivered at least
  18539.9 s after the phone produced them (a lower bound that follows from
  the clock bound; it is not a measurement of the lane's delay, and it grows
  as the lane's backlog grows).

### The lane's lateness is a backlog, not a constant

One worst-case figure is not what a consumer of the lane has to cope with. A
lane that is usually prompt and occasionally hours behind is a different
animal from a lane that is uniformly late, and the two need different
handling. The instrument therefore publishes a `lateness` profile per session:
the spread of `observed` over the rows that carry the phone's monotonic
reading, the median and the 95th percentile, how many rows are more than a
minute old, and the worst-case residual of *one* constant correction.

Measured on the redmi10's live lane (session `af9d723f`, 2026-10-21):

| figure | value |
|---|---|
| rows covered | 56516 |
| observed lag, smallest | 0.06 s |
| observed lag, median | 3.6 s |
| observed lag, 95th percentile | 12752 s |
| observed lag, largest | 18541 s |
| rows more than 60 s old | 23203 (41%) |
| worst residual of one constant correction | 18542 s |

So the transport floor is about 0.06 s - the lane can deliver a row
essentially at once - and the staleness is a queue backlog that builds and
drains. One figure cannot age the session; a row's age has to come from that
row's own receipt. In an earlier session of the same lane (`bf9d0097`) the lag
was steady at 21.9 s median with a 15.8 s spread over 7.2 h, so the backlog is
episodic and where it is absent one correction ages the session's rows to
within 16.8 s - the spread of the lag plus the whole-second slack of the
device epoch - instead of the 18542 s the live session's backlog costs.

The profile is about *relative* age. The absolute age of a row is still not
recoverable from the lane - that is the identifiability limit above - and the
profile does not claim it.

Where the third reference *does* exist, the lag does decompose. The clock
document's offset is `phone clock - host clock` and the lag is `host receipt -
phone clock reading`, so the transport delay is the lag plus the offset. The
note3 is that case: its offset is measured at about -1.4 s and its observed
lag is about 28 s, so its transport delay is about 26.9 s. That is a real
transport delay, not a clock error, and the reading says so instead of
calling the node's timestamps untrustworthy.

The consequence for continuity: a hole is a statement about the body's own
clock. Where that clock is measured, the hole is anchored to the host's
timeline within the measured bounds (`clock_anchor: anchored`). Where it is
not, the hole carries the phone's clock as an unverified premise
(`clock_anchor: unanchored`), and a forward clock step cannot be excluded.

## Limits

- The queue and the journal are the Android site's files. The tool reads them
  as they are written; a queue still being appended to is read to whatever
  its last complete row is, so the newest interval can grow between runs.
- A body's own clock may step backwards. The tool measures how often and by
  how much, and uses the largest backward step as the floor on the
  uncertainty of any comparison between the body's clock and the host's.
- Malformed queue rows are counted and kept as samples in the report, not
  dropped: a queue that ends in unreadable rows is a fact about the delivery.
- The cadence channel is the busiest channel in the queue, with the channel
  name as a deterministic tie-break. A body whose channels sample at
  different rates would be described by one of them.
- The link holes are measured on the host's clock and the body holes on the
  phone's, so an overlap between them is an overlap of two clocks, not a
  single timeline.
- Nothing here supports a claim about a person's activity, location, health or
  mood. The channels carry light, acceleration, battery and link state; they do
  not carry those answers.
