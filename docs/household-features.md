# Household feature store

Mishe keeps a long record of the house as a living system, so that patterns can be found
in it and tested as predictions. This page says what the record holds and what protects it.

**What it is.** `features/house-hourly.jsonl` in the site home: one row per complete UTC hour,
written by the `household-features` duty (every 15 minutes, tool `tools/household_features.py`).
Each row holds derived numbers per room and per device, taken from the camera, microphone and
phone samplers:

- camera: mean light, its minimum and maximum, and a motion number (how much the picture changed
  between consecutive frames); counts of blind and unavailable captures
- audio: how many listening windows, the median and 90th-percentile loudness, and a count of
  each classification the listener gave (silent, no speech, no hint, hint, ...)
- phones: counts of good, stale and unknown readings per device, motion of the device, battery,
  and light where the phone reports it

**What it never holds.** No raw frames, no audio, no text, and no person: the units are rooms and
devices. A device is a sensor channel, not someone.

**Absence is unknown.** If a source gave nothing in an hour, the row has no key for it. A reader
must treat a missing key, and a missing hour, as unknown and never as zero or "quiet".

**Kept for years.** The store is the memory prediction nets learn from. The retention tool refuses `features/` under every
spelling of the path (a test pins this). The raw samplers it reads (`spatial/work/household/*.jsonl`) are not trimmed by anything
today; if that changes, the store must already have consumed them: a row is only ever added for an
hour that is over, and rows are never rewritten.

**Limits.** An hour already written is not revised if a late row arrives for it. A line that is
not valid, or has no usable time, is counted in `features/house-hourly.state.json` and the duty
detail, not repaired.

**Reading it.** One JSON object per line; `t` is the hour's start in epoch seconds, `hour` the same
as text. Series for a prediction net are columns across rows, e.g. `camera["living room"].motion`.

The forecasts built on this store, and the window that shows them, are described in [House predictions](house-predictions.md).
