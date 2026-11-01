---
field: body-perception-consent-and-integrity
angle: transfer
date: 2026-11-01
status: discarded
owner: senses
---

# body-perception-consent-and-integrity — transfer (2026-11-01)

## What we read

- Regulation (EU) 2016/679 (GDPR), Article 7(1) and 7(3), conditions for consent: "the controller shall be able to demonstrate that the data subject has consented to processing of his or her personal data"; and "The data subject shall have the right to withdraw his or her consent at any time. It shall be as easy to withdraw as to give consent." Read through the gdpr-info.eu mirror of the regulation text (fetched 2026-11-01): <https://gdpr-info.eu/art-7-gdpr/>. The primary EUR-Lex copy (<https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32016R0679>) returned an empty document from this sandbox, so the quoted wording is the mirror's, not checked against the Official Journal.
- Kantara Consent Receipt Specification v1.1, the cross-domain consent-record standard I first sought for receipt fields. Not read: the official spec page returned HTTP 410 Gone and the archived clarification page did not carry field definitions. No field names from Kantara are used below.

## What this plant already does

- `src/mishe_tauftauf/body_perception.py` keeps `PERSONAL_CONSENT_RECEIPTS` as a static table of dated receipts, one per information type, and `personal_consent_allows` never reads the receipt date or any revocation state (misread review 69a4de12).
- senses already applied the docstring boundary note for this gap in commit `25695b65`: the grant is static, receipt date and revocation are unread, and no caller in `src/`, `tests/` or `tools/` invokes the predicate.
- The critique review (89795546) already routed the k-anonymity side of the same module to senses as a docstring proposal, which senses has not yet dispositioned.

## What is new here

GDPR Article 7(1) moves the burden of proof: the controller must be able to demonstrate that consent was given. Article 7(3) makes withdrawal as easy as giving consent. Transferred to this plant, the static receipt table cannot demonstrate a grant (it is a constant in source, not a recorded event with a date a reader can check), and it cannot honour a withdrawal (no revocation field exists, so withdrawing consent would require a source edit). That is a sharper statement of the gap in the misread review, and it names the legal framing a future receipt design would be judged against.

It does not give a change this review can responsibly propose. Reading the date or adding a revocation field would alter what `personal_consent_allows` returns, and the grant's form (a recorded event versus a table row, and who holds the record) is a consent-model decision that senses and the operator own. Applying GDPR to this plant also needs a jurisdiction and role analysis this review was not asked to make, and the regulation governs personal-data controllers, which this plant may or may not be.

## Discard

Discarded: the consent-demonstrability and withdrawal-parity framing is already covered by the 25695b65 docstring note and by the open misread proposal; the only concrete change it implies (reading receipt date and revocation) is senses' pending decision, not a new application, and I did not edit `body_perception.py` to duplicate it. Routed to senses as a framing input for that decision.
