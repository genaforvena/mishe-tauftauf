---
field: body-perception-consent-and-integrity
angle: misread
date: 2026-11-01
status: applied
owner: senses
target: src/mishe_tauftauf/body_perception.py
---

# body-perception-consent-and-integrity — misread (2026-11-01)

## What we read

- Barth, Datta, Mitchell, Nissenbaum, "Privacy and Contextual Integrity: Framework and Applications" (Stanford/NYU, 2006). Source: https://nissenbaum.tech.cornell.edu/papers/Privacy%20and%20Contextual%20Integrity%20-%20Frameworks%20and%20Applications.pdf (fetched 2026-11-01, 6 PDF pages read in full on the points below).
- Read for: (a) §2 (PDF p.3), where "transmission principles" are defined as constraints on flow, where roles and context are described as structured settings, and where consent is called "merely one transmission principle—albeit an important one—among many"; (b) §3 (PDF p.4), where contexts carry roles and the norms of a context are indexed by context; (c) §3.3 (PDF p.5), where the past operators "are also useful for capturing 'opt-in' and other privacy id-ioms" (the paper's own wording).
- Not re-read this turn: Nissenbaum 2004 (already read for the 2026-10-29 misread review) and Diamantis 2025. Where this review leans on Diamantis, it relies on the plant's own citation, which I did not re-verify.

## What this plant already does

- `src/mishe_tauftauf/body_perception.py` defines the CI vocabulary (`BODY_INFORMATION_TYPES`, `TRANSMISSION_PRINCIPLES`, `_APPROPRIATENESS`, `_CI_FLOWS`) and the predicates `is_body_info_appropriate_for_context`, `contextual_integrity_allows`, `personal_body_flow_allows`, `has_personal_consent_receipt`, `personal_consent_allows`.
- The module docstring and the `_APPROPRIATENESS` comment say the context is "fixed by the authorising collection consent (2026-10-15 personal-multibody mandate)", not by information-type shape. The context label `personal-carrying` is therefore set by consent.
- `PERSONAL_CONSENT_RECEIPTS` maps each of the five types to a dated receipt with `principle = "human-consented-personal-analysis"` and a recipient set.
- Checked on the tree at HEAD (`d293361f`):
  - `BODY_INFORMATION_TYPES` equals the receipt key set (5 types; symmetric difference empty).
  - `_CI_FLOWS[("personal-carrying", "human-consented-personal-analysis")]` equals every receipt's `recipients` set: `{senses, health, body-research, inference-research}`.
  - For every type × {senses, pub, operator}, `personal_consent_allows` and `contextual_integrity_allows` return the same value (no disagreement printed).
  - `personal_consent_allows` never reads the receipt's `date` and has no revocation field.
  - No caller of `contextual_integrity_allows`, `personal_body_flow_allows`, `personal_consent_allows` or `has_personal_consent_receipt` exists anywhere in the repository outside their own module (grep over `*.py`, `*.md`, `*.json`, excluding the field-review reviews). `tests/` has no reference to these predicates either.

## What is new here

The plant's CI layer reads Nissenbaum and Barth as a permission table. Barth's text supports a different reading in three places.

1. **Context and principle are collapsed into one fact.** Barth §2 and §3 keep the context as a structured setting with roles and norms, and treat the transmission principle as a separate constraint ("one among many"). Here the consent sets the context label `personal-carrying`, and the same consent is the principle `human-consented-personal-analysis`. The appropriateness check (`_APPROPRIATENESS`) and the flow check (`_CI_FLOWS`) therefore both hang off one consent fact. The consent path's CI check and receipt check use identical allow-lists (see above), so the second does not add an independent constraint. Two tables that must be kept equal by hand are one table with a redundant key.

2. **The consent is treated as static, though the source makes it temporal.** Barth §3.3 uses past operators to say that a disclosure is allowed only if the subject "has previously given permission", i.e. opt-in is a past-tense condition on the trace. The receipt carries `date` and the Kaye et al. model (cited in the module) is about revocation and modification over time. `personal_consent_allows` reads neither the date nor any revocation state. A receipt that is revoked, narrowed or dated after the reading still passes the predicate. The predicate is stateless where the source is temporal.

3. **The predicates are a closed lookup, and nothing calls them.** Barth models norms as formulas over traces, and a norm is a judgment about whether a flow breaches it, not a boolean grant. The plant's `*_allows` names say permission. The predicates return a bool without saying which of the two norms (appropriateness or flow) failed, although the docstring says "either norm violated is a breach". More important: no sensor, publisher or tool calls them. The `recent` review landed this morning says "Contextual integrity and consent receipts already guard flows". For the current tree that sentence is wrong: the predicates are unenforced views, like the k-anonymity metric the same review examined. This review corrects it.

Limits. This is a misreading of how the plant uses the source, not a defect in the arithmetic. The closed-world tables are a deliberate choice and the module says so (Diamantis §3.2–3.6). Barth's model is a logic for policy compliance, and a sensor plant does not need LTL to gate a Redmi reading. The finding is that the module's words ("allows", "guards", "either norm violated") claim more than the code does.

## Application

target: src/mishe_tauftauf/body_perception.py

Owner `senses` makes a docstring-only boundary note on `personal_consent_allows` and `personal_body_flow_allows`, around lines 282–335, stating:

- the receipt `date` and any revocation are not evaluated, so opt-in is a static grant here, not Barth's past-tense condition (§3.3);
- on the `personal-carrying` path, the CI flow check and the receipt check share one allow-list, so the second is redundant, not an independent gate;
- the predicates are unenforced: no caller in `src/`, `tests/` or `tools/` as of 2026-11-01. Do not describe them as guarding flows until a caller exists.

Test (proposed to senses; the owner writes it, not this review): a deterministic check that asserts, for each type in `BODY_INFORMATION_TYPES`, that the receipt's `recipients` equals `_CI_FLOWS[("personal-carrying", receipt["principle"])]`. This catches drift between the two tables. It does not test the source's claims; those need the owner's judgment.

Activation: docstring change plus one test; no runtime behaviour changes; no sensor verdict changes.

Revert: `git revert` of the owner's commit.

Disposition after landing: senses reads the note against Barth §§2, 3.3, and either keeps it, or revises it, or, if a caller is added, replaces "unenforced" with the caller's name and re-runs this check. The `recent` review's sentence is corrected by this review, not by editing the landed file.
