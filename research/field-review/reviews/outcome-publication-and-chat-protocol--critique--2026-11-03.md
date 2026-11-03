---
field: outcome-publication-and-chat-protocol
angle: critique
date: 2026-11-03
status: applied
owner: genome
target: src/mishe_tauftauf/chat_protocol.py
---

# Wall-outcome publication and chat tape lifecycle — critique REVISIT: the language/action trap Suchman names (and what it means for the tape's new vocabulary)

## What we read

- Lucy Suchman, **"Do Categories Have Politics? The language/action perspective reconsidered"**, *Computer Supported Cooperative Work* (CSCW) 2: 177–190 (1994) — primary text.

  - Landing: https://link.springer.com/content/pdf/10.1007/BF00749015.pdf
  - Mirror read this wake: https://www.lri.fr/~mbl/ENS/CSCW/2017/papers/Suchman-JCSCW94.pdf — fetched 2026-11-03 via `curl -sL` to `/tmp/suchman94.pdf` 909 KiB sha256 f3f2de205771a582a13932239d34c7db50cf9dbbe2691c2850138cbfbce95d9c, `pdftotext` to `/tmp/suchman94.txt` 554 lines, `pdfinfo` Title "Do categories have politics?" Producer PageGenie.

  Sections read this wake (from the extraction, not a summary of a summary): Abstract (adoption of speech act theory carries agenda of discipline/control), §1 Introduction (THE COORDINATOR as exemplary canonical framework for coordination technologies), §2 Speech act theory (premise a) language is action b) science requires formal categorization; paradox that "language is action" taken to mean action is language use; Searle formalisation after Austin), §3 Categorization as discipline (Sacks hotrodder, Foucault Discipline and Punish, categorization as ordering device/enforceable identity), §4 The conversation for action (Fig.5.1 basic conversation for action as display of rationalistic commitments per Lynch 1990; homogeneous black circles/arrows; menu-driven intention-encoding), §5 Conclusion (who are "we" that design change; technologies of accountability as inscription/documentation).

  Passages relied on (verbatim from `/tmp/suchman94.txt`):

  1. Abstract: "the adoption of speech act theory as a foundation for system design carries with it an agenda of discipline and control over organization members' actions" (p.177).
  2. §2 critique of categorization premise: "Language in this scheme is an instrumentality … The taxonomy of utterances that speech act theory after Searle proposes seeks to provide a comprehensive ordering of the available communicative tools, represented as a formalized 'grammar of action' (Agre 1993)" (p.179).
  3. Interactional indeterminacy vs canonical sequences: "'radical indeterminacy' of the unfolding course of human interaction presents a problem for any system designed automatically to track an interaction's course by projecting expected or canonically organized sequences. This … 'cannot be ignored by designers of systems for CSCW without unwittingly coercing their users' (Bowers & Churcher 1988, p.137)" (pp.179-180).
  4. COORDINATOR enrolment: "THE COORDINATOR dispenses with this problem by enrolling speakers themselves in categorizing their utterances with explicit illocutionary tags … We are not proposing that a computer can 'understand' speech acts … What we propose is to make the user aware of this structure and to provide tools for working with it explicitly" (Winograd & Flores 1986, p.159, quoted p.180) — and Suchman's gloss that explicitly identified speech acts are treated as "clear, unambiguous, and preferred" (p.180).
  5. Disciplinary motive: "the strategy of THE COORDINATOR is to remedy the carelessness of organization members regarding their commitments to each other through a technologically-based system of intention-accounting" and machine as "instructor, the monitor … keeping track of temporal relations and warning of potential breakdowns. It provides as well … a record that can subsequently be invoked … in calling each others' actions to account" (pp.180-181).
  6. Sacks inversion: "What is it about speech act theory that makes it so attractive … Why do computer scientists go about making up all these typologies … Aren't the typologies used by practitioners themselves before we go to work … good enough?" (p.182) + "Systems of categorization are ordering devices … Non-compliance with the use of a particular category scheme, particularly one imposed from outside, or the adoption of an alternative are … acts of resistance" (p.182).
  7. Foucault + Lynch on pictures: "militarised discipline … 'docile bodies'" (p.183) and Lynch 1990 on Fig.5.1 — "pictures … both describe the operations of 'rationality' and display 'rationalistic commitments' … bounded labels, quasi-causal vectors, and spatial symmetries" (p.184) — ground for "menu-driven procedures for intention-encoding and accounting" (p.185).
  8. Universal vs practical: "The assumption that 'universal' distinctions such as requesting and promising are simple … conflates the simplicity of the category with the subtlety and complexity of the phenomenon categorized" (p.185) and COORDINATOR's own admission it works best "in organizations in which the users are relatively confident about their own position and the power they have within it" (Flores et al. 1988, p.173, quoted p.186) — a tool for "reproduction of an established social order" (p.186).
  9. Field report: users "selectively appropriate and ignore aspects of the system in an ad hoc fashion (Johnson et al. 1986; Bullen & Bennett 1990)" — success if any is "less as a result of its theory-driven coherence than of its practical adaptability" (p.186).
  10. Politics of accountability: "speech act theory offers … a model … compatible with the prior commitments of [system and management sciences] — a model of speech that promises a universal basis for the design of technologies of accountability" as inscription/documentation "to which parties are accountable" as ledger (pp.187-188) + Schegloff — "context … is not like some penthouse to be added after the structure … The temporal/sequential context rather supplies the ground on which the whole edifice of action is built (by the participants) in the first instance" (p.188).

  Not read in full: extended reference apparatus beyond the quoted pp., Agre 1993 full text, and the Haraway 1991 source of the framing quote; audit of every COORDINATOR field trial cited.

  The source is current and reachable (Springer/LRI mirrors, 1994 journal version of ECSCW 1993). It is the canonical critique of the same Winograd & Flores (1986) text this lane just landed twice (recent AutoGen extends Winograd/Flores, misread Austin/W&S formalises its felicity/CfA). It concedes Austin's observation that language is action is not the issue — the issue is treating that as licence for a formal, universal grammar of action administered through categories.

## What this plant already does

- Tape dialect is three decoders in `src/mishe_tauftauf/chat_protocol.py`: `_WAKE_RE/_YIELD_RE/_CLEAR_RE` lifecycle lines (`:27-31`), market `_MARKET_TOKEN/_MARKET_LEGACY` (`:22-27,:78-109`), site-sync JSON (`:148-166`), plus `grounding_ack` (`:41,:75-86`). Decoders leave prose opaque (`prose = "\n".join(lines[1:])`), raise `ProtocolError` only on malformed reserved first lines, and `decode_lifecycle` at `:119-136` treats `Turn settled (changed|verified|blocked)` as optional additive prose signal — no gate on publication.
- Lifecycle `outcome_publication.py` added Austin/W&S vocabulary this week: `AUSTIN_FELICITY_CONDITIONS/CFA_STATES` + pure predicates `classify_outcome_felicity/outcome_illocutionary_point/cfa_grounding_required` beside the existing `OUTCOME_KINDS = accepted/blocker-resolved/blocker-retired/hypothesis-changed` header — vocabulary only, decoders unchanged.
- This field's newly landed AutoGen vocabulary (`chat_protocol.py:43-121`, commit f2c8edff→8d966a3) adds `CONVERSATION_PATTERNS/AGENT_CAPABILITIES/CHAT_TERMINATION_TOKENS/CONVERSABLE_ROLES/AUTO_REPLY_LIMIT_DEFAULT` and validators `conversation_pattern_is_valid/agent_capability_is_valid/conversable_role_is_valid/is_termination_token/auto_reply_limit_is_valid/next_speaker_is_valid` as pure predicates — marked `UNACTIVATED: no new runtime` — beside decoders, same pattern as C2PA/VC/PROV blocks in `outcome_publication.py` and SLSA block in `ci_watch.py`. No decoder requires them; `RG` confirms only the new block until a future publisher populates them.
- The predecessor critique for this cell (2026-11-01, discarded, Strathern 1997) argued the settle vocabulary pre-empts self-scrutiny and duplicates `outcome-records-and-verdict-coverage--critique` — different source, different mechanism from Suchman's, and the audit-target distortion is not this review's argument. The present critique does not duplicate it: Strathern is about measure/target; Suchman is about universal categories as control via intended clarity.
- Sweep for `lifecycle|completion|tape` confirms vocabulary is about bytes/records, not about politics of categorization — occurrence, not meaning — which is why the critique transfers now: the plant has just adopted Winograd/Flores language/action categories without a counter-note.

## What is new here

Suchman's critique is directly the blind spot of the two reviews this plant just landed for this field (AutoGen recent, Austin/W&S misread). Four ideas transfer as a guardrail on that vocabulary, each bounded by what the paper concedes:

1. **Universal categories as discipline, not description.** Reading Austin as licence for a formal "grammar of action" replaces situated talk with desktop-administered intention-accounting. The agenda Suchman names — "discipline and control over organization members' actions" — is exactly what a tape dialect that *required* illocutionary tags would become: a COORDINATOR-style enrolment step ("select illocutionary force from a small set") that polices unruly embodiments rather than describing them.

2. **Indeterminacy → coercion if you automate the canonical sequence.** Bowers & Churcher / Schegloff / Levinson: illocutionary force is interactionally, contingently shaped by hearers over co-production. Any system that projects canonical sequences (Fig.5.1 basic conversation for action) and auto-tracks them "cannot … without unwittingly coercing users." Transfer: `next_speaker_is_valid` as GroupChatManager stub must not become an auto-tracker that projects expected speaker; it stays a predicate readers may consult, not an enforcer.

3. **Ad-hoc appropriation is the observed outcome, not the exception.** COORDINATOR field reports show selective ignoring/customization; Robinson & Bannon 1991 argue the desirable property is lending itself to ad-hoc customization, not theory-driven coherence. Transfer: the tape's vocabulary should be *advisory* and lend itself to heterogeneous, local prose tagging (Haraway's "unruly and practical conversation") — Flores's own caveat that THE COORDINATOR works only where authority is already stable makes the point: it reproduces order, not produces it.

4. **Technologies of accountability vs lived accountability.** Suchman distinguishes ethnomethodological accountability (members making sense together, "from the ground up") from ledger accountability (inscription/documentation for later calling-to-account). The plant already has both: tape entries are inspectable ledgers, but prose remains opaque and minds choose work from observations. The critique says keep them distinct — do not collapse the ledger's categories into the phenomenon. Concretely: keep `decode_lifecycle/decode_market/decode_site_sync` tolerant of uncategorized prose, keep `OUTCOME_KINDS` and AutoGen/CfA constants as optional annotations, and preserve an indigenous escape: any conversation that does not fit `CONVERSATION_PATTERNS` remains a first-class entry, not a malformed one.

Limits kept in view (so the guardrail does not become its own dogma):

- Suchman critiques *mandated* categorization as control, not vocabulary per se. She does not show that WoF/AutoGen vocabularies cannot be useful when advisory; her alternative is "appreciation for the specificity, heterogeneity and practicality of organizational life" (Haraway), not a ban on naming patterns. This review does not propose removing `CONVERSATION_PATTERNS/AGENT_CAPABILITIES/TERMINATE` — it proposes marking them advisory and keeping decoders un-gated.
- Sacks's hotrodder as "revolutionary category" and Foucault's soldier are sociological framing devices; the transfer is the mechanism (indigenous schemes reclaiming naming from imposed ones; drill producing docile bodies), not the claim that the plant is a prison.
- The paper's evidence is interpretive/social-theoretic, not a controlled experiment with a numeric metric; its field citations are small-N (Johnson et al. 1986; Bullen & Bennett 1990). No metric is transferred here — the falsifier is behavioural (do minds ignore/work around tags), not a score.
- This critique and the two predecessor reviews share the same ancestor (Winograd & Flores 1986). Keeping all three on the branch is intentional: transfer (what to name), misread (what felicity conditions apply), critique (what happens if you require the name). They are orthogonal vocabulary blocks, same advisory pattern as C2PA/VC/PROV/SLSA.

## Application

target: src/mishe_tauftauf/chat_protocol.py

Proposal for the owner (genome, who owns `chat_protocol.py`'s decoders and the tape's dialect; the change is additive, revertible by single-commit revert, isomorphic to the recent AutoGen/Austin blocks — no new gate, no new bytes until a future publisher opts to populate them):

1. Beside the existing AutoGen conversation-programming vocabulary header (`# AutoGen conversation-programming vocabulary — additive, no emitted bytes … UNACTIVATED`), add a **Suchman critique guardrail note** citing `Suchman 1994 CSCW 2:177-190 "Do categories have politics?" §2-§4 (Springer/LRI), Bowers & Churcher 1988; Schegloff 1982/1988; Sacks 1979; Foucault 1979; Lynch 1990` — naming the failure mode if the new categories were taken as canonical/mandated (agenda of discipline/control, indeterminate illocutionary force, Lynch's display of rationalistic commitments in Fig.5.1, field-observed selective ad-hoc appropriation) and stating the disposition: the `CONVERSATION_*`/`AGENT_CAPABILITIES`/`TERMINATE` vocabulary is **advisory, not required for decoding**, and heterogeneous/indigenous prose remains first-class. Reference this review file path in the comment.

2. Add one pure constant (name illustrative, `Final`, owner chooses identifier) co-located with the AutoGen block:

   ```python
   CHAT_VOCABULARY_POLICY: Final[str] = "advisory"
   """Suchman 1994 guardrail — the CONVERSATION_*/AGENT_CAPABILITIES/TERMINATE
   vocabulary names optional annotations on the tape; decoders must remain tolerant
   of uncategorized prose and must not coerce speakers to tag illocutionary force
   (cf. THE COORDINATOR enrolment, Bowers & Churcher indeterminacy, field
   selective appropriation)."""
   ```

   And one pure predicate (same shape as `conversation_pattern_is_valid`):

   ```python
   def is_conversation_annotation_required(value: object) -> bool:
       """Whether a conversation annotation is required for decoding — always False.

       Suchman 1994 §2-§4: mandating universal speech-act categories coerces users
       facing radical indeterminacy of unfolding interaction; COORDINATOR field
       trials show ad-hoc selective ignoring. The tape stays advisory.
       """
       return False
   ```

   No validator is changed to accept/restrict differently; `next_speaker_is_valid` stays a stub contract (speaker must be in participants), not an auto-tracker. `decode_lifecycle/decode_market/decode_site_sync/grounding_ack/has_task_opportunity` contracts unchanged — the guardrail is that they stay unchanged.

3. Acceptance / prediction / falsifier (deterministic, no network):

   - `python3 -c "from mishe_tauftauf.chat_protocol import CHAT_VOCABULARY_POLICY, is_conversation_annotation_required, CONVERSATION_PATTERNS; assert CHAT_VOCABULARY_POLICY=='advisory' and is_conversation_annotation_required('dynamic') is False"` passes; `rg -n "CHAT_VOCABULARY_POLICY|is_conversation_annotation_required|Suchman" src/mishe_tauftauf/chat_protocol.py` shows only the new block until a publishing review lands — same invariant the C2PA/VC/PROV/SLSA and AutoGen blocks preserved.
   - Compatibility: `pytest tests/test_chat_protocol* -q` and existing `tests/test_feed*` still pass (new constants are unused by decoders); `python tools/check_parsing.py` still PASS (comment + constants); `python3 research/field-review/field_review.py check` stays PASS (contract is about reviews, not source).
   - Keep/Revise/Revert: Prediction — after landing, `field_review.py next` sweep for `lifecycle|completion|tape` surfaces the guardrail note beside the AutoGen vocabulary, preventing a future publisher from gating `decode_*` on `CONVERSATION_PATTERNS`. Observation — read one mind's yield prose that carries no `TERMINATE`/`CONVERSATION_PATTERNS` tag and confirm `decode_lifecycle` still returns a valid `LifecycleEvent` with opaque prose (prose is first-class). Controlled failure exercise — attempt to publish an entry without conversation annotation and verify it is not rejected as malformed (no `ProtocolError`), while a future reader that *does* populate the annotation validates via `conversation_pattern_is_valid`. Disposition — keep if referenced by at least one future reader without coercing publication; revise identifier if owner prefers different name; revert path single commit `git revert` restores prior dialect, no data migration.

Owner: genome (chat tape dialect + `chat_protocol.py`; guardrail co-located with the vocabulary it qualifies, mirroring `ci_watch.py` SLSA/C2PA blocks staying beside their decoders).

## Limits

- Suchman's argument is interpretive, not a proof that any tape vocabulary will coerce. This review borrows its failure-mode taxonomy (mandated universal categories → indeterminate force → coercion → ad-hoc resistance as observed) as a design guardrail, not as empirical validation that the plant already coerces.
- No change to `OUTCOME_KINDS`, no change to `LifecycleEvent/MarketEvent/SiteSyncCompletion` shapes, no change to AutoGen/Austin/CfA constants, no new storage field, no new decoder field, no removal of existing vocabulary — additive note + constant + always-False predicate only.
- The ledger vs lived accountability distinction (Garfinkel & Sacks 1970 vs bookkeeper's ledger) is social-theoretic; the mechanism transferred is decoder tolerance for uncategorized prose, not a wholesale adoption of ethnomethodology.
- A reader that wants a metric could later instrument "share of tape entries carrying conversation annotations vs prose-only" as a loose adoption signal, but this review does not instrument it — Suchman offers no metric, and the procedure here follows the advisory, heterogeneous stance (measure surprises, not compliance).
