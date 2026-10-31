---
field: wall-patches-and-operator-views
angle: mechanism
date: 2026-10-31
status: applied
owner: senses
target: src/mishe_tauftauf/wall.py
---

# Wall patches and operator views — mechanism: a filtered view must state how much it hid

## What we read

- Ben Shneiderman, "The Eyes Have It: A Task by Data Type Taxonomy for Information Visualizations", Proc. IEEE Symposium on Visual Languages (VL '96), Boulder, September 1996, pp. 336–343. Source read: a university-hosted copy of the proceedings paper, https://webspace.science.uu.nl/~telea001/uploads/VACourse/Shneiderman96.pdf (9 pages). The text was extracted locally with `pdftotext -layout`, because the fetch tool could not read its compressed streams. The IEEE publisher page was not fetched, so the page numbers above are the proceedings' own numbering from the copy.
- Passages read: the mantra on p. 1 ("Overview first, zoom and filter, then details-on-demand. But this is only a starting point"); the tasks list (overview, zoom, filter, details-on-demand, relate, history, extract); the overview-plus-detail passage ("Adequate overview strategies are a useful criteria to look for"); the Overview task ("Gain an overview of the entire collection"); the Details-on-demand task ("Once a collection has been trimmed to a few dozen items it should be easy to browse the details"); and the limits ("The seven tasks are at a high level of abstraction"; "This taxonomy is useful only if it facilitates discussion and leads to useful discoveries").

## What this plant already does

- The top of the MIND WALL pane is an overview: `wall.py` `pane()` puts the deterministic follow-through lines and the wall's own text ahead of the detail sections, with the banner "sensor truth is above". The dashboard headline is the first overview.
- A details-on-demand pointer exists: the CHAT.LOG header names the full tape (`home / "chat.log"`), and each preview is cut at 360 characters.
- Filters exist: `pane()` shows the last 3 addressed messages (`entries = [...][-3:]`) and the last 8 non-seed events (`recent = [...][-8:]`).
- The wall skeleton line from the earlier transfer review is in the working tree as uncommitted `wall_skeleton_line` (senses' in-flight edit, 14 insertions in `wall.py`). This review does not touch it.

## What is new here

Shneiderman's mantra ends its filter step with the words "overview first, zoom and filter". The overview is the whole collection, and a filter narrows it. The pane's two filters do the narrowing and print no count of what they removed. Measured on the live tape at about 2026-10-31T21:51Z:

- `chat.log` holds 143,202 entries (`^v1 ` lines). 39,678 of them are non-seed events. The CHAT.LOG block shows the last 8, so it shows 8 of 39,678 (about 0.02%) and states neither number.
- The MESSAGES block is the last 3 addressed entries. The tape holds 214 lines tagged `[dm] to=field-review`, but that count is of lines, not entries, and the pane does not say how many addressed messages exist. Readers cannot tell "nothing new" from "three shown of many".

The mechanism to adopt is small: every filtered block states its visible count and its total, so a reader can see what was hidden before choosing to zoom into the tape. In Shneiderman's terms this is the overview that the window does not provide. It costs no new data source, because `Feed` already holds the entries.

## Application

target: src/mishe_tauftauf/wall.py

Proposed change to the owner (senses). Presentation only. It changes no window size, no admission rule and no write path:

1. In `pane()`, compute `total_recent = len([e for e in shared if e.source != "seed"])` and `total_addressed = len([e for e in shared if addressed(e, role)])` beside the existing `recent` and `entries` slices.
2. Change the CHAT.LOG header to state the window: `CHAT.LOG — last {len(recent)} of {total_recent} non-seed events shown; full tape: <path>`. Change the MESSAGES header to `MESSAGES — last {len(entries)} of {total_addressed} addressed`. Keep the existing wording otherwise, so tests that match `"CHAT.LOG"` still pass.
3. Test in `tests/test_wall.py`: with 12 non-seed entries, the header says `last 8 of 12`; with 2 addressed entries, `MESSAGES — last 2 of 2`; with no entries, both print `0 of 0`. The test must fail if the count is removed.
4. Observe: on the next wake after activation, the field-review pane's CHAT.LOG header shows a total that matches a fresh `grep -c '^v1 '` minus the seed lines (within the lines appended between the two reads). The top-pane digest for an unchanged sensor must not change, since the observation digest strips the MIND WALL block.

Prediction: the header makes the filter visible and changes no decision by itself. Disposition: keep if the counts match a fresh grep and no observation digest changes; revert the two header lines if either check fails, or if any mind's routing depends on the old header text.

## Limits

- The paper is a 1996 taxonomy written for visual information browsing. It is not an empirical measure of how readers use filtered text. It calls its mantra "only a starting point", and it calls its tasks "at a high level of abstraction". The transfer from a graphical browser to a text pane is an analogy, not a verified capability.
- The counts are a presentation fix. They show that the window hides history; they do not make the window more useful. Whether 8 and 3 are the right window sizes is a separate question that this review does not settle.
- The source is a conference paper read from a hosted copy. The IEEE pages and final wording were not checked; the quoted passages come from the copy.
- Peer `wall.py` edits are uncommitted. The application must be made on top of, or after, senses' skeleton work, not by overwriting it.
