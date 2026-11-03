---
field: body-perception-consent-and-integrity
angle: recent
date: 2026-11-03
status: applied
owner: senses
target: src/mishe_tauftauf/body_perception.py
---

# body-perception-consent-and-integrity — recent (2026-11-03)

## What we read

- Gilbert, Welzel, Ostermann, Smith, Minssen, Kirsten, Gilbert, "Enabling secure and self determined health data sharing and consent management", *npj Digital Medicine* 8:560 (2025), DOI 10.1038/s41746-025-01945-z. Open-access publisher PDF fetched 2026-11-03 as `/tmp/gilbert-2025.pdf` 1,003,820 B sha256 `609fb93ab4e771f203a14ab862deb04d4856986448bbbf1703f4aa17e1bddcd1` (10 pp., publisher PDF via https://www.nature.com/articles/s41746-025-01945-z.pdf). HTML landing: https://doi.org/10.1038/s41746-025-01945-z.
- Read for: the consent-model taxonomy and the stated trade-off of the model this plant uses. Read in full: Abstract (PG-HD from DHTs, blockchain/SSI/de-identified tokens), Introduction p.1–2 (DHT scope, GDPR Arts 6(1)(a)/9(2)(a) + Art 89, EHDS), Table 1 p.2 and its §2 text p.1–2, the tracking/technology §§ p.2–4 (Fig.1 smart-contract flow, Table 2–3), the willingness discussion p.2 (88% opt-in vs 12% opt-out, transparency about actors).
- Table 1 verbatim (p.2, four rows):
  - Broad consent — Timing: One-time, upfront; User control: Limited; Flexibility: Low; Challenges: Lacks granularity, limited reconsent.
  - Dynamic consent — Timing: Ongoing, case-by-case; User control: High; Flexibility: High; Challenges: Consent fatigue, Tech requirements.
  - Meta consent — Timing: User-defined (broad or dynamic); User control: Moderate to high; Flexibility: High; Challenges: Complexity in user preference management.
  - Standard Health Consent (SHC) — Timing: Platform-based, standardized process; User control: High; Flexibility: High; Challenges: Requires infrastructure and standardization.
- §2 text verbatim (p.2): "One model is broad consent, where individuals agree to future research on their data for up to 5 years and can revoke consent at any time, though critics argue it lacks true informed consent14,15. Another model, dynamic consent, allows individuals to provide or withdraw consent via a web or app platform, offering more patient control but potentially leading to 'consent fatigue' due to the need for frequent, detailed consent requests16–19. A third model, meta consent, combines broad and dynamic consent, allowing individuals to choose how they want to provide consent for past and future data use (Table 1)20." Refs 16–19 cited for fatigue. SHC defined p.2 as "would combine features of all three … and would give citizens control … through an easy-to-use and standardized consent process" (Table 1 ref 21).
- Not re-read this turn: Supplementary Table 1 (abbreviations), the blockchain/SSI implementation section beyond Fig.1/Table 2–3 summaries, the German EHDS instantiation (§ "To enable practical integration" p.3), and the reference list beyond confirming that Refs 16–19 are the fatigue citations. The perspective is an early-stage conceptual framework (Abstract: "novel early-stage conceptual framework", Hypothesis on p.2), not an empirical consent-rate trial.

## What this plant already does

- `src/mishe_tauftauf/body_perception.py:206–265` implements `PERSONAL_CONSENT_RECEIPTS` as dated per-type sticky receipts citing Kaye et al. 2015 EJHG Boxes 1–3 (p.143 "wrapped information … travels with data", one record, revisability over time, §2 comment "Per-type revisability without replacing broad consent (p.143 fatigue answer)"). Each of the five `BODY_INFORMATION_TYPES` maps to `{principle:"human-consented-personal-analysis", origin:"personal-multibody-20261015", date:"2026-10-15", recipients:{senses,health,body-research,inference-research}, scope_note}`. The `recent` review at 2026-11-01 for this field (Lange et al. slicing, ASIA CCS 2025) addressed time-series re-identification risk of the k metric, not consent taxonomy.
- The receipts are dynamic consent in Kaye's sense (per-type `date`, `origin`, revisable per type, travels as metadata). The module never names the taxonomy: Table 1's labels (broad / dynamic / meta / SHC) do not appear in the source. `CONSENT_MODEL` is absent (grep `CONSENT_MODEL` over `src/` returns nothing; only the comment fragment "without replacing broad consent" at line 214 mentions broad in prose). `personal_consent_allows` does not evaluate `date`/revocation (see `misread` review 2026-11-01 §30), consistent with an early-stage dynamic receipt that is typed but not yet wired to enforcement.
- The prior `misread` (Barth et al. 2006) and `critique`/`mechanism` reviews for this field leave the consent model unlabeled and the fatigue caveat undocumented.

## What is new here

Gilbert et al. Table 1 gives a crisp vocabulary that the plant's own sticky-receipt comment lacks: broad is one-time/upfront (up to 5y, low granularity), dynamic is ongoing case-by-case (high control/high flexibility, but fatigue + tech requirements), meta lets the user choose the granularity, SHC is a platform-standardized combination. The paper explicitly assigns "consent fatigue" to dynamic consent (p.2, Refs 16–19) and frames SHC as the standardized platform that would absorb that cost. Separately, the willingness paragraph (p.2, 88% opt-in vs 12% opt-out, transparency about actors drives trust) anchors why ongoing consent is not merely a UX preference.

For this plant, the finding is taxonomic, not behavioral: the existing `PERSONAL_CONSENT_RECEIPTS` are Kaye-style dynamic consent (ongoing, per-type, revisable), not broad (not one-time 5y), not meta (no user-chosen blanket-vs-case switch), and not SHC (no platform/infrastructure). Naming that in the docstring makes the fatigue trade-off reviewable. The current receipts mitigate broad-consent's "overly broad" weakness (per-type narrowness) at the cost of the paper's stated dynamic weakness (per-type change → per-type revocation requests → fatigue). The module does not currently handle fatigue, revocation UX, or SHC infrastructure, and this paper does not claim to solve them — it is a conceptual framework with "early-stage" in its abstract and hypothesis framing on p.2.

Limits: the paper is a perspective, not a measurement of fatigue rates or of the SHC's effectiveness; Table 1 is a typology, not a protocol; and the German SHC/EHDS instantiation is a proposal, not a standard this plant can import. Any threshold (how many per-type updates before fatigue) is explicitly out of scope here.

## Application

target: src/mishe_tauftauf/body_perception.py

Owner `senses` adds a docstring-only boundary note beside `PERSONAL_CONSENT_RECEIPTS` (around lines 206–265), stating:

- Consent-model taxonomy per Gilbert et al., *npj Digital Medicine* 8:560 (2025) Table 1: this plant's per-type dated receipts implement **dynamic consent** (Kaye et al. EJHG 2015 Boxes 1–3, as already cited) — ongoing, case-by-case, high control/high flexibility — not broad (one-time, up to 5y, limited reconsent), not meta (user-defined blanket-or-dynamic choice), not Standard Health Consent (platform-based standardized SHC).
- The dynamic model carries Gilbert's stated trade-off: more granularity at the cost of **consent fatigue** and tech requirements (Table 1 Challenges; §2 p.2 Refs 16–19). The current module mitigates broad-consent overbreadth via per-type narrowness (`BODY_INFORMATION_TYPES` × per-type receipt) but does not mitigate fatigue.
- No wiring, no revocation evaluation, no SHC infrastructure is added by this note; revocation remains unenforced per the `misread` review's finding on `personal_consent_allows`. Adding meta/SHC behaviour or fatigue handling would need a separate review.

Test: docstring-only change; no runtime behaviour changes. Verification as with prior recent for this field: `python -m py_compile src/mishe_tauftauf/body_perception.py` exits 0; `python3 research/field-review/field_review.py check` passes; grep confirms `PERSONAL_CONSENT_RECEIPTS` comment now contains "Gilbert" and "Table 1" and "consent fatigue" as vocabulary.

Activation: additive comment; reversible by `git revert`.

Disposition after landing: senses reads the note against Gilbert Table 1 + Kaye Boxes 1–3 and keeps, revises, or replaces it when a caller that evaluates `date`/revocation exists.
