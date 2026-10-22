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

## Every row has its own age

A consumer that wants to know whether *this* observation was live must not
re-derive the pairing - walk backward from the live tail, because every
receipt states how many rows its POST appended - and get it wrong the way two
implementations do. So the instrument publishes the pairing:

```bash
python research/body/clock_bound.py --space <site home> --node redmi10 \
    --emit-age-index <path>/age-index.jsonl
```

One JSON line per delivered POST, with the sequence range it carried, its
receipt time, and the age of every row it carried. Because every row of one
POST shares that POST's delivery age, the index *is* the per-row age table in
compressed form: a consumer looks a row up by its sequence number and reads
its age there.

The reading publishes the same thing for the rows a person actually reads:

- **the current window's rows**, each with its own age, its own lag, and the
  reference instant the age was measured against;
- **the delivered history's ends**: how old the newest delivered row is, how
  old the oldest is, and how far back the recovered observations reach.

Three ages, and the difference between them is the lane's backlog:

| age | what it is |
|---|---|
| the delivery age | how long the host has held the row; exact on the host clock, and the **lower bound** of the observation's own age |
| the row's own lag | `host receipt - device epoch`: the phone's clock error plus the transport delay, which the lane cannot separate |
| the sample age | the sample's age in the phone's own clock frame; between two rows of one session the difference is the true age difference, because the same clock's offset cancels |

Two receipt instants, and they are not the same instant: the **journal**
records when the host *accepted* the POST, and the **window** records when
the perception reader *read* the page that carried the row. The reading
measures the gap between them for every row they share, so the reader's own
poll delay is a measured number (0.5-1.5 s on the redmi10's live lane at
2026-10-21) rather than an assumption.

The limit that does not go away: the lane does not recover the transport
delay, so it does not bound a row's age from above, and it does not bound how
far the phone's clock may be ahead of the host.

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
- **A row's age is the age of its delivery** - the delivery age is exact on
  the host clock and is the lower bound of the observation's own age. The
  observation is older by that row's transport delay, which the lane does not
  recover, and the lane does not bound how far the phone's clock may be ahead
  of the host, so it does not bound the observation's age from above.
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

## The human's day, hour by hour

The human's own question is not "how old is this row" but "was my evening in
my history, and when did it reach you". So the reading publishes the day
itself, per hour, in the reading's `day_shape` section.

Two clocks, and they are not merged:

- the **phone's own clock** (`device_epoch`) decides the hour a row belongs
  to, because the hour is the human's day;
- the **host's clock** decides when the row arrived, and the difference
  between the two is the row's wait.

Every hour carries its production count, how many of its rows arrived within
the backlog window and how many after it, the worst wait in that hour, and
the acceleration channel's **coverage** for that hour: how many of its
acceleration rows carried a sample, how many did not, and the producer's own
reason for each one that did not.

That last part is what keeps the table from being misread. A row delivered
without an accelerometer sample is a fact about the **producer's** freshness
check, not about the human's motion, so an hour in which the phone produced
no sample is not an hour in which the human was still. The reading names the
hours that carry no sample at all, and says so in terms a consumer cannot
mistake for a measurement of stillness. The coverage denominator is the
acceleration rows of that hour, not every row the body produced - the other
channels carry values on every row, so mixing them in would report a healthy
hour as a quarter covered.

The body's own silences are published as **intervals** beside the table
rather than as missing rows, because an hour the body produced nothing in is
a different fact from an hour whose rows all arrived late. On the redmi10's
live lane at 2026-10-22T04:06Z (81 phone-clock hours, 227,092 rows):

| fact | value |
|---|---|
| hours the body produced nothing at all | 82 (2026-10-16 21 → 2026-10-20 06, phone clock) |
| acceleration coverage over the history | 49.4% |
| hours carrying no accelerometer sample at all | 10 |
| rows that reached the host more than 2 h after production | 24,515 |
| worst wait | 18,540.7 s |

The backlog and the silence are separate facts, and the hour table keeps them
separate: hour `2026-10-16 10` has **full** acceleration coverage and all
2,868 of its rows still arrived late, while hour `2026-10-16 09` has 3.9%
coverage and 2,762 late rows. An hour's coverage says what was sampled; its
wait says when it reached the host. Neither one alone is the human's day.

The reading deliberately does **not** attribute the long sample-less runs to
the producer's threshold, or the 82-hour silence to the phone, the link or the
human. Those are separate questions with separate owners, and the lane does
not hold the evidence to answer them.

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
