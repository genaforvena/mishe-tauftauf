# House predictions

Mishe treats the house as one living system and tries to predict it. A pattern is only believed
once it has predicted something that then happened, so every forecast is written down before the hour
it is about, and scored after.

**What is predicted.** Seven numbers from the [household feature store](household-features.md): light
and motion in the living room and the bedroom, and in the living room the median and loud end of the sound
level and the share of silent listening windows. Rooms, never people.

**Two plain guesses come first.** `last-value` assumes the next hour looks like the last complete one.
`same-hour` uses the usual value for that hour of the week (for that hour of the day until there are three
weeks of history). Each run forecasts the next hour and the same hour tomorrow. Any later net has to beat
both, on forecasts made in advance, before it counts.

**The log cannot be quietly changed.** `features/predictions.jsonl` is append-only and every line carries
the hash of the line before it. If a line is edited or removed the chain reads `BROKEN` in the window. A
forecast is never made from missing data, and an hour that was not measured is `unobserved`, never an error
of zero. A store whose newest hour is over four hours old makes no forecast and reports UNKNOWN.

**Live and replay stay apart.** The replay walks through stored history using only earlier hours. It gives
the first scoreboard immediately, but it is retrospective and says nothing about live skill. The live board
fills one hour at a time.

**A surprise** is a miss of at least three times a net's usual error, by a net that already has 24 scored
forecasts. The window lists the latest ones; they are where to look for something the house did that the
usual pattern did not expect.

**Where to look.** The outer `house` window (see [Outer windows](outer-windows.md)) prints the scoreboard.
The files are in `features/`: `predictions.jsonl` (the log), `predictions.state.json` and
`predictions-score.json` (both derived and rebuildable from the log). The duty `house-predictions` runs the
tool `tools/house_predict.py` every 15 minutes.

**Limits.** Two baselines and seven targets are a start, not a finding. With about two weeks of history the
weekly rhythm is not yet visible, so `same-hour` is mostly a daily rhythm. A forecast made while a sensor was
down is simply absent. The log and score are derived numbers only.
