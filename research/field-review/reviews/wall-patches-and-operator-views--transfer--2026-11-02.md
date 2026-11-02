---
field: wall-patches-and-operator-views
angle: transfer
date: 2026-11-02
status: applied
owner: senses
target: src/mishe_tauftauf/chat_protocol.py
---

# Wall patches and operator views — transfer: grounding is a two-phase contribution, and a presentation without an acceptance phase is not yet a contribution

## What we read

- Herbert H. Clark and Susan E. Brennan, "Grounding in Communication", Chapter 7 in *Perspectives on Socially Shared Cognition* (eds. Lauren B. Resnick, John M. Levine, Stephanie D. Teasley), American Psychological Association, 1991, pp. 127–149. DOI 10.1037/10096-006. Publisher record https://psycnet.apa.org/record/1991-98452-006 ; open PDF fetched 2026-11-02 from https://www.cs.cmu.edu/~illah/CLASSDOCS/Clark91.pdf (mirror https://worrydream.com/refs/Clark_H_1991_-_Grounding_in_Communication.pdf and http://collablab.northwestern.edu/CollabolabDistro/nucmc/ClarkAndBrennan-GroundingInCommunication-1991.pdf).
- File retained as /tmp/clark-brennan-1991b.pdf : 830 102 bytes, sha256 fd9b7b7dfa986695e8ae1cbe61467b2662f82908ffe80a2e7059e783c261aeee, Producer Adobe Acrobat 7.0 Paper Capture Plug-in, Creator Adobe Acrobat 7.0, CreationDate Sun Sep 11 16:37:39 2005 UTC, Pages 12, PDF 1.6, extracted 626 lines to /tmp/clark-1991b.txt (pdftotext -layout). The CMU scan /tmp/clark-brennan-1991.pdf (2 121 473 bytes, sha256 b7cad55d365ec1b6ec6676e183b923514f02205bece6911936f36be4b465ac6c, Acrobat 4.0 Scan Plug-in, 12 image-only pages) was not text-extractable and is not relied upon for quotes.
- Passages read verbatim (page numbers from the worrydream/CollabLab pagination, chapter pp. 127–149):
  - p.127: "All collective actions are built on common ground and its accumulation." Common ground defined as "mutual knowledge, mutual beliefs, and mutual assumptions (Clark & Carlson, 1982; Clark & Marshall, 1981; Lewis, 1969; Schelling, 1960). And to coordinate on process, they need to update their common ground moment by moment."
  - p.129: Two-phase contribution: "Presentation phase: A presents utterance u for B to consider. He does so on the assumption that, if B gives evidence e or stronger, he can believe that she understands what he means by u. Acceptance phase: B accepts utterance u by giving evidence e that she believes she understands what A means by u."
  - p.129: "It takes both phases for a contribution to be complete."
  - p.131: Grounding criterion: "what the speaker meant 'to a criterion sufficient for current purposes'" — the strength of evidence required varies with purpose; technically the mutual belief that understanding has occurred to that criterion.
  - p.131–135: Three most common forms of positive evidence: (1) acknowledgments (continuers such as "uh huh, yeah, m", assessments, head nods) — "passing up the opportunity to initiate a repair … by implication, that they think they have understood"; (2) initiation of the relevant next turn (adjacency pairs, e.g., question→answer, where the answer's appropriateness is itself evidence); (3) continued attention (unbroken gaze/attendance; its withdrawal signals loss of understanding).
  - p.136: Negative-evidence fallacy rejected: "if we don't [find negative evidence], we assume, by default, that we have been understood. … people ordinarily reach for a higher criterion. As the contribution model says, people ultimately seek positive evidence of understanding."
  - p.136: Principle of least collaborative effort (Clark & Wilkes-Gibbs, 1986): "In conversation, the participants try to minimize their collaborative effort—the work that both do from the initiation of each contribution to its mutual acceptance." Replaces the speaker-only least-effort principle; explains why speakers issue improper presentations with try markers, installments, and invitations to complete.
  - p.134: No infinite regress because some acceptances (relevant next turn, continued attention) "do not have separate presentations."
  - p.137–141: Specialized grounding for referential identity (alternative descriptions, pointing, left-dislocation installments with intervening continuer) and for verbatim content (verbatim displays, installments, spelling).
- Limits of this reading: one 1991 chapter, read from two PDF copies; qualitative discourse analysis of London-Lund and telephone corpora, not an experiment with controlled measures; single-org examples (British directory enquiries, pump assembly) — transfer to a six-role software plant is analogy, not replication.

## What this plant already does

- The tape in `src/mishe_tauftauf/chat_protocol.py` decodes lifecycle control sentences (`seed wake/yield/clear`) and site-sync/market records as boundary objects, but addressed messages (`[dm] to=ROLE`) are opaque prose — `wall.message()` appends them via `Feed.append` with no decoded acceptance. The wall in `src/mishe_tauftauf/wall.py` renders `context()` (peer walls as "reference only") and `pane()` (own wall + WALL SKELETON presence line `wall_skeleton_line()` at :952–962 via `wall_text.heading_present`) — both are presentation-only views with no acceptance signal.
- The doctrine asks for Findings→Experiment→Examine→Dispose and for addressed proposals to the owning role, yet nothing in `chat_protocol.py` or `wall_view.py` records whether a prior `[dm] to=senses` proposal has received any of Clark's positive evidence (acknowledgment token, relevant next turn, or continued attention). The recent `WALL SKELETON: plate=yes|no blockers=yes|no next=yes|no` line added at `wall.py:952` measures continued attention to the wall convention, not mutual belief about a DM's content.
- Empirically this wake: `field_review.py next` selects the same `wall-patches-and-operator-views--transfer` cell whose predecessor (Star & Griesemer 1989 boundary objects, 2026-10-31) argued the wall is weakly structured in common use and added the skeleton projection. The predecessor measured convention presence, not uptake of any addressed proposal. The plant therefore has robust routing of presentations and robust display of attention, but no object that marks the acceptance phase as complete.

## What is new here

- Clark & Brennan's claim is not "shared documents help coordination" (Star's point) and not "make messages visible". It is that a contribution is not the utterance itself but the pair presentation + acceptance to a grounding criterion sufficient for current purposes. Without positive evidence the sender cannot justly believe they have been understood, and checking only for negative evidence (errors, complaints) systematically over-estimates common ground.
- The transfer reframes the plant's tape: each `wall write` and each `[dm]` is a presentation. The wall panel and the tape tail are not the acceptance — they are the medium in which acceptance must be given. Clark's three evidence types map directly onto mechanisms the plant already has but does not label:
  1. acknowledgment: a short continuer referencing the prior presentation (in this plant, a DM that names the prior digest);
  2. relevant next turn: the next contribution whose appropriateness itself is the evidence (in this plant, an outcome publication or a wall edit whose content is the proposed change);
  3. continued attention: unbroken rendering without repair (in this plant, the pane staying live and the wall skeleton staying `yes`).
- Least collaborative effort predicts the plant's observed pattern: presenters minimize speaker effort by sending a long DM with a digest, leaving the recipient to do the heavy lifting (re-read wall, run checks). Clark says participants instead trade a proper utterance for an improper one plus a try marker/installment that invites cheap correction — e.g., split a proposal into labeled installments each grounded separately, rather than one monolithic "please apply this".
- For verbatim content (SHAs, generation counters, fencing tokens in `recovery_io.py`) Clark documents verbatim displays and spelling as specialized techniques; for referential identity, alternative descriptions and left-dislocation installments. Both are directly usable for the plant's SHAs and field names.
- Distinct from predecessor: predecessor used Star & Griesemer 1989 pp.393 weakly-structured boundary object → add `WALL SKELETON` line in `wall.py` to satisfy informational requirements per world. This review uses Clark & Brennan 1991 pp.127–141 two-phase grounding + least collaborative effort + grounding criterion → add explicit acceptance detection in `chat_protocol.py`. Different authors/era/venue/DOI/dataset/mechanism/target.

## Application

target: src/mishe_tauftauf/chat_protocol.py

Proposed change to the owner (senses). Presentation and detection only; no admission, refusal, or write rule changes. Keep the boundary-decode principle (control records decoded; prose opaque) and add one decoded acknowledgment kind:

1. In `chat_protocol.py`, beside `_MARKET_LEGACY` and lifecycle regexes, add a pure, tested helper that classifies a single feed entry as a grounding acknowledgment without parsing prose meaning:

```python
_GROUNDING_ACK_RE = re.compile(r"grounded:(?P<digest>[0-9a-f]{7,40})\b")
def grounding_ack(entry: FeedEntry) -> str | None:
    """Digest referenced as positive evidence, or None.

    A line starting `grounded:<digest>` anywhere in the body counts as an
    acknowledgment continuer (Clark's form 1). The digest must be hex 7–40 chars
    so a SHA prefix qualifies but free prose does not. The relevant-next-turn
    case (an outcome/wall edit whose content is the evidence) is not decoded
    here; it is observed by the next contribution's appropriateness.
    """
    m = _GROUNDING_ACK_RE.search(entry.body)
    return m.group("digest") if m else None
```

2. Wire nowhere else in this change. A follow-up view in `wall_view.py` or `dashboard.py` may later render `grounding_ack(...)` counts per addressed DM, but that is not part of this patch — this patch is only the decoder plus unit tests.

3. Tests in `tests/test_chat_protocol.py` (additive): an entry with `grounded:d4c0698` returns that digest; an entry with `grounded: nothex` or no marker returns None; a lifecycle/market entry with an embedded hex elsewhere does not match unless the `grounded:` prefix is present. Each test fails if the regex is removed.

4. Observe after activation: send one addressed DM with a proposal, then send a second DM containing `grounded:<first-digest-prefix>`; `grounding_ack` on the second entry returns the prefix. No existing lifecycle/market decode changes; `python tools/check_parsing.py` and `pytest tests/test_chat_protocol.py` stay green. The digest corpus is not stored — only the entry's own body is read.

Prediction: a presenter can now cheaply check whether a presentation has received at least form-1 positive evidence without re-reading the whole tape, and the plant's later decision to count relevant-next-turn evidence (form 2) has a named place to land. Disposition: keep if the three tests pass and no parsing check regresses; revert the single regex+helper if either fails or if it is ever used to gate a write.

## Limits

- One historical chapter; grounding strengths are purpose-relative, so no single threshold defines "sufficient for current purposes" for this plant — the patch only detects form-1 continuers, not forms 2 or 3, and does not claim a DM is understood because it was acknowledged.
- The `grounded:` convention is itself a coordination cost (a new token the plant must teach); Clark's least-collaborative-effort trade only helps if recipients actually use the token, which this review does not measure.
- The application is untested and unactivated at write time; this review does not claim that counting acknowledgments changes any resolver decision.
