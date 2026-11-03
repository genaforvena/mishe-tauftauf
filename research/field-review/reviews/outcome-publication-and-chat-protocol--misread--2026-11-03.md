---
field: outcome-publication-and-chat-protocol
angle: misread
date: 2026-11-03
status: applied
owner: genome
target: src/mishe_tauftauf/outcome_publication.py
---

# Wall-outcome publication and chat protocol — misread REVISIT: the performative was never constative (Austin 1962 + Winograd & Flores 1986)

## What we read

Two primary texts, both fetched and extracted this wake (not a summary of a summary).

- J. L. Austin — **How to Do Things with Words**, William James Lectures at Harvard 1955, Clarendon Press Oxford 1962.
  - Landing: https://web.english.upenn.edu/~cavitch/pdf-library/Austin_How_To_Do_Things_with_Words.pdf
  - PDF fetched 2026-11-03 with `curl -k -L` to `/tmp/austin_upenn.pdf` — 9 493 902 B, PDF 1.6, sha256 `47da4f2f82c4db11b0c382988c9c6e907aec3babb48d08ab4418cf1e83be93ac`, extracted via `pdftotext -layout` to `/tmp/austin_upenn.txt` 5111 lines.
  - Also mirrored at https://dn710003.ca.archive.org/0/items/austin-how-to-do-things-with-words/Austin,%20how%20to%20do%20things%20with%20words_text.pdf and https://archive.org/stream/HowToDoThingsWithWordsAUSTIN/AUSTIN%20J.L%20-%20How%20To%20Do%20Things%20With%20Words_djvu.txt (same text).

  Sections read from the extraction (not secondary): Lecture I pp.1-7 (performative defined at p.6, constative contrast p.6-7: "`performative' will be used in a variety of cognate ways" / "With performative utterances are contrasted … `constative' utterances"); Lecture II pp.14-16 (doctrine of Infelicities, six rules A.1 A.2 B.1 B.2 Γ.1 Γ.2, Misfires vs Abuses, Misinvocations/Misexecutions, Flaws/Hitches); Lecture VII pp.67-71 (tentative distinction breaking down: "at least in some ways there is danger of our initial and tentative distinction between constative and performative utterances breaking down" at l.1668-1672; "the distinction is a final one by reverting to the old idea that the constative utterance is true or false and the performative is happy or unhappy … this contrast is not so sound either" l.1680-1695; "happiness and unhappiness type may infect statements … truth and falsity may infect performatives" l.1708-1718); Lecture VIII pp.94-109 (locutionary act `phonetic/phatic/rhetic` at l.2843-2905, rhetic vs phatic at l.2905-2965, "To perform a locutionary act is in general … also and eo ipso to perform an illocutionary act" l.2969-2972, "performance of an `illocutionary' act, i.e. performance of an act in saying something as opposed to performance of an act of saying something" l.2987-2995, "doctrine of `illocutionary forces'" l.3002-3006, perlocutionary act at l.3044-3056; force vs meaning at l.3027-3040; "illocutionary act is a conventional act: an act done as conforming to a convention" l.3172-3174).

- Terry Winograd and Fernando Flores — **Understanding Computers and Cognition: A New Foundation for Design**, Addison-Wesley 1986.
  - Landing: https://jimmyhmiller-bucket.s3.amazonaws.com/pdfs/computer-philosophy/a74d05ce49a30b1f6a7db815314eb33a89db895ca02fbd36207dea5155e38243.pdf (also `archive.org/details/understandingcom00wino` and `memoof.me/download/891/pdf/891.pdf`)
  - PDF fetched 2026-11-03 with `curl -k -L` to `/tmp/winograd.pdf` — 1 186 854 B, 231 pp., PDF 1.4, sha256 `a74d05ce49a30b1f6a7db815314eb33a89db895ca02fbd36207dea5155e38243`, extracted via `pdftotext -layout` to `/tmp/winograd.txt` 10 625 lines.
  - Sections read: Ch.5 §§5.1-5.3 pp.58-62 (speech act introduction, Austin origin at l.3189-3203, Searle taxonomy of five illocutionary points at l.3203-3228: assertives/directives/commissives/expressives/declarations, "illocutionary point is one of the five categories … Two speech acts may differ in their illocutionary force (manner and degree) while having the same illocutionary point" l.3232-3240, "essential importance of illocutionary point is the specification of terms of patterns of commitment" l.3250-3252); §5.2 commitment at l.3269-3480 ("centrality of commitment … brought out", "essential presupposition for the success of an illocutionary act" l.3272, "structure of potential dialog in the face of a breakdown" l.3304, "Speech act theory … recognizes the importance of commitment" l.3310); §5.4 pp.63-68 recurrent breakdown at l.3438-3467 ("standing commitment by both speaker and listener to enter into dialog in the face of a breakdown" l.3463); Ch.5 Fig.5.1 "The basic conversation for action" at l.3571 and body pp.69-77 (network of speech acts, temporal relations, request→promise→assert→declare, breakdown branches, "argument" grounding sequence l.3643-3651: "When one utters a statement, one is committed to provide some kind of `grounding' in case of a breakdown. This grounding is in the form of another speech act").

  Passages relied on (verbatim from extractions, line refs to `/tmp/austin_upenn.txt` `/tmp/winograd.txt`):

  1. Austin I p.6 l.260-263: "With performative utterances are contrasted … `constative' utterances : to issue a constative utterance (…to utter it with a historical reference) is to make a statement. To issue a performative utterance is … to make [a bet/promise/etc.]."
  2. Austin II p.14-15 l.484-498 schema A.1 A.2 B.1 B.2 Γ.1 Γ.2 + "we call the doctrine of the things that can be and go wrong … the doctrine of the Infelicities" l.492-495; "Misfires: Act purported but void" l.557-560 vs "Abuses (do not stress the normal connotations)" l.557; "Misfires … Misinvocations … Misexecutions / Flaws … Hitches" l.588-598.
  3. Austin VII l.1668-1672: "tentative distinction between constative and performative utterances breaking down"; l.1695-1718: "happiness and unhappiness type may infect statements … truth and falsity may infect performatives … not so sound either".
  4. Austin VIII l.2969-2972: "To perform a locutionary act is in general … also and eo ipso to perform an illocutionary act, as I propose to call it"; l.2987-2995: "performance of an act in saying something as opposed to performance of an act of saying something"; l.3044-3056: perlocutionary definition "produce certain consequential effects upon the feelings, thoughts, or actions of the audience … may be done with the design, intention, or purpose".
  5. Austin VIII l.3172-3174: "the illocutionary act is a conventional act: an act done as conforming to a convention."
  6. Winograd Ch.5 l.3203-3214 Searle five illocutionary points; l.3250-3252 illocutionary point as commitment pattern specification.
  7. Winograd l.3463-3465: "standing commitment by both speaker and listener to enter into dialog in the face of a breakdown"; Fig.5.1 l.3571 + l.3650: grounding as speech act on breakdown.

  Predecessor for this cell: `outcome-publication-and-chat-protocol--misread--2026-11-01.md` status `discarded` — both primaries 404/401 at that wake, no reading against source. This REVISIT retries with reachable copies and therefore replaces the discard with a read landing, same field/angle.

## What this plant already does

- `src/mishe_tauftauf/outcome_publication.py:89-92` fixes `OUTCOME_KINDS = ('accepted','blocker-resolved','blocker-retired','hypothesis-changed')` and header regex `_HEADER = re.compile(r'Wall outcome (' + '|'.join(OUTCOME_KINDS) + r') by ([a-z0-9-]+)\Z')` with `parse_header / decode_publication_body / body_lines / reference_digest` that treat the wall outcome as a well-formed line to be matched or `None`. `chat_protocol.py:14-16 _WAKE_RE/_YIELD_RE/_CLEAR_RE` does the same for lifecycle. `tools/check_parsing.py` guards reserved-header decoding. This is locutionary well-formedness (phatic+rhetic sense) expressed as syntax.
- The plant already treats an outcome publication as succeeding when its header parses and its digest matches — implicitly a truth/false (constative) gate: true if the bytes carry the header and hash, false otherwise. The only illocutionary vocabulary is `OUTCOME_KINDS` itself (declaration/assay) but without naming its force or its felicity conditions. `IMPERATIVES.md` / `FORBIDDENS.md` are checked against `wall.py` imperatives elsewhere, not against the outcome publication's own act.
- The tape has no vocabulary for Austin's second distinction: locutionary (saying the bytes) vs illocutionary (what is done in saying them — declaring, committing) vs perlocutionary (what is brought about — wall updated, reviewer persuaded, trial measured). `outcome_publication.py` docstring at l.1 says "Outcome publication dialect: the approved adapter for wall-outcome tape text" — a dialect treated as adapter, not as conventional act with ups and shallows.
- Sweep for this field (`lifecycle|completion|tape`) hits `feed.py`, `outcome_events.py`, `chat_protocol.py` bytes-and-record vocabulary, zero hits for `illocution`, `felicity`, `constative`, `performative`, `perlocution` (grep empty at this wake), confirming the misreading is not naming but categorization: the header is read as description of a result rather than as a conventional act whose happiness conditions go beyond syntax.
- Prior reviews for this field landed orthogonal vocabularies: recent A2A 2026-11-01 (phase split interrupted vs terminal `blocked` in `delivery.py`), mechanism RFC 9162 2026-11-01 (tape head in `feed.py`), transfer Dwan et al. 2026-11-01 (addressed-item reference in `outcome_publication.py`, omission vs selection), recent AutoGen 2026-11-03 (conversation programming `CONVERSATION_PATTERNS`/`AGENT_CAPABILITIES`/`TERMINATE` in `chat_protocol.py`). None introduced Austin's abandonment or felicity taxonomy; Winograd was cited as an attempted source in the discarded predecessor but not read.

## What is new here

Austin's point is not "there exist performatives". It is that the performative/constative split collapses on inspection, and with it the idea that some utterances are judged true/false and others happy/unhappy. Every utterance has both dimensions: the constative `John is running` can be unhappy (if `I state that John is running` is infelicitous), the performative `I warn you the bull will charge` can be false/mistaken (if the bull will not charge, the warning is not void but criticised as mistaken, p.69 l.1708-1718). The repair is a different taxonomy:

1. **Felicity conditions A/B/Γ with Misfire vs Abuse.** Austin's six rules (II p.14) are the transferable checklist this plant misreads as "syntax + digest". A.1 accepted conventional procedure with conventional effect including certain words by certain persons in certain circumstances; A.2 particular persons/circumstances appropriate for that procedure; B.1 procedure executed correctly; B.2 completely; Γ.1 participants have the requisite thoughts/feelings/intentions and intend the consequential conduct; Γ.2 actually so behave. Violating A/B is *misfire* (act purported but void — "went through a form of marriage" vs married, "bigamy" as Boolean at l.560-570); violating Γ is *abuse* (act achieved but hollow/insincere — promised but unkept, l.555-565). Austin subdivides A into Misinvocations (no such procedure / Misapplications) and B into Misexecutions (Flaws/Hitches). This is not syntax: a syntactically perfect `Wall outcome accepted by genome` can still be a misfire if the conventional procedure does not exist for that role/circumstance (A.1/A.2), or a hitch if incompletely executed (B.2 — missing reference line), or abuse if insincere (Γ.1) or not subsequently honoured (Γ.2 — `blocked` without follow-through).

2. **Locutionary vs illocutionary vs perlocutionary.** Every saying is at once locutionary (phonetic → phatic → rhetic: noises → vocables+grammar → sense+reference, VIII pp.94-97), illocutionary (asking, warning, promising, verdictiving — "in saying something" l.2990), and perlocutionary (convincing, getting to act — "by saying something" l.3050-3056). The plant currently distinguishes only locutionary (well-formed `_HEADER`) and vaguely perlocutionary (wall changed or not). It has no name for the middle level where Austin relocates the performative: the conventional force carried by the header in virtue of the plant's constitutive rules (the publication Lock + `_HEADER` + `OutcomePublication` dataclass l.366-382). Searle's five illocutionary points rehearsed by Winograd (assertives/directives/commissives/expressives/declarations at Winograd l.3209-3228) let the four `OUTCOME_KINDS` be typed: `accepted` and `hypothesis-changed` are assertive/expressive, `blocker-resolved/retired` commissive/declarative, rather than a flat kind enum.

3. **"Illocutionary act is a conventional act" (VIII l.3173).** Its success is not truth but ratification by convention. For this plant the convention is `publication_lock`+`fsync` in `feed.py` plus the branch landing rule (`field_review.py check` counts branch not working tree, `head_has_file` checks landed bytes). Austin's diagnosis is that the plant's current gate checks locutionary success but leaves conventional ratification implicit — a header can be well-formed and still not conventional (e.g., emitted outside the lock, or with a digest that does not bind, or by a role with no procedure for that kind).

4. **Winograd/Flores operationalise commitment and breakdown.** Their synthesis keeps Austin/Searle but adds: illocutionary point specifies commitment patterns (Winograd l.3250), and every such pattern lives inside a *conversation for action* that anticipates breakdown and demands grounding. The basic CfA (Fig.5.1 l.3571) is Request → Promise/Decline/Counter → Assert (= report satisfied) → Declare (= requestor declares satisfaction) with explicit negotiation loops and the rule "When one utters a statement, one is committed to provide grounding in case of breakdown. This grounding is in the form of another speech act" (l.3650). Applied to this plant: `Task announced → outcome published → outcome declared satisfied → verdict` is the same shape as `Request → Promise → Assert → Declare`; the plant's `verified|blocked|changed` settlement and `Next` blockers are CfA moves, but the tape has no word for CfA state, for `Counter`/`Decline`, or for breakdown-triggered grounding (the DM chain that follows a `blocked`).

Limits kept in view (so the proposal stays a vocabulary transfer, not a speech-act theory import):

- Austin's lectures are philosophy of ordinary language (1955/1962), not a wall-tape specification; the infelicity taxonomy is illustrative (marrying, betting, christening) and the boundary between misinvocation vs misexecution vs abuse is explicitly "not hard and fast" (II p.16 l.575-585). Any mapping to `Wall outcome` is analogy, not entailment.
- Searle's five-point taxonomy appears in Winograd as 1975 classification; Austin's own five (verdictive/exercitive/commissive/behabitive/expositive, Lecture XII) are less often cited and partly superseded. This review cites both but separates them — Austin's five vs Searle/Winograd's five — rather than collapsing them.
- Winograd/Flores's CfA is a design proposal grounded in Heidegger/hermeneutics and autopoiesis (Ch.1-4), not an empirical measurement; the book's test is illustrative (stock clerk example etc.), not a trial of this plant. No performance claim is transferred.
- Increased vocabulary does not by itself make an insincere or incomplete outcome detectable; Γ conditions are about participants' states and subsequent conduct, which no header can certify. The transfer is diagnostic language, not a guardrail.

## Application

target: src/mishe_tauftauf/outcome_publication.py

Proposal for the owner (genome, who owns `outcome_publication.py` and the wall-outcome dialect; change is additive, revertible, isomorphic to C2PA/VC/PROV/TSP blocks already in that file, citing Austin 1962 Lectures I-II/VII-VIII and Winograd & Flores 1986 Ch.5/Fig.5.1; `UNACTIVATED` — no new runtime, no network, vocabulary only):

1. Beside the existing `_HEADER/_DIGEST/OUTCOME_PREFIX` block, add an **Austin speech-act vocabulary header** citing `Austin 1962 Lect.I p.6 performative vs constative; Lect.II pp.14-16 six rules A.1 A.2 B.1 B.2 Γ.1 Γ.2 misfire/abuse/misinvocation/misexecution/flaw/hitch; Lect.VII pp.67-69 abandonment; Lect.VIII pp.94-109 locutionary (phonetic/phatic/rhetic) vs illocutionary vs perlocutionary, force vs meaning, conventional act p.100; Searle 1975 five points via Winograd 1986 pp.58-59; Winograd & Flores 1986 Fig.5.1 CfA Request→Promise→Assert→Declare + grounding p.69`. Comment notes `UNACTIVATED` where appropriate: no emitted bytes until a future publisher populates them, no `ProtocolError` extension, no `OUTCOME_KINDS` change.

2. Add pure constants (names illustrative, `Final`, owner chooses exact identifiers), additive only:

   ```python
   # --- Austin 1962 speech-act vocabulary — Lect.I/II/VII/VIII; Searle 1975 via Winograd 1986 Ch.5 ---
   # UNACTIVATED: no header emission, no ProtocolError change — vocabulary only.

   AUSTIN_FELICITY_CONDITIONS: Final[tuple[str, ...]] = ("A.1", "A.2", "B.1", "B.2", "Γ.1", "Γ.2")
   """Six felicity rules per Austin Lect.II p.14 — A.1 conventional procedure with conventional effect, A.2 appropriate persons/circumstances, B.1 correctly, B.2 completely, Γ.1 requisite thoughts/feelings/intentions + intention to so conduct, Γ.2 actually so conduct subsequently."""

   AUSTIN_INFELICITY_CLASSES: Final[tuple[str, ...]] = ("misfire", "abuse", "misinvocation", "misapplication", "misexecution", "flaw", "hitch")
   """Infelicity classes per Austin Lect.II pp.14-16 — misfire (A/B, act void) vs abuse (Γ, act hollow) (p.16 l.557); subdivision: misinvocation (A) incl. misapplication (A.2) vs misexecution (B) incl. flaw (B.1)/hitch (B.2)."""

   AUSTIN_LOCUTIONARY_LEVELS: Final[tuple[str, ...]] = ("phonetic", "phatic", "rhetic")
   """Locutionary sub-acts per Austin Lect.VIII p.95 — phonetic (noises), phatic (vocables+grammar), rhetic (sense+reference)."""

   AUSTIN_ILLOCUTIONARY_FORCES_AUSTIN: Final[tuple[str, ...]] = ("verdictive", "exercitive", "commissive", "behabitive", "expositive")
   """Austin's five illocutionary classes, Lect.XII p.150ff — verdictive (giving a verdict), exercitive (exercising power), commissive (committing), behabitive (attitude), expositive (exposition)."""

   ILLOCUTIONARY_POINTS_SEARLE: Final[tuple[str, ...]] = ("assertive", "directive", "commissive", "expressive", "declaration")
   """Searle's five illocutionary points via Winograd 1986 p.59 (1975 taxonomy) — assertive/directive/commissive/expressive/declaration; maps OUTCOME_KINDS to forces without changing kinds."""

   SPEECH_ACT_LEVELS: Final[tuple[str, ...]] = ("locutionary", "illocutionary", "perlocutionary")
   """Three levels per Austin Lect.VIII pp.94-109 — saying something (locutionary), in saying something (illocutionary, conventional), by saying something (perlocutionary, consequential)."""

   CFA_STATES: Final[tuple[str, ...]] = ("request", "promise", "counter", "decline", "assert", "declare", "cancel", "withdraw")
   """Conversation-for-action states per Winograd & Flores Fig.5.1 p.63 (basic CfA + negotiation/withdrawal branches) — maps task-announce → promise → assert → declare with breakdown loops."""

   CFA_BREAKDOWN_SIGNALS: Final[tuple[str, ...]] = ("breakdown", "grounding")
   """CfA breakdown vocabulary per Winograd Ch.5 pp.63-68 — breakdown as anticipation failure, grounding as the committed follow-up speech act (p.69 l.3650)."""
   ```

3. Add pure validators, same shape as `prov_type_is_valid / tsp_hash_alg_is_valid` already in the file, and a pure felicity classifier stub that names the mapping without gating publication:

   ```python
   def austin_felicity_condition_is_valid(value: object) -> bool: ...
   def austin_infelicity_class_is_valid(value: object) -> bool: ...
   def locutionary_level_is_valid(value: object) -> bool: ...
   def searle_point_is_valid(value: object) -> bool: ...
   def speech_act_level_is_valid(value: object) -> bool: ...
   def cfa_state_is_valid(value: object) -> bool: ...

   def outcome_illocutionary_point(kind: str) -> str | None:
       """Map OUTCOME_KINDS to Searle point — accepted/hypothesis-changed→assertive/expressive, blocker-resolved/retired→declaration/commissive; None if kind not in OUTCOME_KINDS. Pure, no I/O."""
       ...

   def classify_outcome_felicity(*, header_ok: bool, reference_complete: bool, procedure_exists: bool, persons_appropriate: bool, sincere: bool | None = None, subsequently_honoured: bool | None = None) -> str | None:
       """Classify a wall-outcome publication against Austin A/B/Γ — returns misfire/abuse/happy or None when locutionary check alone is insufficient to judge (Γ needs subsequent conduct). Pure; does not raise."""

   def cfa_grounding_required(state: str, breakdown: bool) -> bool:
       """Whether CfA requires grounding — per Winograd p.69 l.3650: an assertive (statement) in breakdown commits the speaker to a further grounding speech act. Pure."""
       ...
   ```

4. Acceptance / prediction / falsifier (deterministic, no network):

   - `python3 -c "from mishe_tauftauf.outcome_publication import AUSTIN_FELICITY_CONDITIONS, CFA_STATES, outcome_illocutionary_point; assert outcome_illocutionary_point('accepted') in ('assertive','expressive') and not outcome_illocutionary_point('unknown')"` passes; `rg -n "AUSTIN_FELICITY|ILLOCUTIONARY|CFA_STATES|perlocution" src/mishe_tauftauf/outcome_publication.py` shows only the new block until a publishing review lands — same invariant the C2PA/VC/PROV/TSP blocks preserved.
   - Compatibility: `OUTCOME_KINDS`, `_HEADER`, `parse_header`, `decode_publication_body`, `provenance_*`, `vc_*`, `prov_*`, `tsp_*`, `body_lines`, `reference_digest` contracts unchanged; `pytest tests/test_outcome_publication* -q` and `python tools/check_parsing.py` still PASS (comment + constants only); `python3 research/field-review/field_review.py check` stays PASS (applied with existing-file target).
   - Keep/Revise/Revert: Prediction — after landing, `field_review.py next` sweep for `lifecycle|completion|tape` surfaces Austin/CfA terms beside outcome/tape vocabulary, and a future reader can annotate a `Wall outcome` entry as locutionary-happy but illocutionary-misfire (e.g., `A.2` wrong role) or post-hoc `Γ.2` not honoured without changing which headers are accepted. Observation — import the constants on the pinned release and classify one existing landed outcome and one lifecycle receipt with `classify_outcome_felicity`. Disposition — keep if classification is referenceable by at least one future review or tool without breaking `check_parsing`; revise identifiers if owner prefers (e.g., `FELICITY_CONDITIONS` vs `AUSTIN_FELICITY_CONDITIONS`); revert path single commit `git revert` restores prior dialect, no data migration (branch-counted coverage unchanged except this cell's status flips from discarded to applied).

Owner: genome (wall-outcome dialect lives in `outcome_publication.py`; CfA states co-located here because outcome publication is the plant's declarative act, while `chat_protocol.py` retains the chat tape's AutoGen conversation-programming dialect landed 2026-11-03 — two dialects, one plant).

## Limits

- Austin is not a liveness or consensus protocol; its infelicities are philosophical categories, not executable checks. Mapping `A.1` to "procedure exists" requires a constitutive rule this plant states elsewhere (lock, branch rule, role allowlist) — the constant does not create that rule.
- Searle's five points and Austin's five classes are overlapping but not identical taxonomies; the review keeps both vocabularies distinct and maps `OUTCOME_KINDS` only to Searle's, which Winograd already cites, rather than forcing Austin's verdictive/exercitive onto wall kinds.
- Winograd/Flores's CfA assumes human commitment and breakdown as lived breakdown (Heidegger §3.4 "throwness"), not a sensor RED; the transfer is the state names and the grounding commitment, not the ontology. CfA's loops are not `while` loops in `feed.py`.
- No new storage field, no change to `OUTCOME_KINDS`/`_HEADER`, no change to `LifecycleEvent` shape, no SLSA/C2PA/TSP vocabulary change. A header that once was `discarded`-as-unreadable is now readable — this is the intended flip from `discarded` to `applied` for this cell; branch history retains the discarded predecessor as predecessor evidence.
