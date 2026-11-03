"""Read the linked Android Body consumer's retained Redmi10 observations.

Privacy lens: Nissenbaum, "Privacy as Contextual Integrity," Wash. L. Rev. 79:119
(2004) §§III–III.B (via Walzer, Spheres of Justice) and Barth et al. 2006 §2 —
appropriateness + flow/distribution (context, subject/sender/recipient,
information type, transmission principle); either norm violated is a breach.
Any redistribution of sense.body.redmi10 beyond its originating context's
transmission principle is a privacy-boundary change reviewed under
docs/protocol-boundaries.md and the 2026-10-15 personal-multibody mandate.
Device evidence (battery/light/acceleration incl. gravity) is not a human
movement/presence claim; carrying context remains contextual, not sensor-certified.
Hardening: Diamantis, "The Danger of Contextual Integrity," BEQ 2025
§§3.2–3.6 — value indeterminacy (pp.9–11), context indeterminacy (pp.11–13),
complexity favouring producer (pp.14–15), heads-wins/tails-lose (p.15),
frog-in-hot-water and conservatism (pp.16–17). Tables below are closed-world:
any (context, info_type) or (context, principle, recipient) not listed is
denied; general-value balancing ("democracy, freedom, wealth, efficiency,
other social values" BEQ p.9) is excluded; the mandate's "including new
not-yet-developed ones" does not auto-extend the vocabularies.
Consent receipts: Kaye et al., "Dynamic consent: a patient interface for
twenty-first century research networks," EJHG 23, 141-146 (2015) Boxes 1-3 —
sticky policies that travel with samples/information (p.143), per-type
revisability in real time without replacing broad consent, one record of all
transactions. Each BODY_INFORMATION_TYPES member carries a dated, per-type
receipt (origin, principle, recipients, scope) — preferences travel with the
reading; a new type needs a receipt. Redistribution reviewed under
docs/protocol-boundaries.md and the per-type receipt store.
Disclosure metric: Samarati & Sweeney, "Protecting Privacy when Disclosing
Information: k-Anonymity and Its Enforcement through Generalization and
Suppression," SRI-CSL 1998 / IEEE S&P 1998 — quasi-identifier QI (Def. 2.1
p.3), k-anonymity Def. 2.3 p.4, Assumption 2.1 QI correctness p.4 fn.1,
distance vector Def. 3.2 p.7, minimal generalisation Def. 3.3 p.8-9, MaxSup
Defs. 4.1-4.3 pp.10-12, preference costs §5 p.12-13, precision/completeness
§7 pp.15-17 Fig. 11, multiple-release open problem §8 p.18. This module stays
exact — the k symbols below are the typed metric so that any future
generalisation/suppression proposal is reviewable; citing QI per Def. 2.1 is
required for a release, not inferred.
Local longitudinal lens: Erlingsson et al., "RAPPOR: Randomized Aggregatable
Privacy-Preserving Ordinal Response," ACM CCS '14 (DOI 10.1145/2660267.2660348,
Figure 1 p.5, Theorem 1 p.8, Lemma 1 p.9, Theorem 2 p.9, §2–4) — client-local
randomized response before the wall bus (no trusted third party, p.2, vs
Dwork & Roth curator model stats_boundary.py §2.1). Bloom-encoded signal
(k=256, h=4, §2, but collision alone is not DP p.9), permanent epsilon
ε∞ (§2 step 2, Theorem 1), instantaneous epsilon ε1 (§2 step 3, Lemma 1 q*/p*
and Theorem 2), memoization of B' reused for each stable value v ("Subsequently,
this B' is memoized and reused …" p.5) to avoid averaging to truth (p.6),
cohorts m=64 sharding h hash families (§4 p.11) and LASSO decoding over many
clients, Chrome deployment existence proof (p.3, §5.4). Correlated drift when v
changes leaks despite memoization (p.3–4); ε∞/ε1 are worst-case over Bloom
pre-images (p.9). This module stays exact — RAPPOR symbols below are typed
calibration (f, p, q, k, h, m, ε∞, ε1, q*, p*) so a future local noisy body
release can be reviewed with visible longitudinal vs one-shot budgets.
Diversity lens: Machanavajjhala et al., "l-Diversity: Privacy Beyond k-Anonymity," ICDE 2006 / TKDD 2007 (DOI 10.1145/1217299.1217302, Figures 1-2 p1, homogeneity/background attacks §1.1 p2, Theorem 3.1 Eq1 p5, Definitions 3.1-3.2 p4, Principle 2 p7, Definition 4.1 entropy p7, Definition 4.2 recursive p8, Def 4.5 multi-attribute p8) — sensitive vs QI partition, per-class sensitive histogram, entropy ≥ log ℓ or recursive r1 < c·tail, syntactic not semantic, single-attribute check, multi-attribute needs Q∪{other Si} expansion. This module stays exact — l-diversity helpers below are typed measurement at DV=[0,…,0]; k-True does not imply diverse; ℓ/c are policy choices not inferred, no f/α/β estimation, no DGH/VGH generalisation.
Human-subjects lens: National Commission, The Belmont Report, FR 44:23192
(1979-04-18) via ORI/HHS — Part A practice vs research (practice: interventions
solely for individual well-being; research: hypothesis + generalizable knowledge
+ formal protocol; any element of research in an activity means the activity
should undergo review); Part B principles respect-for-persons (autonomy +
protection, comprehension/voluntariness, periodically reevaluated), beneficence
(do-not-harm + maximize benefit/minimize harm with systematic probability/
magnitude), justice (fairness in distribution + selection anti-bias, 5 formulas);
Part C applications informed-consent (information + comprehension + voluntariness,
reasonable-volunteer, withdrawal), risk/benefit assessment, selection. Any
redistribution of sense.body.redmi10 that contributes to generalizable knowledge,
or any new BODY_INFORMATION_TYPES member ("including new not-yet-developed ones"
does not auto-extend), is a practice/research boundary change reviewed under
docs/protocol-boundaries.md and .mishe-tauftauf/research/shared/personal-
multibody-analysis-20261015.md. Blanket disclosure alone does not satisfy
Belmont — vocabulary below makes comprehension, voluntariness, re-consent and
per-type risk/benefit/alternative reviewable without new I/O or verdict.
Enactive agency diagnostic (transfer, no logic/threshold change):
  Barandiaran, Di Paolo & Rohde, "Defining Agency: Individuality, Normativity,
  Asymmetry, and Spatio-temporality in Action," Adaptive Behavior 17(5):367-386
  (2009) doi:10.1177/1059712309343819 — generative definition pp.376-377, Eq.1
  dS/dt = F_Q(S,E), Eq.2 dE/dt = G_Q(S,E), Eq.3 Δp = H_T(S) p⊂Q p.372, Table 1
  p.374, Fig.1 p.377. Use Table 1 as a per-channel check without new
  instrumentation:
    (a) individuality — which network/boundary constitutes the individual
        (phone alone vs human+phone extended system; observer-imposed boundary
        is not the individual's boundary, Jonas 1968 regress pp.370-371);
    (b) interactional asymmetry — which coupling constraints p⊂Q are claimed
        modulated by S over interval T per Eq.3 vs passively suffered by S;
        not mere correlation or energetic investment (gliding/bird examples p.371);
    (c) normativity — which viability condition generated by that individual's
        own organisation the modulation is assessed against (failure is failure
        *for* that system, p.372-373), not an externally judged dashboard threshold.
  Discipline: raw battery/light/acceleration device evidence alone does not
  satisfy all three — at most individuality (where a membrane/network can be
  argued) plus externally judged appropriateness — and must not be read as an
  agent's action until a viability-relative regulation is named. Keep the
  existing "Device evidence is not a human movement/presence claim" warning and
  UNKNOWN handling; this note adds vocabulary only, no auto-adaptation or
  threshold drift. Any agency claim must state the observer's cut: which
  boundary is judged and whether the judgement is about closure vs
  viability-relative adaptivity (cf. enactivism--recent Tomczyk 2025 pp.61-62
  observer co-construction caveat). Future implementable audit (outside this
  prose change): one channel (light or acceleration) with declared boundary,
  one viability condition, and one Δp-over-T observation exercised against Table 1.
"""

from datetime import datetime, timezone
import json
import math
import time
from pathlib import Path
from typing import Final

SENSE_ID = "sense.body.redmi10"
ANDROID_SESSION = "android-body"
REDMI_SOURCE = "http://127.0.0.1:8765/node/redmi10"
FRESH_SECONDS = 30.0
MAX_REGISTRY_BYTES = 65536
MAX_STATE_BYTES = 1048576
MAX_SEQUENCE = 9007199254740991

#: Contextual-integrity vocabulary — Nissenbaum 2004 §§III–III.B (Walzer
#: Spheres of Justice) + Barth et al. 2006 §2; hardened per Diamantis BEQ
#: 2025 §§3.2–3.6 ("The Danger of Contextual Integrity"). Five parameters:
#: context, subject/sender/recipient, information type, transmission
#: principle. Either appropriateness *or* flow violation is a breach.
#: Closed-world: any (context, info_type) or (context, principle,
#: recipient) not listed is denied; no "arenas not governed by norms"
#: licence to add norms by assertion.
PERSONAL_CONTEXTS: Final[frozenset[str]] = frozenset({
    "personal-carrying",
    "health-diagnosis",
    "host-radio",
    "host-network",
    "publication",
})
#: BODY_INFORMATION_TYPES is closed-world. The 2026-10-15 mandate's
#: "including new not-yet-developed ones" does not auto-extend this set:
#: adding a new type (e.g., microphone, geolocation, csi_presence) requires
#: an explicit entry here *and* a revisited per-type human consent record —
#: not a table edit alone. Unlisted types are denied (frog-in-hot-water
#: detector, Diamantis BEQ p.16–17); without per-type consent the type
#: remains outside the CI tables.
BODY_INFORMATION_TYPES: Final[frozenset[str]] = frozenset({
    "battery",
    "light",
    "acceleration",
    "wifi_link_rssi_dbm",
    "ble_scan",
})
#: TRANSMISSION_PRINCIPLES is exhaustive (four entries). General-value
#: balancing ("democracy, freedom, wealth, efficiency, other social values"
#: Diamantis BEQ p.9, §3.2) is excluded; any new principle requires an
#: explicit entry here *and* a revisited consent record. Unlisted
#: principles are denied — value-indeterminacy vector typed as deny.
TRANSMISSION_PRINCIPLES: Final[frozenset[str]] = frozenset({
    "human-consented-personal-analysis",
    "confidential-to-operator",
    "need-for-health-diagnosis",
    "public-by-explicit-disclosure",
})
#: Appropriateness table: which information types are fitting to reveal in
#: each context. Personal-carrying and health-diagnosis may carry personal
#: body channels; publication carries none by default — per-item disclosure
#: is a distribution question, not an appropriateness one. Host contexts carry
#: no body information types (they govern adapter state, not person evidence).
#: Context is fixed by the authorising collection consent (2026-10-15
#: personal-multibody mandate: personal-carrying for Redmi body channels,
#: host-radio/host-network for adapter state), not by information type shape —
#: ble_scan collected as personal-carrying does not become host-radio because
#: it mentions a radio; post-hoc relabelling is a privacy-boundary change
#: reviewed under docs/protocol-boundaries.md (Diamantis BEQ §3.3).
_APPROPRIATENESS: Final[dict[str, frozenset[str]]] = {
    "personal-carrying": BODY_INFORMATION_TYPES,
    "health-diagnosis": BODY_INFORMATION_TYPES,
    "host-radio": frozenset(),
    "host-network": frozenset(),
    "publication": frozenset(),
}
#: Flow table: which (context, transmission principle) pairs allow which
#: recipients. The 2026-10-15 human consent is the principle
#: "human-consented-personal-analysis" confined to non-public recipients;
#: "public-by-explicit-disclosure" is the only principle that allows the
#: publication context. Any pair not listed denies the flow. Frog
#: detector: each new information type without per-type consent would become
#: the new benchmark CI cements (Diamantis BEQ pp.16–17); the blanket clause
#: is therefore non-self-executing.
_CI_FLOWS: Final[dict[tuple[str, str], frozenset[str]]] = {
    ("personal-carrying", "human-consented-personal-analysis"): frozenset({
        "senses", "health", "body-research", "inference-research",
    }),
    ("personal-carrying", "confidential-to-operator"): frozenset({
        "senses", "operator",
    }),
    ("personal-carrying", "need-for-health-diagnosis"): frozenset({
        "health", "senses",
    }),
    ("health-diagnosis", "need-for-health-diagnosis"): frozenset({
        "health", "senses",
    }),
    ("health-diagnosis", "human-consented-personal-analysis"): frozenset({
        "senses", "health", "body-research", "inference-research",
    }),
    ("publication", "public-by-explicit-disclosure"): frozenset({
        "pub", "docs", "witness", "genome", "health", "senses",
        "body-research", "inference-research", "operator",
    }),
    ("host-radio", "need-for-health-diagnosis"): frozenset({
        "health", "senses", "genome",
    }),
}
#: Barth et al. 2006 positive/negative duality — Figure 1 / Formula (1)
#: boundary note (2026-11-03 misread REVISIT, complements 2026-11-01
#: context/principle collapse + static-grant + unenforced predicates).
#: Barth Abstract/§1/§3 define two duals: positive norms permit
#: communication if their temporal condition holds (disjunction over
#: norms+(c)), negative norms permit only if their condition holds
#: (conjunction over norms-(c)). Formula (1) Fig.1 p.5-6: a trace σ
#: satisfies the norms of context c iff it is allowed by at least one
#: positive *and* respects all negatives. Doctor/researcher dual §3.3
#: p.6: the same confidentiality ("if Alice tells Bob under
#: confidentiality then in the future Bob must not disclose") as a
#: positive is bypassable by another positive; as a negative it binds
#: "regardless of how he obtained them from Alice" — surviving alternative
#: authorization. This module currently encodes only positive norms as
#: allow-lists (_APPROPRIATENESS for appropriateness, _CI_FLOWS for
#: distribution); deny is closed-world absence ("not listed is denied,"
#: Diamantis BEQ p.9 hardened), not an explicit negative norm. No
#: negative norm with a future temporal obligation ψ (e.g., Barth §3.3
#: confidentiality future "must not disclose") is typed;
#: "confidential-to-operator" is a positive label permitting flow to
#: {senses, operator} under that principle, not a surviving negative
#: obligation. sender/subject are carried in
#: contextual_integrity_allows(context, sender, recipient, subject,
#: info_type, transmission_principle) for the five-parameter shape (§2)
#: but are not enforced by either table — psychiatrist-vs-HR role
#: distinction and any inrole(p,r)/incontext(p,c) check are absent; no
#: role-generalization closure ("if (Alice, psychiatrist)∈ρ then
#: (Alice, doctor)∈ρ" p.4) and no data-hierarchy closure under
#: computation rules I ("postal address → postal code" p.4,
#: content(m) closed under I, BODY_INFORMATION_TYPES flat). Predicates
#: remain pointwise, atemporal booleans over one proposed flow; no trace
#: σ, no past/future operator evaluation. Per 2026-11-01 still unenforced
#: (no caller in src/tests/tools as of 2026-11-03). Do not describe the
#: tables as enforcing Barth's Formula (1) until a negative norm and a
#: trace check exist; when either lands name the norm and caller here and
#: re-check this note. Revertible by git revert; no verdict/behaviour
#: change.
#: Consent receipts — Kaye et al., "Dynamic consent: a patient interface for
#: twenty-first century research networks," EJHG 23, 141-146 (2015) Boxes 1-3.
#: "machine-readable disclosure policies or 'sticky policies' that attach to
#: data. This package of 'wrapped information', which contains specific consent
#: provisions, travels with a participant or donor's data and samples as these
#: are shared or accessed" (p.143). Boxes 2-3: one record of all transactions
#: in one place; consent preferences can be modified over time; participants
#: "consent to new projects … or alter their consent preferences in real time."
#: Per-type revisability without replacing broad consent (p.143 fatigue answer):
#: the 2026-10-15 blanket stays valid for the five listed types, but each type
#: carries its own dated receipt (origin, principle, recipients, scope_note)
#: that can be revoked or narrowed without editing _APPROPRIATENESS/_CI_FLOWS.
#: Adding microphone/geolocation/csi_presence does not inherit the blanket — it
#: needs a new receipt. This makes Diamantis BEQ pp.16-17 non-self-executing
#: blanket typed: a new BODY_INFORMATION_TYPES entry without a receipt remains
#: denied. Homomorphic encryption, web/SMS interface, LIMS not transferred —
#: only typed receipts that travel as metadata beside the reading.
#: Pure data, no I/O, no SENSE_ID/verdict, no mandate file change.
#: Consent-model taxonomy — Gilbert et al., "Enabling secure and self
#: determined health data sharing and consent management," npj Digital Medicine
#: 8:560 (2025) Table 1 p.2: this module's per-type dated receipts are dynamic
#: consent (ongoing, case-by-case, high control/high flexibility per Kaye Boxes
#: 1-3 as cited above), not broad (one-time/upfront up to 5y, limited
#: control/low flexibility), not meta (user-defined broad-or-dynamic choice),
#: not Standard Health Consent (SHC; platform-based standardized). Dynamic's
#: stated challenge per Table 1 Challenges and §2 p.2 Refs 16–19 is consent
#: fatigue and tech requirements; per-type narrowness mitigates broad
#: overbreadth without mitigating fatigue; no revocation evaluation or SHC
#: infrastructure is added (personal_consent_allows ignores date per 2026-11-01
#: misread); fatigue/SHC handling would need a separate review.
CONSENT_RECEIPT_VERSION: Final[str] = "1"
#: Per-type dated consent receipts — the sticky policy that travels with the
#: reading (Kaye EJHG p.143). Each BODY_INFORMATION_TYPES member maps to
#: {principle, origin, date, recipients, scope_note}. "Preferences travel with
#: samples/information." One record of all transactions, revisable per type.
PERSONAL_CONSENT_RECEIPTS: Final[dict[str, dict[str, object]]] = {
    "battery": {
        "principle": "human-consented-personal-analysis",
        "origin": ".mishe-tauftauf/research/shared/personal-multibody-analysis-20261015.md",
        "date": "2026-10-15",
        "recipients": frozenset({"senses", "health", "body-research", "inference-research"}),
        "scope_note": "personal-carrying battery; device evidence, not person claim; sticky-policy scope for redistribution",
    },
    "light": {
        "principle": "human-consented-personal-analysis",
        "origin": ".mishe-tauftauf/research/shared/personal-multibody-analysis-20261015.md",
        "date": "2026-10-15",
        "recipients": frozenset({"senses", "health", "body-research", "inference-research"}),
        "scope_note": "personal-carrying light (lux); device evidence, not person claim; sticky-policy scope for redistribution",
    },
    "acceleration": {
        "principle": "human-consented-personal-analysis",
        "origin": ".mishe-tauftauf/research/shared/personal-multibody-analysis-20261015.md",
        "date": "2026-10-15",
        "recipients": frozenset({"senses", "health", "body-research", "inference-research"}),
        "scope_note": "personal-carrying acceleration incl. gravity; device evidence, not person claim; sticky-policy scope",
    },
    "wifi_link_rssi_dbm": {
        "principle": "human-consented-personal-analysis",
        "origin": ".mishe-tauftauf/research/shared/personal-multibody-analysis-20261015.md",
        "date": "2026-10-15",
        "recipients": frozenset({"senses", "health", "body-research", "inference-research"}),
        "scope_note": "personal-carrying wifi_link_rssi_dbm (auxiliary radio context); device evidence, sticky-policy scope",
    },
    "ble_scan": {
        "principle": "human-consented-personal-analysis",
        "origin": ".mishe-tauftauf/research/shared/personal-multibody-analysis-20261015.md",
        "date": "2026-10-15",
        "recipients": frozenset({"senses", "health", "body-research", "inference-research"}),
        "scope_note": "personal-carrying ble_scan; auxiliary, collected as personal-carrying not host-radio; sticky-policy scope",
    },
}
#: Verifiable-Credential receipt shape — W3C Verifiable Credentials Data Model
#: v2.0 REC 2025-05-15 §1.1/§1.2/§3/§4.7/§4.8/§4.10/§4.12/§5.1/§7.1/§8 via
#: https://www.w3.org/TR/vc-data-model-2.0/ (2026-11-03 transfer REVISIT
#: complements Kaye EJHG 2015 sticky policy + 2026-11-01 GDPR Art.7/Kantara
#: discard + 2026-11-03 Pufferfish TODS correlated-record note at
#: DENSE_TAIL_BYTES). VC data model: verifiable credential expresses issuer
#: claims about a credentialSubject secured by a proof, in an
#: issuer–holder–verifier ecosystem with a verifiable data registry for
#: identifiers and revocation (§1.2/§5.1). Mapping onto
#: PERSONAL_CONSENT_RECEIPTS: issuer (§4.7 MUST URL, RECOMMENDED
#: dereferenceable controlled identifier document) vs current ``origin`` file
#: path; credentialSubject (§4.8 property-value claim about the subject per
#: BODY_INFORMATION_TYPES type) vs current bare dict; validity
#: (validFrom/validUntil §4.9) + credentialStatus (§4.10 e.g.
#: BitstringStatusListEntry) vs no expiry/revocation; proof/securing
#: mechanism (§4.12, §7.1 verification) vs tamper-invisible constant; holder
#: = reading carrying the sticky policy, verifier = recipient caller in
#: _CI_FLOWS/personal_consent_allows, registry = status publisher (§1.2/§5.1).
#: PERSONAL_CONSENT_RECEIPTS is not a VC: no issuer URL/identifier document,
#: no proof, no status list, no verifier check; demonstration therefore rests
#: on the static source note, not on a verifiable presentation — re-stating the
#: 2026-11-01 demonstrability/withdrawal-parity gap in VC vocabulary without
#: wiring withdrawal. Privacy caution §8.11/§8.4 identifier-correlation and
#: §8.13 aggregation: making receipts verifiable must not add a linkable
#: long-lived identifier without correlation analysis; correlation across
#: DENSE_TAIL_BYTES windows (Pufferfish) and aggregation remain separate risks
#: not solved by VC shape alone. Keeps Kaye revisability and Pufferfish
#: correlation notes as complements; revertible by git revert, no behaviour
#: change.

#: Belmont Report (FR 44:23192, 1979-04-18) via ORI/HHS — practice vs research
#: and three principles. Pure vocabulary beside PERSONAL_CONSENT_RECEIPTS;
#: no verdict, no I/O, no sensor. Practice = interventions solely for the
#: well-being of this deployment with reasonable expectation of success;
#: research = activity designed to test a hypothesis, permit conclusions, and
#: contribute to generalizable knowledge (formal protocol/review). Any element
#: of research in an activity means the activity should undergo review (Part A).
#: The 2026-10-15 mandate's "including new not-yet-developed ones" does not
#: auto-extend this vocabulary either — a new BODY_INFORMATION_TYPES member is
#: a practice/research boundary change reviewed under docs/protocol-boundaries.md
#: and .mishe-tauftauf/research/shared/personal-multibody-analysis-20261015.md.
#: Blanket disclosure alone does not satisfy Belmont; the per-type assessment
#: makes information+comprehension+voluntariness, risk/benefit and selection
#: reviewable (Parts B+C).
BELMONT_PRINCIPLES: Final[frozenset[str]] = frozenset({
    "respect-for-persons",
    "beneficence",
    "justice",
})
BELMONT_APPLICATIONS: Final[frozenset[str]] = frozenset({
    "informed-consent",
    "risk-benefit-assessment",
    "selection",
})
PRACTICE_VS_RESEARCH: Final[frozenset[str]] = frozenset({
    "practice",
    "research",
})
#: Per-type Belmont assessment — non-normative, additive, closed-world. Each
#: BODY_INFORMATION_TYPES member maps to {purpose, is_research, risks,
#: benefits, less_burdensome_alternative, information, comprehension,
#: voluntariness, withdrawal_path, reconsent_due}. is_research marks whether
#: the current use draws generalizable knowledge (Part A any-element rule);
#: information/comprehension/voluntariness + withdrawal_path encode Part C.1
#: (reasonable-volunteer, no coercion/undue influence, opportunity to ask
#: and to withdraw). reconsent_due marks whether the dated receipt is stale
#: without periodic reevaluation (Part B.1). Risks/benefits use Belmont B.2
#: systematic probability/magnitude; less_burdensome_alternative enforces
#: beneficence minimization; selection/justice is captured in purpose and
#: recipient scope (B.3). Unlisted types have no stanza and fail completeness.
BELMONT_PER_TYPE_ASSESSMENT: Final[dict[str, dict[str, object]]] = {
    "battery": {
        "purpose": "operational liveness of carried body (panel freshness, bounded recovery); retained history contributes to validity-window and carrying-context generalizable knowledge",
        "is_research": True,
        "risks": "low probability re-identification from battery+time sequences; git retention increases linkability; magnitude limited to level/charging",
        "benefits": "visible freshness for operator decisions; delivery-gap diagnostics",
        "less_burdensome_alternative": "coarser battery polling interval; host-only fallback when body unavailable",
        "information": True,
        "comprehension": True,
        "voluntariness": True,
        "withdrawal_path": "revoke per-type receipt or remove linked-site; no coercion per 2026-10-15 mandate",
        "reconsent_due": False,
    },
    "light": {
        "purpose": "operational panel freshness and delivery diagnostics; retained lux history contributes to validity-window and carrying-context generalizable knowledge",
        "is_research": True,
        "risks": "low probability re-identification from light+accel sequences; git retention increases linkability; magnitude limited to ambient lux bucket",
        "benefits": "visible Unknown/verified freshness for operator; bounded recovery detection",
        "less_burdensome_alternative": "coarser light bucket or longer polling interval; host-radio fallback when body unavailable",
        "information": True,
        "comprehension": True,
        "voluntariness": True,
        "withdrawal_path": "revoke per-type receipt or remove linked-site; no coercion per 2026-10-15 mandate",
        "reconsent_due": False,
    },
    "acceleration": {
        "purpose": "operational motion-leg availability for freshness framing; retained accel history contributes to carrying-context generalizable knowledge",
        "is_research": True,
        "risks": "moderate probability of activity inference from accel sequences incl. gravity; git retention increases linkability",
        "benefits": "distinguish moving vs unavailable vs stale for honest Unknown; supports dense-baseline windowing",
        "less_burdensome_alternative": "publish motion-leg-absent with producer reason instead of raw accel; increase polling interval",
        "information": True,
        "comprehension": True,
        "voluntariness": True,
        "withdrawal_path": "revoke per-type receipt or remove linked-site; no coercion per 2026-10-15 mandate",
        "reconsent_due": False,
    },
    "wifi_link_rssi_dbm": {
        "purpose": "operational radio context for recovery diagnostics; retained rssi history contributes to link-quality generalizable knowledge",
        "is_research": True,
        "risks": "low probability inference of proximity from rssi; git retention linkability low magnitude",
        "benefits": "bounded recovery and delivery-gap diagnosis when body link degrades",
        "less_burdensome_alternative": "omit rssi and rely on delivery-gaps + peer-delivery senses; redact on publication",
        "information": True,
        "comprehension": True,
        "voluntariness": True,
        "withdrawal_path": "revoke per-type receipt or remove linked-site; no coercion per 2026-10-15 mandate",
        "reconsent_due": False,
    },
    "ble_scan": {
        "purpose": "operational auxiliary radio scan for diagnostics; retained scan history contributes to environment-context generalizable knowledge",
        "is_research": True,
        "risks": "low probability of environment fingerprinting from scan results; auxiliary channel, not host-radio",
        "benefits": "auxiliary evidence that body is carried in a radio environment; supports peer-delivery correlation",
        "less_burdensome_alternative": "disable ble_scan channel or publish error-only summary; keep host-radio separate",
        "information": True,
        "comprehension": True,
        "voluntariness": True,
        "withdrawal_path": "revoke per-type receipt or remove linked-site; no coercion per 2026-10-15 mandate",
        "reconsent_due": False,
    },
}



def is_body_info_appropriate_for_context(context: str, info_type: str) -> bool:
    """Whether ``info_type`` is appropriate to reveal in ``context``.

    Nissenbaum 2004 §III.A: norms of appropriateness dictate what information
    about persons is fitting in a given context; no context is norm-free.
    Diamantis BEQ 2025 §3.3: context is fixed by the authorising consent
    (personal-carrying for Redmi body channels per 2026-10-15 mandate), not
    by information type shape; ble_scan stays personal-carrying, not
    host-radio; post-hoc relabelling is a privacy-boundary change. Pure view
    over :data:`_APPROPRIATENESS`; no I/O, no ``sense.*`` verdict.
    """
    allowed = _APPROPRIATENESS.get(context)
    if allowed is None:
        return False
    return info_type in allowed


def contextual_integrity_allows(
    *,
    context: str,
    sender: str,
    recipient: str,
    subject: str,
    info_type: str,
    transmission_principle: str,
) -> bool:
    """Whether a flow respects contextual integrity (appropriateness + flow).

    Nissenbaum 2004 §§III–III.B via Walzer; Barth et al. 2006 §2 five
    parameters (context, sender/recipient/subject, information type,
    transmission principle). Either norm violated is a breach. Hardened per
    Diamantis BEQ 2025 §§3.2–3.6: closed-world (unlisted info_type,
    principle, or recipient denies), context fixed by authorising consent,
    no post-hoc relabelling; frog/value vectors typed as deny.
    Pure view over :data:`_APPROPRIATENESS` and :data:`_CI_FLOWS`; no I/O,
    no verdict. ``sender``/``subject`` are carried for the five-parameter
    shape and for future subject-scoped policy; the current table enforces
    context + information type (appropriateness) and context + principle +
    recipient (distribution). A flow claiming a different context for the
    same bytes is a privacy-boundary change reviewed under
    docs/protocol-boundaries.md.
    """
    if not is_body_info_appropriate_for_context(context, info_type):
        return False
    allowed = _CI_FLOWS.get((context, transmission_principle))
    if allowed is None:
        return False
    return recipient in allowed


def personal_body_flow_allows(
    info_type: str,
    recipient: str,
    transmission_principle: str,
) -> bool:
    """Specialised CI check for the Redmi personal-carrying context.

    Decodes the 2026-10-15 human consent as the principle
    ``human-consented-personal-analysis`` confined to non-public recipients.
    Shorthand for ``contextual_integrity_allows(context='personal-carrying',
    sender='redmi10', recipient=recipient, subject='human',
    info_type=info_type, transmission_principle=transmission_principle)``.
    Pure, no I/O.

    Boundary note (field-review 2026-11-01, Barth et al. 2006 §§2, 3.3):
    this is a static grant. The consent is encoded as a fixed table row, so
    the receipt ``date`` and any revocation are not consulted; opt-in is not
    modelled as a past-tense condition on the trace. On the
    ``personal-carrying`` path the flow check here and the receipt check in
    :func:`personal_consent_allows` use the same allow-list, so the second
    is redundant rather than an independent gate. As of 2026-11-01 no
    caller in ``src/``, ``tests/`` or ``tools/`` invokes this predicate:
    it is an unenforced view, not a guard on flows. Do not describe it as
    guarding flows until a caller exists; if one is added, name that caller
    here and re-check this note.
    """
    return contextual_integrity_allows(
        context="personal-carrying",
        sender="redmi10",
        recipient=recipient,
        subject="human",
        info_type=info_type,
        transmission_principle=transmission_principle,
    )


def has_personal_consent_receipt(info_type: str) -> bool:
    """Whether a dated per-type consent receipt exists for ``info_type``.

    Kaye et al. EJHG 2015 Boxes 2-3: one record of all transactions;
    preferences are revisable per type. A new BODY_INFORMATION_TYPES entry
    without a receipt is denied (typed frog-in-hot-water detector;
    Diamantis BEQ pp.16-17 non-self-executing blanket, now typed).
    Pure view over :data:`PERSONAL_CONSENT_RECEIPTS`; no I/O, no verdict.
    """
    return info_type in PERSONAL_CONSENT_RECEIPTS


def personal_consent_allows(
    *,
    info_type: str,
    recipient: str,
    transmission_principle: str,
) -> bool:
    """Whether ``info_type`` may flow to ``recipient`` under the receipt.

    Kaye EJHG p.143: "machine-readable disclosure policies or 'sticky
    policies' that attach to data" — the wrapper travels with the
    reading. This predicate is the typed wrapper check: conjunction of
    (1) a dated per-type receipt exists, (2) receipt principle equals the
    requested principle, (3) recipient is in receipt recipients, and
    (4) contextual integrity would also allow the flow (appropriateness +
    distribution). New types without receipts are denied even if added to
    BODY_INFORMATION_TYPES. Blanket non-self-executing, now typed.
    Pure, no I/O, no SENSE_ID/verdict; redistribution still a
    privacy-boundary change reviewed under docs/protocol-boundaries.md.

    Boundary note (field-review 2026-11-01, Barth et al. 2006 §§2, 3.3):
    the receipt ``date`` and revocation state are not read here, so opt-in
    is a static grant rather than the past-tense condition Barth §3.3
    describes. Condition (3) and condition (4) draw on the same allow-list
    for the ``personal-carrying`` path, so the receipt check is redundant
    with :func:`personal_body_flow_allows`, not an independent constraint.
    As of 2026-11-01 no caller in ``src/``, ``tests/`` or ``tools/`` invokes
    this predicate; it is unenforced and does not guard flows. Name the
    caller here and re-check this note if one is added.
    """
    receipt = PERSONAL_CONSENT_RECEIPTS.get(info_type)
    if receipt is None:
        return False
    if receipt.get("principle") != transmission_principle:
        return False
    recipients = receipt.get("recipients")
    if not isinstance(recipients, frozenset):
        return False
    if recipient not in recipients:
        return False
    return contextual_integrity_allows(
        context="personal-carrying",
        sender="redmi10",
        recipient=recipient,
        subject="human",
        info_type=info_type,
        transmission_principle=transmission_principle,
    )


def is_belmont_informed_consent_complete(info_type: str) -> bool:
    """Whether Belmont C.1 informed consent is complete for ``info_type``.

    Belmont Report FR 44:23192 Parts B.1 + C.1: informed consent requires
    three elements — information, comprehension and voluntariness — plus
    the opportunity to ask and to withdraw at any time (reasonable-volunteer
    standard, no coercion/undue influence, periodically reevaluated). This
    predicate is the typed completeness check: a dated per-type receipt must
    exist and the :data:`BELMONT_PER_TYPE_ASSESSMENT` stanza for that type
    must record that information, comprehension and voluntariness have been
    addressed with an explicit withdrawal path and that re-consent is not
    due (non-stale). Unlisted types without a receipt and without a stanza
    fail completeness (closed-world deny). Pure view over
    :data:`PERSONAL_CONSENT_RECEIPTS` and :data:`BELMONT_PER_TYPE_ASSESSMENT`;
    no I/O, no ``sense.*`` verdict.
    """
    if info_type not in PERSONAL_CONSENT_RECEIPTS:
        return False
    assessment = BELMONT_PER_TYPE_ASSESSMENT.get(info_type)
    if assessment is None:
        return False
    if not assessment.get("information"):
        return False
    if not assessment.get("comprehension"):
        return False
    if not assessment.get("voluntariness"):
        return False
    withdrawal = assessment.get("withdrawal_path")
    if not isinstance(withdrawal, str) or not withdrawal:
        return False
    if assessment.get("reconsent_due"):
        return False
    return True


def belmont_requires_review(info_type: str) -> bool:
    """Whether Belmont Part A requires review for ``info_type``.

    Belmont FR 44:23192 Part A: practice vs research — if there is any
    element of research (hypothesis + generalizable knowledge + formal
    protocol) in an activity, the activity should undergo review. This
    predicate returns ``True`` when the :data:`BELMONT_PER_TYPE_ASSESSMENT`
    stanza for ``info_type`` marks ``is_research`` as true — i.e., the
    current purpose draws generalizable knowledge beyond operational
    well-being of this deployment. Sense.body.redmi10 retained in git,
    counted in reviews, or used for validity-window tuning is research by
    that rule. Unlisted types without a stanza return ``False``
    (closed-world: no assessment, no reviewable element typed). Pure view;
    no I/O, no verdict.
    """
    assessment = BELMONT_PER_TYPE_ASSESSMENT.get(info_type)
    if assessment is None:
        return False
    return bool(assessment.get("is_research"))


def _unknown(reason: str) -> dict[str, object]:
    return {"id": SENSE_ID, "kind": "read", "state": "unknown", "reason": reason,
            "sample": f"Redmi10 UNKNOWN: {reason}; source=linked Android Body consumer"}


def _read_json(path: Path, limit: int) -> tuple[dict[str, object] | None, str | None]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "unreadable"
    if len(raw) > limit:
        return None, "oversized"
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "malformed"
    if not isinstance(value, dict):
        return None, "not-an-object"
    return value, None


def _finite_number(value: object) -> float | None:
    if type(value) not in (int, float, str):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _linked_android_home(home: Path) -> tuple[Path | None, str | None]:
    registry, error = _read_json(home / "health" / "linked-sites.json", MAX_REGISTRY_BYTES)
    if error is not None:
        return None, f"linked-site-registry-{error}"
    if (type(registry.get("version")) is not int or registry.get("version") != 1
            or not isinstance(registry.get("sites"), list)):
        return None, "linked-site-registry-invalid"
    candidates = [site for site in registry["sites"]
                  if isinstance(site, dict) and site.get("session") == ANDROID_SESSION]
    if len(candidates) != 1:
        return None, "linked-android-body-site-missing-or-ambiguous"
    site_home = candidates[0].get("home")
    if not isinstance(site_home, str) or not Path(site_home).is_absolute():
        return None, "linked-android-body-home-invalid"
    return Path(site_home), None


def _row_status(
        row: object, kind: str, now: float,
) -> tuple[str, dict[str, str] | None, dict[str, object] | None]:
    if row is None:
        return "unknown/no-observation", None, None
    if not isinstance(row, dict) or row.get("kind") != kind:
        return "unknown/malformed-row", None, None
    sequence = row.get("sequence")
    received = _finite_number(row.get("received"))
    collected = _finite_number(row.get("collected"))
    fields = row.get("fields")
    if (type(sequence) is not int or not 0 < sequence <= MAX_SEQUENCE
            or received is None or collected is None or not isinstance(fields, dict)
            or any(not isinstance(key, str) or not isinstance(value, str)
                   for key, value in fields.items())):
        return "unknown/malformed-row", None, None
    if type(row.get("backlog_page_pending_at_receipt")) is not bool:
        return "unknown/malformed-row", None, None
    if row["backlog_page_pending_at_receipt"]:
        return "unknown/backlog", fields, row
    age = now - received
    if age < -5:
        return "unknown/clock-future", fields, row
    if age > FRESH_SECONDS:
        return "stale", fields, row
    if "age_ms" in fields:
        sensor_age = _finite_number(fields["age_ms"])
        if sensor_age is None or sensor_age < 0:
            return "unknown/sensor-age", fields, row
        if sensor_age > FRESH_SECONDS * 1000:
            return "stale", fields, row
    return "fresh-clock-conditional", fields, row




def _utc(epoch: float | None) -> str:
    if epoch is None:
        return "unknown"
    try:
        return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return "unknown"


def _value(fields: dict[str, str] | None, key: str) -> float | None:
    return _finite_number(fields.get(key)) if fields is not None else None


def _auxiliary(latest: dict[str, object], key: str, now: float) -> tuple[str, str | None]:
    row = latest.get(key)
    if row is None:
        return "unknown/no-observation", None
    if not isinstance(row, dict) or row.get("kind") != key:
        return "unknown/malformed-row", None
    status, fields, _ = _row_status(row, key, now)
    reason = fields.get("reason") if fields is not None else None
    if status == "fresh-clock-conditional" and reason:
        return status, reason
    return status, reason


def read_redmi10(home: Path, now: float | None = None) -> dict[str, object]:
    """Return one bounded, receipt-fresh reading; never infer human movement."""
    current = time.time() if now is None else _finite_number(now)
    if current is None:
        return _unknown("reader-clock-invalid")
    android_home, error = _linked_android_home(home)
    if error is not None or android_home is None:
        return _unknown(error or "linked-android-body-home-unknown")
    path = android_home / "body" / "perception" / "redmi10.json"
    state, error = _read_json(path, MAX_STATE_BYTES)
    if error is not None or state is None:
        return _unknown(f"consumer-state-{error or 'unavailable'}")
    cursor = state.get("cursor")
    session = state.get("session")
    latest = state.get("latest")
    if (type(state.get("version")) is not int or state.get("version") != 1
            or state.get("node") != "redmi10" or state.get("source") != REDMI_SOURCE
            or type(cursor) is not int or not 0 <= cursor <= MAX_SEQUENCE
            or not isinstance(session, str) or not session.strip() or not isinstance(latest, dict)):
        return _unknown("consumer-state-invalid")
    if type(state.get("has_more")) is not bool or state.get("has_more"):
        return _unknown("consumer-backlog-or-state-invalid")
    poll_at = _finite_number(state.get("poll_at"))
    if poll_at is None or current - poll_at < -5:
        return _unknown("consumer-poll-clock-unknown")
    if current - poll_at > FRESH_SECONDS:
        return _unknown("consumer-poll-stale")
    if state.get("error") is not None:
        return _unknown("consumer-poll-error")
    statuses: dict[str, str] = {}
    fields_by_kind: dict[str, dict[str, str] | None] = {}
    rows: dict[str, dict[str, object] | None] = {}
    for kind in ("battery", "light", "acceleration"):
        statuses[kind], fields_by_kind[kind], rows[kind] = _row_status(
            latest.get(kind), kind, current)

    row_status = dict(statuses)
    battery = _value(fields_by_kind["battery"], "level")
    lux = _value(fields_by_kind["light"], "lux")
    axes = tuple(_value(fields_by_kind["acceleration"], axis) for axis in ("x", "y", "z"))
    magnitude = math.hypot(*axes) if all(value is not None for value in axes) else None
    if battery is None:
        statuses["battery"] = "unknown/value"
    if lux is None:
        statuses["light"] = "unknown/value"
    if battery is not None and not 0 <= battery <= 1:
        statuses["battery"] = "invalid/value"
    if lux is not None and lux < 0:
        statuses["light"] = "invalid/value"
    if magnitude is None:
        statuses["acceleration"] = "unknown/value"
    # The producer publishes a channel it has no current sample for, with its
    # own reason ("unavailable reason=stale"). That is a fact about the
    # source, and it is not the same fact as a payload this reader could not
    # use: the plant's own dense baseline already counts such a row as a
    # delivery and not as motion. Reading it as "unknown/value" hid the
    # producer's cause behind a word that reads like a reader-side fault, so
    # the cause is named here and the reader's verdict stays honest. Only a
    # current row can be producer-unavailable: a row that is old by age is
    # stale, and that word stays.
    unavailable: dict[str, str] = {}
    for kind, keys in (("battery", ("level",)),
                       ("light", ("lux",)),
                       ("acceleration", ("x", "y", "z"))):
        fields = fields_by_kind.get(kind)
        if fields is None or row_status.get(kind) != "fresh-clock-conditional":
            continue
        reason = fields.get("reason")
        if reason and all(key not in fields for key in keys):
            unavailable[kind] = reason
            statuses[kind] = f"unavailable/{reason}"
    battery_fields = fields_by_kind["battery"]
    charging = battery_fields.get("charging") if battery_fields is not None else None
    if charging not in {"true", "false"}:
        charging = "unknown"

    acceleration_row = rows["acceleration"]
    phone_time = _finite_number(acceleration_row.get("collected")) if acceleration_row else None
    receipt_time = _finite_number(acceleration_row.get("received")) if acceleration_row else None
    channel_provenance = {
        kind: {"sequence": row["sequence"],
               "phone_sample_epoch_s": row["collected"],
               "consumer_receipt_epoch_s": row["received"]}
        for kind, row in rows.items() if row is not None
    }
    battery_text = (f"{battery:.2f}{'+' if charging == 'true' else ''}"
                    if statuses["battery"] == "fresh-clock-conditional" and battery is not None
                    else statuses["battery"])
    light_text = (f"{lux:g}lux"
                  if statuses["light"] == "fresh-clock-conditional" and lux is not None
                  else statuses["light"])
    if statuses["acceleration"].startswith("unavailable/"):
        acceleration_text = statuses["acceleration"]
    elif statuses["acceleration"] == "fresh-clock-conditional" and magnitude is not None:
        acceleration_text = f"{magnitude:.2f}m/s² incl-g"
    elif statuses["acceleration"] == "stale":
        acceleration_text = "motion-leg-absent"
    else:
        acceleration_text = statuses["acceleration"]
    # Cursor progress remains in structured provenance/details, not the
    # observation summary: cursor-only advances must not re-wake the mind.
    sample = (f"Redmi10 sid={session} b={battery_text} L={light_text} "
              f"|a|={acceleration_text}")
    status = ("verified" if all(value == "fresh-clock-conditional"
                                for value in statuses.values()) else "unknown")

    wifi_status, wifi_reason = _auxiliary(latest, "wifi_link_rssi_dbm", current)
    ble_status, ble_reason = _auxiliary(latest, "ble_scan", current)
    wifi_text = (f"Wi-Fi {wifi_reason}" if wifi_status == "fresh-clock-conditional" and wifi_reason
                 else f"Wi-Fi {wifi_status}")
    ble_text = (f"BLE last scan error {ble_reason} (stale)"
                if ble_status == "stale" and ble_reason
                else f"BLE scan {ble_reason}" if ble_reason
                else f"BLE scan {ble_status}")
    transition = state.get("last_light_transition")
    if not isinstance(transition, dict):
        transition = None
    else:
        needed = {"kind", "bucket_from", "bucket_to", "claim", "freshness_at_detection",
                  "validity_seconds", "from", "to"}
        if not needed.issubset(transition):
            transition = None
        else:
            try:
                if (transition.get("kind") != "measured_light_bucket_transition"
                        or transition.get("claim") != "historical-measured-light-change-only"
                        or not isinstance(transition.get("bucket_from"), int)
                        or not isinstance(transition.get("bucket_to"), int)
                        or not isinstance(transition.get("validity_seconds"), int)
                        or not isinstance(transition.get("from"), dict)
                        or not isinstance(transition.get("to"), dict)):
                    transition = None
            except (TypeError, ValueError):
                transition = None
    transition_text = ""
    if transition is not None:
        transition_text = (" measured-light-transition="
                           f"{transition['bucket_from']}->{transition['bucket_to']}"
                           f" validity={transition['validity_seconds']}s"
                           f" freshness={transition['freshness_at_detection']};")
    unavailable_text = "".join(
        f"{kind} unavailable reason={reason}; "
        for kind, reason in sorted(unavailable.items()))
    details = (f"source={REDMI_SOURCE} session={session} cursor={cursor}; "
               f"acceleration phone_sample={_utc(phone_time)} consumer_receipt={_utc(receipt_time)}; "
               f"host-receipt freshness conditional; {unavailable_text}{wifi_text}; {ble_text};{transition_text} "
               "phone/host clock split unknown; acceleration includes gravity and is not person-movement evidence.")
    # The reason names the channels that are not current and why each is not,
    # so a reader is not left with a verdict and no cause.
    not_current = sorted(kind for kind, value in statuses.items()
                          if value != "fresh-clock-conditional")
    reason = ("fresh-clock-conditional" if status == "verified" else
              ",".join(f"{kind}={statuses[kind]}" for kind in not_current))
    return {"id": SENSE_ID, "kind": "read", "state": status,
            "reason": reason,
            "sample": sample, "source": REDMI_SOURCE, "cursor": cursor,
            "session": session,
            "channel_provenance": channel_provenance,
            "light_transition": transition,            "acceleration_phone_sample_epoch_s": phone_time,
            "acceleration_consumer_receipt_epoch_s": receipt_time,
            "channels": statuses, "details": details}

LIGHT_SENSE_ID = "sense.body.redmi10.light"

def read_redmi10_light(home: Path, now: float | None = None) -> dict[str, object]:
    """Return the fresh carried-device light reading even when motion is absent."""
    base = read_redmi10(home, now)
    if not isinstance(base, dict) or base.get("id") != SENSE_ID:
        return {"id": LIGHT_SENSE_ID, "kind": "read", "state": "unknown",
                "reason": "composite-reader-unavailable",
                "sample": "Redmi10 light UNKNOWN: reader unavailable",
                "source": REDMI_SOURCE}
    channels = base.get("channels") if isinstance(base.get("channels"), dict) else {}
    light_status = channels.get("light", "stale")
    state = "verified" if light_status == "fresh-clock-conditional" else "unknown"
    # The light reading's own reason is the light channel's own status: the
    # composite's reason names every channel that is not current, and a light
    # reading must not be given the acceleration channel's cause.
    reason = "fresh-clock-conditional" if state == "verified" else str(light_status)
    sample = str(base.get("sample", "Redmi10 light unknown"))
    return {"id": LIGHT_SENSE_ID, "kind": "read", "state": state, "reason": reason,
            "sample": sample, "source": REDMI_SOURCE,
            "cursor": base.get("cursor"), "session": base.get("session"),
            "channels": {"light": light_status},
            "light_transition": base.get("light_transition"),
            "details": ("Redmi10 light sense inherits Redmi consumer provenance; "
                        "acceleration may be unavailable without hiding carried light context.")}


GAP_SENSE_ID = "sense.body.redmi10.delivery-gaps"
GAP_LOG_NAME = "class-c-delivery-gap-detectors.jsonl"
EXCURSION_LOG_NAME = "excursion-log-redmi10.txt"
#: Both logs are bounded by the producer at 128 lines; the caps leave room for
#: the widest line each format can carry, so a full log is never read as
#: oversized and silently loses the reading it holds.
MAX_GAP_LOG_BYTES = 65536
MAX_EXCURSION_LOG_BYTES = 262144
#: The recurrence window: how far back the reported halt count reaches.
GAP_WINDOW_SECONDS = 86400.0

DENSE_SENSE_ID = "sense.body.redmi10.dense-baseline"
#: The carried-body rows the Senses pane re-reads at render time, so their
#: freshness is the render's, never the discovery scan's.
#: The dense baseline is deliberately excluded: it only updates on the
#: scan renewal, so the pane renders its scan row under the scan freshness
#: gate, keeping the producer's own stale/unknown verdict visible.
BODY_SENSE_IDS = frozenset({SENSE_ID, LIGHT_SENSE_ID, GAP_SENSE_ID})


def _gaps_unknown(reason: str, **extra: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": GAP_SENSE_ID, "kind": "read", "state": "unknown", "reason": reason,
        "sample": f"Redmi10 delivery gaps UNKNOWN: {reason}", "source": REDMI_SOURCE}
    row.update(extra)
    return row


def _read_text(path: Path, limit: int) -> tuple[str | None, str | None]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "unreadable"
    if len(raw) > limit:
        return None, "oversized"
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "malformed"


def _gap_records(text: str) -> tuple[list[dict[str, object]] | None, int]:
    """Parse the consumer's class-C halt records; ``None`` when any line is bad.

    A line that does not carry the published shape is counted, never guessed at:
    the count would be a lower bound, so the caller reports UNKNOWN instead of a
    number a reader could take for the whole history.
    """
    records: list[dict[str, object]] = []
    bad = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        detected = record.get("detected_at") if isinstance(record, dict) else None
        received = _finite_number(record.get("last_received_epoch_s")) if isinstance(record, dict) else None
        try:
            stamp = datetime.fromisoformat(detected) if isinstance(detected, str) else None
        except ValueError:
            stamp = None
        if (not isinstance(record, dict)
                or record.get("kind") != "class-c-delivery-gap"
                or type(record.get("cursor")) is not int
                or not 0 <= record["cursor"] <= MAX_SEQUENCE
                or not isinstance(record.get("session"), str) or not record["session"].strip()
                or received is None
                or type(record.get("host_stopped")) not in (bool, type(None))
                or stamp is None or stamp.tzinfo is None):
            bad += 1
            continue
        records.append({"detected_epoch_s": stamp.timestamp(), "last_received_epoch_s": received,
                        "cursor": record["cursor"], "session": record["session"],
                        "host_stopped": record["host_stopped"]})
    return (None, bad) if bad else (records, 0)


def _worst_batch_delay(text: str) -> float | None:
    """Widest receipt-minus-collection span in the retained excursion lines, in seconds.

    That span is how long a halted batch waited before the consumer received it:
    the flushed rows keep their phone sample times but not their receipt times.
    """
    worst: float | None = None
    for line in text.splitlines():
        if not line.startswith("excursion emitted=") or " node=redmi10 " not in line:
            continue
        for token in line.split():
            if not token.startswith("acquisition_span_ms="):
                continue
            high = _finite_number(token.partition("=")[2].partition(",")[2])
            if high is None or high < 0:
                return None
            if worst is None or high > worst:
                worst = high
    return None if worst is None else worst / 1000.0


def _lag_text(seconds: float) -> str:
    """A compact floor-rounded duration; the exact seconds stay structured.

    The pane clips a long sample at 120 cells, and a corrupted log can carry an
    absurd finite span, so the rendered token is bounded whatever the input is.
    """
    whole = int(seconds)
    if whole < 60:
        return f"{whole}s"
    minutes = whole // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h{minutes % 60}m"
    return f"{hours // 24}d{hours % 24}h"


def read_redmi10_delivery_gaps(home: Path, now: float | None = None) -> dict[str, object]:
    """Count the consumer's detected Redmi delivery halts over the last day.

    A halt is the consumer's class-C verdict: no new record for its window while
    polls keep succeeding, with no armed class-A or class-B cause. It is the
    phenomenon that leaves receipt ages in the retained window far behind the
    phone sample times, so a reader needs it before treating a Redmi window as
    contemporaneous. Counts are by detection time and say nothing about how long
    each halt lasted or why it started.
    """
    current = time.time() if now is None else _finite_number(now)
    if current is None:
        return _gaps_unknown("reader-clock-invalid")
    android_home, error = _linked_android_home(home)
    if error is not None or android_home is None:
        return _gaps_unknown(error or "linked-android-body-home-unknown")
    perception = android_home / "body" / "perception"
    text, error = _read_text(perception / GAP_LOG_NAME, MAX_GAP_LOG_BYTES)
    if error is not None or text is None:
        return _gaps_unknown(f"gap-log-{error or 'unreadable'}")
    records, bad = _gap_records(text)
    if records is None:
        return _gaps_unknown("gap-log-malformed", malformed=bad)
    if not records:
        return _gaps_unknown("gap-log-empty")
    records.sort(key=lambda record: record["detected_epoch_s"])
    if any(record["detected_epoch_s"] > current + 5 for record in records):
        return _gaps_unknown("gap-log-clock-future")
    window = [record for record in records
              if current - record["detected_epoch_s"] <= GAP_WINDOW_SECONDS]
    last = records[-1]
    excursion, excursion_error = _read_text(perception / EXCURSION_LOG_NAME, MAX_EXCURSION_LOG_BYTES)
    delay = _worst_batch_delay(excursion) if excursion is not None else None
    delay_text = _lag_text(delay) if delay is not None else (
        f"unknown-{excursion_error}" if excursion_error is not None else "unknown")
    delay_exact = f"{delay:g}s" if delay is not None else "unknown"
    stopped = last["host_stopped"]
    stopped_text = "unknown" if stopped is None else ("true" if stopped else "false")
    sample = (f"gaps-24h={len(window)} last-onset={_utc(last['last_received_epoch_s'])} "
              f"host-stopped={stopped_text} max-receipt-lag={delay_text}")
    details = (f"source=linked android-body body/perception/{GAP_LOG_NAME}; "
               f"window={GAP_WINDOW_SECONDS:.0f}s since={_utc(records[0]['detected_epoch_s'])} "
               f"records={len(records)} malformed={bad}; "
               "detector=consumer class-C (delivery halted while polls succeed); "
               "host-stopped is the detector's own adb read, not a delivery verdict; "
               f"max-receipt-lag={delay_text} ({delay_exact}) is the widest "
               "receipt-minus-collection span over the "
               f"retained {EXCURSION_LOG_NAME} lines, so a flushed batch keeps its phone "
               "sample times but not its receipt times; halt length and cause are not "
               "derivable from this log.")
    return {"id": GAP_SENSE_ID, "kind": "read", "state": "verified", "reason": "gap-log-current",
            "sample": sample, "source": REDMI_SOURCE,
            "gaps_24h": len(window), "gap_records": len(records),
            "last_onset_epoch_s": last["last_received_epoch_s"],
            "last_gap_session": last["session"], "last_gap_cursor": last["cursor"],
            "host_stopped": stopped, "max_receipt_lag_s": delay,
            "window_seconds": GAP_WINDOW_SECONDS, "details": details}

#: The consumer's dense observation queue file (append-only, ~5 s cadence).
DENSE_QUEUE_NAME = "redmi10.observations.log"
#: Tail bytes read from the dense queue; at ~101 bytes/line this yields ~2500
#: lines (~3.5 h at the observed 5 s cadence), bounding the analysis window.
#: This window is canonical sparse high-dimensional micro-data (Narayanan &
#: Shmatikov, "Robust De-anonymization of Large Datasets," arXiv:cs/0610105v2
#: 2007 Abstract + §1 p.1 "high dimensionality and sparsity … fat tail," §3 p.3
#: power law): each row carries rare attributes (e.g., specific ble_scan peer,
#: very-bright spike, exact wifi_link_rssi_dbm sequence) so Thm 1–3 pp.7–8 give
#: m = log N / -log(1-delta) random attributes for (1,1-epsilon)-deanonymization
#: — at N~2500 this is 5–10 auxiliary attributes. Algorithm 1A Score =
#: min_i Sim(aux_i, r'_i) and 1B wt(i)=1/log|supp(i)| + eccentricity gate (p.6)
#: are robust to perturbation/imprecision (p.4 "no difference …") and to sample
#: publication (Thm 4–5 pp.8–9); publishing raw tail rows or exact T[QI]
#: histograms preserves the sparsity the attack uses, and quoting rows in a wall
#: outcome is publishing a sample D^ of that tail.
DENSE_TAIL_BYTES = 262144
#: Rows retained for the compact summary; at 5 s cadence this is a ~2.8 h window.
DENSE_WINDOW_ROWS = 2000
#: Freshness gate for the dense-baseline verdict; matches ``FRESH_SECONDS``.
DENSE_FRESH_SECONDS = 30.0
#: --- Pufferfish correlated-record limit (Kifer & Machanavajjhala, TODS 39(1) 2014) ---
#: §1 p.A2 "GPS traces ... correlated records": record-level definitions
#: (k-anonymity, ℓ-diversity, ε-DP under independent-record D) do not bound
#: inference when rows are correlated — attacker with correlated prior learns
#: from neighbours even when k=2 and ℓ-diversity hold (cf. §6 Thm 6.1/6.2).
#: DENSE_TAIL_BYTES ~2500 rows (~3.5 h at 5 s) is that GPS-trace shape; temporal
#: correlation (dark↔sleep, consecutive rows predictive) is not represented in
#: QI_DEFAULT_KEYS=("light_class",) or any per-class diversity count. Framework
#: triple §3 p.A5 / Def 3.4 p.A7: privacy claim requires S (potential secrets),
#: Spairs⊆S×S (which pairs must stay indistinguishable), and D (plausible
#: data-generating distributions / attacker priors incl. correlations). This
#: plant declares none: L_DIVERSITY_SENSITIVE_KEY=None, no Spairs, no D.
#: Without D, Pufferfish "no assumptions" D is all distributions (too strong for
#: utility); §6 independent-record D that recovers ε-DP (Thm 6.1) is not the
#: dense queue's D (Thm 6.2 leaks under correlated D). Hence
#: k_anonymity_is_k_anonymous==True or "ℓ-diverse" does not bound correlated
#: time-series disclosure; raw tail rows, exact T[QI]/qi_sensitive_histogram
#: slices, or wall outcomes quoting tail rows are releases over correlated rows
#: requiring explicit D (or consent-receipt/DP handling via
#: src/mishe_tauftauf/stats_boundary.py Def 2.4 with explicit ε/δ/composition),
#: not "k>=2 safe". T-closeness §8 EMD calibration ("relationship between t and
#: information gain is unclear") is a separate still-open threshold problem;
#: choosing a different t does not solve correlation. Keeps 2026-11-01
#: homogeneity/background-knowledge note and Narayanan & Shmatikov sparsity note
#: as complements; revertible by git revert, no behaviour change.
#: Pufferfish mechanisms for correlated data — Song, Wang, Chaudhuri,
#: "Pufferfish Privacy Mechanisms for Correlated Data," SIGMOD '17 /
#: arXiv:1603.03977v3 §1/§2/§3/§4 via https://arxiv.org/abs/1603.03977
#: (2026-11-03 mechanism REVISIT, operational complement to TODS 2014
#: critique and to T-closeness §8 discard). Time-series setting §1 p.2:
#: one subject, correlated measurements across time, hide activity a vs b at
#: time t (physical-activity example) — entry-privacy ≈1 noise
#: insufficient, group DP O(T) destroys utility; Pufferfish interpolates
#: when "average amount" of correlation is low. Triple (Def 2.1) S
#: (secrets), Q⊆S×S (pairs to keep indistinguishable), Θ (class of
#: data-generating distributions / Markov Chains over activities §1 p.2);
#: privacy = Q indistinguishable under every θ∈Θ. Wasserstein Mechanism
#: (Algorithm 1, Def 3.1 ∞-Wasserstein W∞, Theorems 3.2–3.3): for
#: L-Lipschitz query F add Laplace noise scale W/ε with
#: W = sup_{(s_i,s_j)∈Q, θ∈Θ} W∞(μ_{i,θ}, μ_{j,θ}) over output
#: distributions; general but inefficient, reduces to Laplace under
#: independent Θ (Thm 3.3). Markov Quilt Mechanism (Def 4.1 max-influence
#: e_Θ(X_Q|Xi), Def 4.2 quilt (X_N,X_Q,X_R) with X_R⊥Xi|X_Q under Θ,
#: Algorithm 2, Theorem 4.3): search S_{Q,i} picking quilt minimizing
#: |X_Q|/(ε−e_Θ), composition via active quilt (Def 4.5, Theorem 4.4) when
#: stable; efficient chain variant Lemma 4.6 O(T²), Lemma 4.9 ═4a*,
#: Algorithm 4 MQMApprox, Theorem 4.10; Θ-misspecification bound Theorem
#: 2.4. Map: DENSE_TAIL_BYTES (~2500 rows, ~3.5 h at 5 s) is that
#: time-series shape; QI_DEFAULT_KEYS=("light_class",)/k=2 and exact
#: T[QI]/qi_sensitive_histogram are not such mechanisms — group DP would
#: be O(T) noise while Wasserstein/MQM calibrate to W / quilt size under
#: declared Θ. This plant declares no S/Q/Θ (L_DIVERSITY_SENSITIVE_KEY=None,
#: no Spairs, no D) so neither mechanism is instantiated; quoting raw tail
#: rows or exact histograms in wall outcomes remains a correlated-row
#: release absent a Pufferfish triple or stats_boundary.py Def 2.4
#: ε/δ/composition disclosure. Distinct from t-closeness EMD: that
#: compares per-class vs whole-table distributions with uncalibrated t;
#: Wasserstein/MQM compare output distributions under two secrets via W∞ /
#: max-influence and calibrate Laplace noise — replaces §8 threshold
#: ambiguity with explicit Θ-misspecification/utility conditions. Keeps
#: Narayanan sparsity + k-anonymity metric + Pufferfish absence + t-closeness
#: §8 notes as complements; revertible by git revert, no behaviour change.
# --- RAPPOR local longitudinal vocabulary (Erlingsson et al., CCS '14) ---
#: Bloom geometry Figure 1 p.5: B of size k with h hashes; collision alone is
#: not DP (Theorem 1 note p.9). k=256, h=4 are the paper's example defaults;
#: ε∞ is not a function of k (p.9). f is longitudinal privacy parameter
#: (§2 p.5 step 2: 0.5f random 1, 0.5f random 0, 1-f true). p,q are
#: instantaneous parameters (§2 p.6 step 3: P(S=1|B'=1)=q, P(S=1|B'=0)=p).
#: m=64 cohorts (§4 p.11) shard h hash families to reduce false positives.
#: All constants are typed calibration only; no Bloom construction, no hash,
#: no sampling here. Memoization of B' per stable value v ("Subsequently,
#: this B' is memoized and reused …" p.5) is required to avoid averaging to
#: truth (p.6); correlated drift when v changes leaks despite memoization
#: (p.3–4). Client-local (no trusted third party, p.2) vs curator DP in
#: stats_boundary.py (§2.1). Chrome deployment p.3 existence proof.
RAPPOR_K_DEFAULT: Final[int] = 256
RAPPOR_H_DEFAULT: Final[int] = 4
RAPPOR_F_DEFAULT: Final[float] = 0.5
RAPPOR_P_DEFAULT: Final[float] = 0.5
RAPPOR_Q_DEFAULT: Final[float] = 0.75
RAPPOR_M_COHORTS_DEFAULT: Final[int] = 64
# --- k-anonymity disclosure metric (Samarati & Sweeney 1998) ---
#: Quasi-identifier QI: set of attributes whose release must be controlled
#: (Def. 2.1 p.3); k-anonymity Def. 2.3 p.4: T satisfies k iff each sequence in
#: T[QI] appears >=k times; Assumption 2.1 p.4 correctness of QI is a policy
#: choice handled by contract, not by the metric; MaxSup Def. 4.3 p.11 is the
#: suppression budget; distance vector Def. 3.2 p.7 and minimal generalisation
#: Def. 3.3 p.8-9 not implemented here — this module stays exact. Precision =
#: 1 - level/height and completeness = 1 - suppressed/|PT| (§7 pp.15-17).
#: Critique — Narayanan & Shmatikov arXiv:cs/0610105v2 (2007) §§2–5:
#: k-anonymity "completely fails on high-dimensional datasets" [2] cited §2 p.2
#: (Aggarwal [2]); adversary Aux is "an arbitrary probabilistic function" over
#: any subset of (possibly imprecise) attribute values, not a fixed a priori QI
#: set (Abstract, §2 p.2, §3 pp.3–4) — if the adversary knows a value it becomes
#: "an identifier (but only for this individual's record)" (p.4). QI_DEFAULT_KEYS
#: is therefore a policy/display choice, not an adversary bound. Metric here is
#: measurement only at exact DV=[0,…,0]; k_anonymity_is_k_anonymous(...) is
#: True does not imply safe to broadcast. Any histogram/sequence release derived
#: from DENSE_TAIL_BYTES that leaves the consented recipients
#: {senses, health, body-research, inference-research} (CI _CI_FLOWS
#: "human-consented-personal-analysis") must be reviewed as a DP release under
#: src/mishe_tauftauf/stats_boundary.py (Dwork & Roth Def. 2.4 epsilon/delta,
#: Thm 3.6 Laplace scale, Thm 3.16 composition, Thm 2.2 group privacy) with
#: explicit epsilon/delta/composition, or as a revisited per-type consent
#: receipt — not as "k>=2 therefore safe." Perturbation and sample publication
#: do not mitigate (1A/1B robustness p.4, p.6 wt/Score + eccentricity, Thm 4–5).
QI_DEFAULT_KEYS: Final[tuple[str, ...]] = ("light_class",)
K_ANONYMITY_DEFAULT_K: Final[int] = 2
K_ANONYMITY_MAX_SUP: Final[int | None] = None
#: Prosecutor/journalist risk and k-map — El Emam & Dankar,
#: "Protecting Privacy Using k-Anonymity," JAMIA 15(5):627–637, 2008,
#: DOI 10.1197/jamia.M2716, PMCID PMC2528029 via
#: https://pmc.ncbi.nlm.nih.gov/articles/PMC2528029/ §§Background/Proposed
#: Improvements/Results (2026-11-03 metric REVISIT, complements
#: Narayanan sparsity + Pufferfish S/Spairs/D absence + Wasserstein/MQM
#: mechanism). Two re-identification scenarios §Background pp.628–630:
#: prosecutor (specific VIP known in anonymised file F', risk 1/f_j where f_j
#: is VIP class size in F', worst case 1/k when min f_j≥k) vs journalist
#: (arbitrary individual, intruder holds identification database Z of
#: population U, risk 1/F_j where F_j is population class size in Z, smart
#: intruder actual 1/min_j F_j). k-map property: each record in F' has ≥k
#: matches in Z' (k-anonymised Z); Fig.2 attains threshold with less loss than
#: baseline k-anonymity Fig.1 (2-anonymised F' min f_j=2 yet min F_j=4 so
#: actual 0.25 vs threshold 0.5, 3 rows suppressed, 10-year bins vs Z' 5-year
#: bins, zero suppression). Baseline D1 (f_j≥k) over-anonymises at small
#: sampling fractions n/N (gap most pronounced there, Abstract/Fig.3;
#: disappears as n/N→1); D2 (direct 1/F_j estimator), D3 (Poisson hypothesis
#: test, k' s.t. f_j≥k'⇒F_j≥k at α), D4 (truncated-at-zero Poisson, f_j≠0)
#: approximate k-map from sample with D4 staying ≤threshold with lower
#: discernability C_DM and suppression (Figs.4–5). C_DM (Bayardo &
#: Agrawal 2005, cost of global-optimization k-anonymiser): unsuppressed
#: tuple penalised by its class size, suppressed by |D|. Map:
#: DENSE_TAIL_BYTES (~2500 rows, ~3.5 h at 5 s) is a sample whose n/N vs its
#: notional population (all windows the phone could produce) is undefined;
#: current helpers qi_histogram/k_anonymity_k/is_k_anonymous/suppressed_count
#: /completeness/distinct_tuples report sample f_j and prosecutor 1/k only —
#: no Z, no F_j, no n/N, no C_DM, no k'/D4. Hence
#: k_anonymity_is_k_anonymous(...,k=2) true on ("light_class",) reports
#: min f_j≥2 (prosecutor ≤0.5) but not journalist actual 1/min F_j, and
#: quoting raw tail rows or exact T[QI]/qi_sensitive_histogram slices in wall
#: outcomes is a Z-aware disclosure whose F_j is unmeasured. Premise for
#: either metric/mechanism — declared Z/N/QI set for this metric, declared
#: S/Q/Θ for Wasserstein/MQM — remains absent (L_DIVERSITY_SENSITIVE_KEY=None,
#: no Spairs, no D/Θ), so neither is instantiated. Keeps Narayanan +
#: k-anonymity disclosure-metric + Pufferfish absence + t-closeness §8 +
#: Wasserstein/MQM notes as complements; revertible by git revert, no
#: behaviour change.
#: l-diversity sensitive-diversity vocabulary (Machanavajjhala et al., ICDE 2006 / TKDD 2007)
#: Principle 2 p7: q*-block is ℓ-diverse if at least ℓ well-represented values for sensitive S.
#: Table is ℓ-diverse if every q*-block is. Entropy Definition 4.1 p7: entropy(q*) = -Σ p log p ≥ log ℓ.
#: Recursive Definition 4.2 p8: r1 ≥ r2 … sorted, r1 < c·(rℓ+…+rm). 1-diversity always holds.
#: Sensitive vs QI partition §1-2: QI nonsensitive linkable vs sensitive must stay secret.
#: Placeholder L_DIVERSITY_SENSITIVE_KEY documents the sensitive attribute is a policy/content
#: choice reviewed under docs/protocol-boundaries.md, not inferred. No DGH/VGH here.
#: Syntactic not semantic: distinct but close values still pass; t-closeness EMD not implemented.
#: Multi-attribute Definition 4.5 p8 requires Q∪{other Si} as QI — cost grows.
L_DIVERSITY_DEFAULT_L: Final[int] = 2
L_DIVERSITY_DEFAULT_C: Final[float] = 2.0
L_DIVERSITY_SENSITIVE_KEY: Final[str | None] = None


def qi_histogram(
    rows: list[dict], qi_keys: tuple[str, ...] | list[str]
) -> dict[tuple, int]:
    """Projection histogram T[QI] -> count (Samarati & Sweeney Def. 2.3 p.4).

    Projects each row onto the quasi-identifier ``qi_keys``; missing QI fields
    become ``None`` so counting is strict per Def. 2.3. Empty input returns
    empty dict — the UNKNOWN-style denier is ``None`` in the ``k`` wrapper.
    No Path/clock/json I/O; caller supplies already-held rows.
    """
    try:
        keys = tuple(qi_keys)  # type: ignore[arg-type]
    except Exception:
        return {}
    if not keys:
        return {}
    hist: dict[tuple, int] = {}
    try:
        iterable = list(rows)  # type: ignore[arg-type]
    except Exception:
        return {}
    for row in iterable:
        if not isinstance(row, dict):
            continue
        key = tuple(row.get(k) for k in keys)
        hist[key] = hist.get(key, 0) + 1
    return hist


def k_anonymity_k(
    rows: list[dict], qi_keys: tuple[str, ...] | list[str]
) -> int | None:
    """k = min |QI class| over T[QI] (Def. 2.3 p.4, Thm 6.3 p.15 |T|>=k).

    Returns ``min(hist.values())`` when hist nonempty else ``None``.
    Assumption 2.1 p.4 fn.1: correctness of the chosen QI is a modelling
    assumption — if the QI omits a linkable attribute the measured k
    understates risk; see [18] §2.2 p.4. No generalisation/suppression here.

    Boundary: this k is over ``qi_keys`` only (default ``QI_DEFAULT_KEYS``,
    ``light_class``). k >= 2 on ``light_class`` does not bound the
    re-identification risk of a released time-series segment. Lange, Schreieder,
    Christen and Rahm, "Slice it up: Unmasking User Identities in Smartwatch
    Health Data" (ACM ASIA CCS 2025, DOI 10.1145/3708821.3710834; preprint
    arXiv 2308.08310), re-identify smartwatch users from short raw segments.
    They state that de-identification alone is insufficient, that k-anonymity
    is often unsuitable for time series, and they do not evaluate it; they
    propose differential privacy as the stronger defence. A release of raw body
    sequences needs a separate check or differential privacy, not this value.
    Revisit this note against the paper.

    Critique — Narayanan & Shmatikov arXiv:cs/0610105v2 (2007) §§2–5 Thm 1–3:
    on sparse micro-data 5–10 auxiliary attributes suffice for
    (1,1-epsilon)-deanonymization; QI_DEFAULT_KEYS is not an adversary bound
    (Aux is any subset of possibly imprecise attribute values, p.4). Any
    linkable column (e.g., ble_scan peer, wifi rssi sequence) becomes an
    identifier for that record. This k is measurement at DV=[0,…,0] only and
    does not bound that linkage. Robust to perturbation/sample (Algo 1A Score
    = min Sim, 1B wt=1/log|supp| + eccentricity, Thm 4–5).

    Blind spot, homogeneity and background knowledge: k counts rows per QI
    class and never reads the sensitive values inside a class. A class of
    size k whose rows all share one sensitive value passes k-anonymity and
    still discloses that value to anyone who knows the QI: the homogeneity
    attack. Background knowledge about a person's QI can single out a class
    the same way (Machanavajjhala et al., l-diversity, ICDE 2006). No
    sensitive attribute is declared here, and no l-diversity check exists.
    Pinned by test_k_anonymity_passes_a_homogeneous_class_on_light_class.
    """
    hist = qi_histogram(rows, qi_keys)
    if not hist:
        return None
    return min(hist.values())


def k_anonymity_is_k_anonymous(
    rows: list[dict], qi_keys: tuple[str, ...] | list[str], k: int
) -> bool | None:
    """Whether T satisfies k-anonymity for the given QI and k (Def. 2.3 p.4).

    Returns ``None`` on empty table (no histogram) or bad ``k`` (non-int or
    ``k<1``); otherwise ``True`` iff ``min(hist.values()) >= k``.
    Sufficiency per Def. 2.3: when Assumption 2.1 holds and each identity
    contributes at most one tuple. Measurement only — does not imply safe to
    broadcast sparse high-dimensional micro-data (Narayanan & Shmatikov
    arXiv:cs/0610105v2 §§2–5: QI is a policy choice not an adversary bound;
    5–10 auxiliary attributes suffice on DENSE_TAIL_BYTES; robust to
    perturbation and to sample D^, Thm 4–5; any release leaving
    {senses,health,body-research,inference-research} needs DP/consent review
    under stats_boundary.py, not k>=2).
    """
    try:
        kk = int(k)  # type: ignore[arg-type]
    except Exception:
        return None
    if kk < 1:
        return None
    hist = qi_histogram(rows, qi_keys)
    if not hist:
        return None
    return min(hist.values()) >= kk


def k_anonymity_suppressed_count(
    rows: list[dict], qi_keys: tuple[str, ...] | list[str], k: int
) -> int | None:
    """Rows that would be suppressed at DV=[0,...,0] (Defs. 4.1-4.2 p.10).

    ``Σ_{c: |c|<k} |c|`` — minimal-required-suppression count at no
    generalisation (distance vector all-zero). Returns ``None`` on bad ``k``.
    ``MaxSup`` Def. 4.3 p.11 is the budget against which this count is
    compared; this helper does not enforce it.
    """
    try:
        kk = int(k)  # type: ignore[arg-type]
    except Exception:
        return None
    if kk < 1:
        return None
    hist = qi_histogram(rows, qi_keys)
    suppressed = 0
    for count in hist.values():
        if count < kk:
            suppressed += count
    return suppressed


def k_anonymity_completeness(
    rows: list[dict], qi_keys: tuple[str, ...] | list[str], k: int
) -> float | None:
    """Completeness = 1 - suppressed/|rows| (§7 pp.15-17, Fig. 11).

    Returns ``None`` when rows empty or ``k`` invalid. Compare with
    precision = 1 - level/height when DGH levels are introduced (§7).
    Larger k trades precision for privacy; at MaxSup=10% completeness ≥0.90
    on the paper's 265-patient table (Fig. 11 p.17).
    """
    try:
        iterable = list(rows)  # type: ignore[arg-type]
    except Exception:
        return None
    if not iterable:
        return None
    try:
        kk = int(k)  # type: ignore[arg-type]
    except Exception:
        return None
    if kk < 1:
        return None
    suppressed = k_anonymity_suppressed_count(iterable, qi_keys, kk)
    if suppressed is None:
        return None
    total = len(iterable)
    if total == 0:
        return None
    return 1.0 - suppressed / total


def k_anonymity_distinct_tuples(
    rows: list[dict], qi_keys: tuple[str, ...] | list[str]
) -> int:
    """|hist| — distinct QI tuples in T[QI] (§5 p.12 maximum distribution).

    Preference cost for incomparable minimal generalisations; larger
    distinct count means more disclosure granularity retained.
    """
    hist = qi_histogram(rows, qi_keys)
    return len(hist)


#: l-diversity helpers are pure and exact; they do not generalise, suppress, or estimate f.
#: k-anonymous table can still be ℓ=1 (Figure 2 homogeneous block p2). Entropy ≥ log ℓ does not
#: imply protection against t-closeness/EMD skew or semantic similarity. No instance-level
#: background knowledge ("Bob does not have diabetes" p6). Multi-attribute check not in this commit.
def qi_sensitive_histogram(
    rows: list[dict], qi_keys: tuple[str, ...] | list[str], sensitive_key: str
) -> dict[tuple, dict[object, int]]:
    """Per-class sensitive histogram n(q*,s) per QI projection (Machanavajjhala Def 4.1-4.2 p7-8).

    Projects each row onto ``qi_keys`` for the key and counts ``sensitive_key`` values
    per class. Missing sensitive field becomes distinct ``None`` bucket so counting is
    strict. Empty input returns empty dict \u2014 UNKNOWN-style denier is ``None`` in the
    \u2113 wrappers. No Path/clock/json I/O; caller supplies already-held rows.
    """
    try:
        keys = tuple(qi_keys)  # type: ignore[arg-type]
    except Exception:
        return {}
    if not keys:
        return {}
    try:
        skey = str(sensitive_key)  # type: ignore[arg-type]
    except Exception:
        return {}
    if not isinstance(sensitive_key, str) or not skey:
        return {}
    try:
        iterable = list(rows)  # type: ignore[arg-type]
    except Exception:
        return {}
    hist: dict[tuple, dict[object, int]] = {}
    for row in iterable:
        if not isinstance(row, dict):
            continue
        key = tuple(row.get(k) for k in keys)
        # strict: row.get(skey) is None when missing \u2014 distinct bucket
        try:
            sval = row.get(skey)
        except Exception:
            sval = None
        inner = hist.get(key)
        if inner is None:
            inner = {}
            hist[key] = inner
        inner[sval] = inner.get(sval, 0) + 1
    return hist


def entropy_of_q_star(counts: dict[object, int]) -> float:
    """Shannon entropy -\u03a3 p log p for one q*-block (Machanavajjhala Definition 4.1 p7).

    Let p(q*,s)=n(q*,s)/\u03a3 n(q*,s'). Returns 0.0 on empty or total 0. Natural log,
    same as paper p7 Figure 3 note "actually 2.8-diverse". No I/O.
    """
    try:
        if not isinstance(counts, dict) or not counts:
            return 0.0
        total = 0
        for v in counts.values():
            try:
                total += int(v)  # type: ignore[arg-type]
            except Exception:
                return 0.0
        if total <= 0:
            return 0.0
        entropy = 0.0
        for v in counts.values():
            try:
                c = int(v)  # type: ignore[arg-type]
            except Exception:
                return 0.0
            if c <= 0:
                continue
            p = c / total
            if p <= 0.0 or p > 1.0:
                continue
            try:
                entropy -= p * math.log(p)
            except Exception:
                return 0.0
        if not math.isfinite(entropy):
            return 0.0
        return float(entropy)
    except Exception:
        return 0.0


def is_entropy_l_diverse(
    rows: list[dict], qi_keys: tuple[str, ...] | list[str], sensitive_key: str, ell: int
) -> bool | None:
    """Whether every q*-block satisfies entropy \u2265 log \u2113 (Machanavajjhala Definition 4.1 p7).

    Returns ``None`` on invalid ``ell<1``, non-finite, empty input, or missing
    ``sensitive_key`` when rows nonempty \u2014 typed deny analogous to k helpers.
    Whole-table entropy \u2265 log \u2113 is then necessary (concavity note p7).
    Conservative when domain(S) skewed; syntactic not semantic.
    """
    try:
        kk = int(ell)  # type: ignore[arg-type]
    except Exception:
        return None
    if kk < 1:
        return None
    try:
        hist = qi_sensitive_histogram(rows, qi_keys, sensitive_key)  # type: ignore[arg-type]
    except Exception:
        return None
    if not hist:
        return None
    try:
        threshold = math.log(kk)
    except Exception:
        return None
    if not math.isfinite(threshold):
        return None
    for block_counts in hist.values():
        ent = entropy_of_q_star(block_counts)
        if ent + 1e-12 < threshold:  # tiny epsilon for float
            return False
    return True


def is_recursive_c_l_diverse(
    rows: list[dict], qi_keys: tuple[str, ...] | list[str], sensitive_key: str, ell: int, c: float
) -> bool | None:
    """Whether every q*-block satisfies r1 < c\u00b7(rℓ+…+rm) (Machanavajjhala Definition 4.2 p8).

    Sorted descending r1\u2265r2\u2026rm. 1-diversity always True p8. Returns ``None`` on
    invalid ``ell<1``, non-finite ``c``, empty input etc. Less conservative than entropy.
    Constant ``c`` bounds r1 against tail \u2014 tuning knob \u00a75.
    """
    try:
        kk = int(ell)  # type: ignore[arg-type]
        cc = float(c)  # type: ignore[arg-type]
    except Exception:
        return None
    if kk < 1:
        return None
    if not math.isfinite(cc) or cc <= 0.0:
        return None
    try:
        hist = qi_sensitive_histogram(rows, qi_keys, sensitive_key)  # type: ignore[arg-type]
    except Exception:
        return None
    if not hist:
        return None
    if kk == 1:
        return True
    for block_counts in hist.values():
        try:
            sorted_counts = sorted(block_counts.values(), reverse=True)  # type: ignore[arg-type]
        except Exception:
            return None
        m = len(sorted_counts)
        if m < kk:
            return False
        # validate ints
        try:
            r1 = int(sorted_counts[0])  # type: ignore[arg-type]
            tail = sum(int(x) for x in sorted_counts[kk - 1 :])  # type: ignore[arg-type]
        except Exception:
            return None
        if not (r1 < cc * tail - 1e-12):  # need strict < ; tiny epsilon
            # also handle floating c
            if not (r1 < cc * tail):
                return False
    return True


#: RAPPOR helpers are pure and exact; they do not sample, hash, or store B'.
#: Memoization contract §2 p.5: B' memoized per stable value v avoids averaging
#: to truth (p.6). Correlated drift when v changes leaks despite memoization
#: (p.3–4). Bloom collision alone is not DP (Theorem 1 note p.9). Client-local
#: (no trusted third party, p.2) vs curator DP stats_boundary.py (§2.1).
#: Cohorts m=64 and decoding (§4) require population >> k; one Redmi not aggregatable.
def rappor_permanent_epsilon(*, f: float, h: int) -> float | None:
    """Longitudinal ε∞ for the permanent randomized response (Erlingsson et al. Theorem 1 p.8).

    ε∞ = h·ln((1-0.5·f)/(0.5·f)) when 0<f<1 and h>=1 else None. Returns None on
    invalid params — UNKNOWN-style deny. Correlated drift limit p.3–4 and
    Bloom-not-DP caveat p.9 apply; memoization §2 p.5 required.
    Example f=0.5, h=4 → ε∞≈4.39.
    """
    try:
        ff = float(f)  # type: ignore[arg-type]
        hh = int(h)  # type: ignore[arg-type]
    except Exception:
        return None
    if not math.isfinite(ff) or ff <= 0.0 or ff >= 1.0:
        return None
    if hh < 1:
        return None
    try:
        if int(h) != h:  # type: ignore[arg-type]
            return None
    except Exception:
        return None
    denom = 0.5 * ff
    numer = 1.0 - 0.5 * ff
    if denom <= 0.0 or numer <= 0.0:
        return None
    try:
        return float(hh) * math.log(numer / denom)
    except Exception:
        return None


def rappor_q_star(*, p: float, q: float, f: float) -> float | None:
    """Effective P(S=1|B=1) = 0.5·f + (1-f)·q (Lemma 1 p.9, simplified q*).

    Lemma 1 p.9 gives q* = 0.5·f·(p+q) + (1-f)·q and p* = 0.5·f·(p+q) + (1-f)·p;
    when p+q=1 this reduces to 0.5·f + (1-f)·q / 0.5·f + (1-f)·p, which covers
    the paper's recommended p=0.5, q=0.75, f=0.5 where q*=0.625, p*=0.5.
    Returns None when 0<=p<=1, 0<=q<=1, 0<f<1 otherwise.
    """
    try:
        pp = float(p)  # type: ignore[arg-type]
        qq = float(q)  # type: ignore[arg-type]
        ff = float(f)  # type: ignore[arg-type]
    except Exception:
        return None
    if not (math.isfinite(pp) and math.isfinite(qq) and math.isfinite(ff)):
        return None
    if not (0.0 <= pp <= 1.0 and 0.0 <= qq <= 1.0 and 0.0 < ff < 1.0):
        return None
    try:
        return 0.5 * ff + (1.0 - ff) * qq
    except Exception:
        return None


def rappor_p_star(*, p: float, q: float, f: float) -> float | None:
    """Effective P(S=1|B=0) = 0.5·f + (1-f)·p (Lemma 1 p.9, same note as q*).

    See rappor_q_star for validity and reduction. Returns None on invalid params.
    """
    try:
        pp = float(p)  # type: ignore[arg-type]
        qq = float(q)  # type: ignore[arg-type]
        ff = float(f)  # type: ignore[arg-type]
    except Exception:
        return None
    if not (math.isfinite(pp) and math.isfinite(qq) and math.isfinite(ff)):
        return None
    if not (0.0 <= pp <= 1.0 and 0.0 <= qq <= 1.0 and 0.0 < ff < 1.0):
        return None
    try:
        return 0.5 * ff + (1.0 - ff) * pp
    except Exception:
        return None


def rappor_epsilon_1(*, p: float, q: float, f: float, h: int) -> float | None:
    """Instantaneous ε1 for one report (Erlingsson et al. Theorem 2 p.9).

    ε1 = h·ln(q*·(1-p*) / (p*·(1-q*))) with q*,p* from Lemma 1. Returns None
    when any param invalid or denominator non-positive (UNKNOWN-style deny).
    Example p=0.5, q=0.75, f=0.5, h=4 → q*=0.625, p*=0.5 → ε1≈2.04.
    Correlated drift p.3–4 and Bloom-not-DP p.9 limits apply.
    """
    try:
        hh = int(h)  # type: ignore[arg-type]
    except Exception:
        return None
    if hh < 1:
        return None
    try:
        if int(h) != h:  # type: ignore[arg-type]
            return None
    except Exception:
        return None
    qs = rappor_q_star(p=p, q=q, f=f)
    ps = rappor_p_star(p=p, q=q, f=f)
    if qs is None or ps is None:
        return None
    if not (0.0 < ps < 1.0 and 0.0 < qs < 1.0 and ps != qs):
        return None
    try:
        ratio = (qs * (1.0 - ps)) / (ps * (1.0 - qs))
        if ratio <= 0.0 or not math.isfinite(ratio):
            return None
        return float(hh) * math.log(ratio)
    except Exception:
        return None


def is_valid_rappor_params(*, f: float, p: float, q: float, h: int, k: int) -> bool:
    """Whether (f,p,q,h,k) is a valid RAPPOR calibration (Erlingsson et al. §2–4).

    Requires 0<f<1, 0<=p<=1, 0<=q<=1, h>=1, k>=1, all finite. No sampling;
    Bloom size k does not affect ε∞/ε1 alone (Theorem 1 note p.9) but is
    required for geometry. Returns False on any invalid or non-finite input.
    """
    try:
        ff = float(f)  # type: ignore[arg-type]
        pp = float(p)  # type: ignore[arg-type]
        qq = float(q)  # type: ignore[arg-type]
        hh = int(h)  # type: ignore[arg-type]
        kk = int(k)  # type: ignore[arg-type]
    except Exception:
        return False
    if not (math.isfinite(ff) and math.isfinite(pp) and math.isfinite(qq)):
        return False
    if not (0.0 < ff < 1.0 and 0.0 <= pp <= 1.0 and 0.0 <= qq <= 1.0):
        return False
    try:
        if int(h) != h or int(k) != k:  # type: ignore[arg-type]
            return False
    except Exception:
        return False
    if hh < 1 or kk < 1:
        return False
    return True


def _read_tail(path: Path, limit: int) -> tuple[str | None, str | None]:
    """Read the last ``limit`` bytes of a file, or None with a reason."""
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - limit))
            raw = stream.read(limit)
    except FileNotFoundError:
        return None, "missing"
    except OSError:
        return None, "unreadable"
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError:
        return None, "malformed"


def _dense_unknown(reason: str) -> dict[str, object]:
    return {"id": DENSE_SENSE_ID, "kind": "read", "state": "unknown", "reason": reason,
            "sample": f"Redmi10 dense-baseline UNKNOWN: {reason}; source=linked Android Body consumer"}


def _dense_light_class(lux: float) -> str:
    if lux <= 0:
        return "dark"
    if lux < 50:
        return "dim"
    if lux < 1000:
        return "bright"
    return "very-bright"


def _dense_sequence_accounting(lines: list[str]) -> dict[str, int]:
    """Expected-vs-received accounting over the host sequence of a window.

    Follows RFC 3550 Appendix A.3: expected is the producer's sequence span
    over the window, received is the distinct sequence numbers seen, and the
    loss is their difference, reported as a count and never clamped. Repeated
    rows are named as duplicates rather than hidden. Rows whose first field is
    not an integer sequence are counted as unparsed and excluded. A sequence
    that steps back (a host restart or a reordered append) is counted too, so
    a span that spans a restart is not read as clean loss.
    """
    seqs: list[int] = []
    unparsed = 0
    steps_back = 0
    previous: int | None = None
    for line in lines:
        head = line.split(maxsplit=1)[0] if line.strip() else ""
        try:
            seq = int(head)
        except ValueError:
            unparsed += 1
            continue
        if previous is not None and seq <= previous:
            steps_back += 1
        previous = seq
        seqs.append(seq)
    if not seqs:
        return {"expected": 0, "received": 0, "lost": 0, "dup": 0,
                "unparsed": unparsed, "steps_back": steps_back}
    received = len(set(seqs))
    expected = max(seqs) - min(seqs) + 1
    return {"expected": expected, "received": received,
            "lost": expected - received, "dup": len(seqs) - received,
            "unparsed": unparsed, "steps_back": steps_back}


def read_redmi10_dense_baseline(home: Path, now: float | None = None) -> dict[str, object]:
    """Compact carried-device context from the consumer's dense observation queue.

    Reads the tail of the consumer's dense queue (append-only, ~5 s cadence) and
    returns a bounded summary: last light class, charging state, acceleration
    availability, and delivery break. The verdict is ``verified`` when the last
    row is fresh (< ``DENSE_FRESH_SECONDS``), ``stale`` when it is older, and
    ``unknown`` when the queue is unreadable. Deltas come from a fixed-cadence
    re-read (the discovery scan renewal), not from the scan corpus.

    The acceleration channel is published with its run structure as well as
    its coverage: the coverage percentage describes one window, and a reader
    who saw ``motion=0%`` could not tell a momentary zero from a channel
    that never delivered in that window. The run counts say whether values
    arrived in pieces at all, and the longest runs say how long the silence
    lasted when they did not. A run is a stretch of consecutive acceleration
    rows of one kind, timed from its first row to its last, so the numbers
    describe this window and inherit its placement.
    """
    current = time.time() if now is None else _finite_number(now)
    if current is None:
        return _dense_unknown("reader-clock-invalid")
    android_home, error = _linked_android_home(home)
    if error is not None or android_home is None:
        return _dense_unknown(error or "linked-android-body-home-unknown")
    queue = android_home / "body" / "queues" / DENSE_QUEUE_NAME
    text, error = _read_tail(queue, DENSE_TAIL_BYTES)
    if error is not None or text is None:
        return _dense_unknown(f"dense-queue-{error or 'unreadable'}")
    lines = text.splitlines()
    if not lines:
        return _dense_unknown("dense-queue-empty")
    last_light_lux: float | None = None
    last_light_epoch: int | None = None
    last_charging: bool | None = None
    last_battery_epoch: int | None = None
    accel_count = 0
    motion_count = 0
    total_count = 0
    first_epoch: int | None = None
    last_epoch: int | None = None
    # The run structure of the acceleration channel inside the window: a
    # coverage percentage says how much of one window carried values, and a
    # run says whether the channel was present in pieces or absent outright.
    value_runs = 0
    stale_runs = 0
    longest_value_run = 0
    longest_stale_run = 0
    run_class: str | None = None
    run_first_epoch: int | None = None
    run_last_epoch: int | None = None

    def _close_run() -> None:
        """Fold the run in progress into the run counts and the longest runs."""
        nonlocal value_runs, stale_runs, longest_value_run, longest_stale_run
        if run_class is None or run_first_epoch is None or run_last_epoch is None:
            return
        span = max(run_last_epoch - run_first_epoch, 0)
        if run_class == "value":
            value_runs += 1
            longest_value_run = max(longest_value_run, span)
        else:
            stale_runs += 1
            longest_stale_run = max(longest_stale_run, span)

    for line in lines[-DENSE_WINDOW_ROWS:]:
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            epoch = int(parts[1])
        except ValueError:
            continue
        kind = parts[2]
        total_count += 1
        if first_epoch is None:
            first_epoch = epoch
        last_epoch = epoch
        if kind == "light":
            for part in parts[3:]:
                if part.startswith("lux="):
                    try:
                        last_light_lux = float(part[4:])
                        last_light_epoch = epoch
                    except ValueError:
                        pass
                    break
        elif kind == "battery":
            for part in parts[3:]:
                if part.startswith("charging="):
                    val = part[9:]
                    if val in ("true", "false"):
                        last_charging = val == "true"
                        last_battery_epoch = epoch
                    break
        elif kind == "acceleration":
            accel_count += 1
            # An acceleration row that carries no axis values is a delivery
            # gap, not a motion observation: counting it as coverage made the
            # reported rate describe the poll cadence, not the motion
            # channel. Only a row carrying all three axes is an observation.
            carries_values = all(any(part.startswith(f"{axis}=") for part in parts[3:])
                                  for axis in ("x", "y", "z"))
            if carries_values:
                motion_count += 1
            row_class = "value" if carries_values else "stale"
            if row_class != run_class:
                _close_run()
                run_class = row_class
                run_first_epoch = epoch
            run_last_epoch = epoch
    _close_run()
    if total_count == 0 or last_epoch is None:
        return _dense_unknown("dense-queue-empty")
    seq = _dense_sequence_accounting(lines[-DENSE_WINDOW_ROWS:])
    break_seconds = current - last_epoch
    state = "verified" if break_seconds < DENSE_FRESH_SECONDS else "stale"
    window_seconds = (last_epoch - first_epoch) if first_epoch is not None else 0
    accel_ratio = accel_count / total_count if total_count > 0 else 0.0
    motion_ratio = motion_count / total_count if total_count > 0 else 0.0
    light_class = _dense_light_class(last_light_lux) if last_light_lux is not None else "unknown"
    light_text = f"{last_light_lux:g}lux {light_class}" if last_light_lux is not None else "unknown"
    charging_text = ("yes" if last_charging else "no") if last_charging is not None else "unknown"
    sample = (f"L={light_text} charging={charging_text} "
              f"motion={motion_ratio:.0%} ({motion_count}/{total_count} rows) "
              f"break={break_seconds:.0f}s "
              f"window={window_seconds}s rows={total_count} "
              f"runs=value:{value_runs}/stale:{stale_runs} "
              f"longest=value:{longest_value_run}s/stale:{longest_stale_run}s")
    details = (f"source=linked android-body body/queues/{DENSE_QUEUE_NAME}; "
               f"tail-bytes={DENSE_TAIL_BYTES} window-rows={total_count}; "
               f"last-light={light_text} at epoch {last_light_epoch}; "
               f"last-charging={charging_text} at epoch {last_battery_epoch}; "
               f"motion-coverage={motion_ratio:.1%} "
               f"(rows carrying x/y/z values); "
               f"delivery-coverage={accel_ratio:.1%} "
               f"(acceleration rows present, values or not); "
               f"break={break_seconds:.1f}s; "
               f"value-runs={value_runs} stale-runs={stale_runs} "
               f"longest-value-run={longest_value_run}s "
               f"longest-stale-run={longest_stale_run}s "
               "(a run is a stretch of consecutive acceleration rows of one "
               "kind inside the window, timed from its first row to its last); "
               f"seq-expected={seq['expected']} seq-received={seq['received']} "
               f"seq-lost={seq['lost']} seq-dup={seq['dup']} "
               f"seq-unparsed={seq['unparsed']} seq-steps-back={seq['steps_back']} "
               "(host sequence span over the window; loss only shows host-side "
               "after numbering, phone-side loss needs the app's spool-overflow "
               "record); "
               "phone/host clock split unknown; acceleration includes gravity "
               "and is not person-movement evidence.")
    return {"id": DENSE_SENSE_ID, "kind": "read", "state": state,
            "reason": "dense-queue-current" if state == "verified" else "dense-queue-stale",
            "sample": sample, "source": REDMI_SOURCE,
            "last_light_lux": last_light_lux, "last_light_class": light_class,
            "last_light_epoch_s": last_light_epoch,
            "charging": last_charging, "charging_epoch_s": last_battery_epoch,
            "accel_ratio": accel_ratio, "motion_ratio": motion_ratio,
            "motion_count": motion_count, "accel_count": accel_count,
            "break_seconds": break_seconds,
            "window_seconds": window_seconds, "row_count": total_count,
            "value_runs": value_runs, "stale_runs": stale_runs,
            "longest_value_run_s": longest_value_run,
            "longest_stale_run_s": longest_stale_run,
            "seq_expected": seq["expected"], "seq_received": seq["received"],
            "seq_lost": seq["lost"], "seq_dup": seq["dup"],
            "seq_unparsed": seq["unparsed"], "seq_steps_back": seq["steps_back"],
            "details": details}


PEER_SENSE_ID = "sense.body.redmi10.peer-delivery"
#: The tailnet peer's hostname as tailscale reports it. The phone reaches this
#: host over the tailnet (``tailscale serve`` proxies the tailnet :443 to the
#: local rendezvous), so the LAN address it also holds says nothing about
#: whether delivery works.
PEER_HOSTNAME = "Redmi 10"
#: How long a peer that is present but not advancing delivery keeps the
#: reading RED before it is called a stall. The tailnet's own ``Active`` flag
#: turned false at 09:43Z and the cursor did not move again until 13:26Z, so a
#: peer that is present but idle for longer than this is a transport stall, not
#: a momentary gap between two polls.
STALL_SECONDS = 300.0
#: The consumer's own record of when the delivery cursor last advanced. This
#: is the only field that says when observations last ARRIVED: the consumer
#: refreshes ``poll_at`` on every poll and on every error, so a poll age stays
#: near zero while the phone is silent.
CURSOR_ADVANCE_FIELD = "_class_b_last_cursor_advance_at"


def _peer_unknown(reason: str) -> dict[str, object]:
    return {"id": PEER_SENSE_ID, "kind": "read", "state": "unknown",
            "reason": reason,
            "sample": f"Redmi10 peer-delivery UNKNOWN: {reason}; "
                      f"source=tailnet peer state + linked Android Body consumer"}


def read_redmi10_peer_delivery(home: Path, now: float | None = None,
                                peer: dict | None = None) -> dict[str, object]:
    """Cross the tailnet peer's control-plane presence with the delivery cursor.

    Consumer: the personal multi-body programme, which must know whether a
    silent carried body is a phone that is not on the tailnet or one that is
    present but not sending. The two need different responses, and neither the
    consumer's own ``transport=reachable`` nor the peer's ``Online`` flag
    answers it: that verdict describes the host's path to its own rendezvous
    endpoint, and ``Online`` stayed true through a 4 h stall.

    The joint relation is the reading:

    - peer absent -> the phone is not on the tailnet.
    - peer present and the cursor advancing -> delivering.
    - peer present and the cursor frozen -> the phone has a control session
      but is not sending.

    What the peer flags do and do not mean. Tailscale defines ``Online`` as
    the node being connected to the control plane, and ``Active`` as a packet
    having been sent to that peer recently -- neither is proof that this host
    can reach the phone's data path. This sense therefore reports
    control-plane presence, not reachability, and names the two apart: a
    present-but-silent phone is "present, not sending", which leaves both a
    broken data path and a silent producer as possible causes. Reachability
    would need a probe of the peer's own address, which this reading does not
    do, so it does not claim it.

    The delivery age comes from the consumer's recorded cursor-advance time,
    not from its poll time: the consumer refreshes ``poll_at`` on every poll
    and on every error, so a poll age stays near zero while the phone is
    silent. Gating on that would name a healthy poll as a stall, and would
    hide the inactive case behind a stale-read verdict.

    ``peer`` is the decoded ``tailscale status --json`` mapping; when it is
    None the peer state is unknown, and a reading that cannot name the peer
    stays UNKNOWN rather than inferring presence from the cursor alone. The
    report's own health is read first: only a report whose ``BackendState``
    is ``Running`` and which carries this node's own ``Self`` entry supports
    a verdict at all. A backend that is stopped, or a report without ``Self``,
    leaves the peer map stale or partial, so a cached ``Online=true`` in it
    cannot assert that the phone is present now, and the reading stays
    UNKNOWN rather than claiming "present, not sending" from an unhealthy
    report.
    """
    current = time.time() if now is None else _finite_number(now)
    if current is None:
        return _peer_unknown("reader-clock-invalid")
    android_home, error = _linked_android_home(home)
    if error is not None or android_home is None:
        return _peer_unknown(error or "linked-android-body-home-unknown")
    path = android_home / "body" / "perception" / "redmi10.json"
    state, error = _read_json(path, MAX_STATE_BYTES)
    if error is not None or state is None:
        return _peer_unknown(f"consumer-state-{error or 'unavailable'}")
    cursor = state.get("cursor")
    advance_at = _finite_number(state.get(CURSOR_ADVANCE_FIELD))
    if type(cursor) is not int:
        return _peer_unknown("consumer-state-invalid")
    if advance_at is None:
        # The consumer records the advance time only once it has seen one; a
        # state without it cannot say how long delivery has been silent.
        return _peer_unknown("cursor-advance-time-absent")
    if advance_at > current + 5:
        return _peer_unknown("cursor-advance-time-future")
    if peer is None:
        return _peer_unknown("peer-state-unavailable")
    # The report's health is read before its content. A backend that is not
    # ``Running``, or a report without this node's own ``Self`` entry, is a
    # host-side UNKNOWN: the peer map of such a report is stale, partial or
    # not current, so a cached ``Online=true`` in it is not evidence that the
    # phone is present now. ``sense.net.tailnet-peer`` reads the same two
    # conditions the same way, and the composite that shares that report must
    # not be laxer than the reading it shares: without a healthy report the
    # verdict stays UNKNOWN instead of asserting "present, not sending".
    backend_state = peer.get("BackendState")
    if backend_state != "Running":
        return _peer_unknown(f"backend-state-{backend_state}")
    if peer.get("Self") is None:
        return _peer_unknown("self-missing")

    peers = peer.get("Peer")
    if not isinstance(peers, dict):
        return _peer_unknown("peer-state-unreadable")
    # The report keys peers by node key, so the name to match is the peer's
    # own HostName field, not the mapping key.
    entry = None
    for candidate in peers.values():
        if not isinstance(candidate, dict):
            continue
        name = candidate.get("HostName")
        if isinstance(name, str) and name.strip().lower() == PEER_HOSTNAME.lower():
            entry = candidate
            break
    if not isinstance(entry, dict):
        return _peer_unknown("peer-absent-from-tailnet")
    online = entry.get("Online")
    active = entry.get("Active")
    if type(online) is not bool:
        return _peer_unknown("peer-online-unreadable")

    delivery_age = current - advance_at
    consumer_error = state.get("error")
    if not online:
        verdict = "peer-absent"
        reason = "peer-offline"
    elif delivery_age <= STALL_SECONDS:
        # The cursor advanced recently, so the phone is sending whatever the
        # peer flags say. This branch is driven by delivery evidence, not by
        # tailnet activity: activity only means packets were sent toward the
        # peer, not that observations arrived.
        verdict = "delivering"
        reason = "cursor-advancing"
    else:
        # Present on the control plane but the cursor has not moved: the phone
        # is there and is not delivering. Whether its data path is usable is
        # not established from here, and the peer flags do not separate a
        # broken path from a silent producer, so both stay possible.
        verdict = "present-not-sending"
        reason = "cursor-frozen"

    sample = (f"Redmi10 peer={'online' if online else 'offline'}"
              f"/{'active' if active else 'idle'} cursor={cursor} "
              f"delivery_age={delivery_age:.0f}s verdict={verdict}")
    details = (f"source=tailnet peer '{PEER_HOSTNAME}' control-plane state + "
               f"linked android-body body/perception/redmi10.json "
               f"({CURSOR_ADVANCE_FIELD}); "
               f"delivery-path=tailnet->tailscale-serve:443->127.0.0.1:8765 "
               f"(the LAN address is not the delivery path); "
               f"peer_online={online} peer_active={active} cursor={cursor} "
               f"delivery_age={delivery_age:.1f}s consumer_error={consumer_error!r}; "
               "the consumer's own transport=reachable describes the host's own "
               "path to its rendezvous endpoint, not the phone's presence; "
               "peer Online/Active are control-plane presence and recent traffic, "
               "not proof that this host can reach the phone's data path, so "
               "'present-not-sending' leaves a broken path and a silent producer "
               "as separate possible causes; "
               "delivery_age is measured from the cursor advance, because the "
               "consumer's poll time refreshes on every poll and on every error.")
    return {"id": PEER_SENSE_ID, "kind": "read", "state": "verified",
            "reason": reason, "sample": sample, "source": REDMI_SOURCE,
            "verdict": verdict, "peer_online": online, "peer_active": active,
            "cursor": cursor, "delivery_age_seconds": delivery_age,
            "cursor_advance_epoch_s": advance_at,
            "consumer_error": consumer_error,
            "details": details}


#: The consumer's per-body channels that carry a body's own state. A body is
#: observable through these channels; the multi-body coverage reading asks
#: which of them are current for each body.
NODE_STATE_CHANNELS = ("battery", "light", "acceleration")
#: The linked site's local spool summary, one file per body, published by the
#: site's own reader. A body whose HTTP delivery is frozen by design can still
#: be read here, so the two lanes are reported apart.
SPOOL_SUMMARY_PREFIX = "spool-summary-"
SPOOL_SUMMARY_SUFFIX = ".json"


def read_node_consumer_state(home: Path, node: str,
                             now: float | None = None) -> dict[str, object]:
    """Decode one body's retained consumer state into plain values.

    The linked Android Body consumer keeps one state file per body. This
    reader names what that file can support: whether the consumer is still
    polling, and for each channel whether the last delivered row is fresh,
    with the phone-sample time and the consumer-receipt time kept apart. It
    never repairs a malformed state and never infers human movement: an
    absent, unreadable or invalid state is named, so a reader can tell
    "this body is not observable" from "the reader could not look".
    """
    current = time.time() if now is None else _finite_number(now)
    result: dict[str, object] = {"node": node, "lane": "delivery",
                                  "available": False, "channels": {}}
    if current is None:
        result["reason"] = "reader-clock-invalid"
        return result
    android_home, error = _linked_android_home(home)
    if error is not None or android_home is None:
        result["reason"] = error or "linked-android-body-home-unknown"
        return result
    path = android_home / "body" / "perception" / f"{node}.json"
    state, error = _read_json(path, MAX_STATE_BYTES)
    if error is not None or state is None:
        result["reason"] = f"consumer-state-{error or 'unavailable'}"
        return result
    cursor = state.get("cursor")
    session = state.get("session")
    latest = state.get("latest")
    source = state.get("source")
    if (type(state.get("version")) is not int or state.get("version") != 1
            or state.get("node") != node or not isinstance(source, str)
            or type(cursor) is not int or not 0 <= cursor <= MAX_SEQUENCE
            or not isinstance(session, str) or not session.strip()
            or not isinstance(latest, dict)):
        result["reason"] = "consumer-state-invalid"
        return result
    if type(state.get("has_more")) is not bool or state.get("has_more"):
        result["reason"] = "consumer-backlog-or-state-invalid"
        return result
    poll_at = _finite_number(state.get("poll_at"))
    if poll_at is None or current - poll_at < -5:
        result["reason"] = "consumer-poll-clock-unknown"
        return result
    if current - poll_at > FRESH_SECONDS:
        result["reason"] = "consumer-poll-stale"
        return result
    if state.get("error") is not None:
        result["reason"] = "consumer-poll-error"
        return result
    channels: dict[str, dict[str, object]] = {}
    for kind in NODE_STATE_CHANNELS:
        status, _fields, row = _row_status(latest.get(kind), kind, current)
        channels[kind] = {
            "status": status,
            "sequence": row.get("sequence") if row is not None else None,
            "phone_sample_epoch_s": row.get("collected") if row is not None else None,
            "consumer_receipt_epoch_s": row.get("received") if row is not None else None,
        }
    fresh = [entry["status"] for entry in channels.values()]
    result.update({
        "available": True,
        "reason": ("consumer-current" if all(status == "fresh-clock-conditional"
                                             for status in fresh)
                   else "channel-not-current"),
        "source": source,
        "session": session,
        "cursor": cursor,
        "poll_age_seconds": current - poll_at,
        "consumer_error": state.get("error"),
        "channels": channels,
    })
    return result


def read_node_spool_summary(home: Path, node: str,
                            now: float | None = None) -> dict[str, object]:
    """Decode the linked site's local spool summary for one body, if published.

    The site's own reader publishes a per-body spool summary for the bodies it
    reads over the local spool. That lane is independent of the HTTP delivery
    channel: a body whose HTTP delivery is frozen by design can still be
    observed here. A body with no published summary is reported as an absent
    lane, not as a failed read, because the summary is not published for every
    body.
    """
    current = time.time() if now is None else _finite_number(now)
    result: dict[str, object] = {"node": node, "lane": "spool", "available": False}
    if current is None:
        result["reason"] = "reader-clock-invalid"
        return result
    android_home, error = _linked_android_home(home)
    if error is not None or android_home is None:
        result["reason"] = error or "linked-android-body-home-unknown"
        return result
    path = (android_home / "body"
            / f"{SPOOL_SUMMARY_PREFIX}{node}{SPOOL_SUMMARY_SUFFIX}")
    state, error = _read_json(path, MAX_STATE_BYTES)
    if error is not None or state is None:
        result["reason"] = ("spool-summary-absent" if error == "missing"
                            else f"spool-summary-{error or 'unavailable'}")
        return result
    if state.get("node") != node or not isinstance(state.get("counts"), dict):
        result["reason"] = "spool-summary-invalid"
        return result
    newest = _finite_number(state.get("newest_row_epoch_s"))
    observed = _finite_number(state.get("observed_at_epoch_s"))
    oldest = _finite_number(state.get("oldest_row_epoch_s"))
    if newest is None:
        result["reason"] = "spool-summary-no-rows"
        return result
    if newest > current + 5:
        # A row from the future is not evidence about now, and an unbounded
        # future age would sit inside any freshness gate forever.
        result["reason"] = "spool-summary-clock-future"
        return result
    scan_state = state.get("scan_state")
    result.update({
        "available": True,
        "reason": "spool-summary-current",
        "newest_row_epoch_s": newest,
        "oldest_row_epoch_s": oldest,
        "observed_at_epoch_s": observed,
        "age_seconds": current - newest,
        "summary_age_seconds": (current - observed) if observed is not None else None,
        "method": state.get("method"),
        "scan_state": scan_state if isinstance(scan_state, dict) else None,
        "counts": state.get("counts"),
    })
    return result
