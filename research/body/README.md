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
