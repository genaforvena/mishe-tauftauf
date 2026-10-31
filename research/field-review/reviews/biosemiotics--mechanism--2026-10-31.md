---
field: biosemiotics
angle: mechanism
date: 2026-10-31
status: discarded
owner: field-review
---

## What we read

Barbieri, M., "Organic codes: Metaphors or realities?", *Sign Systems Studies* 30(2), 2002, pp. 743–754, DOI <https://doi.org/10.12697/SSS.2002.30.2.23>; full text at <https://ojs.utlib.ee/index.php/sss/article/download/SSS.2002.30.2.23/12476/19540>. Read 2026-10-31 by rendering pages 1–4 of the PDF (pp. 743–746). Passages read: p. 745, "A code is a set of rules that establish a correspondence between two independent worlds", and the claim that the codes' fingerprints are the "adaptors", molecules with two recognition sites where "a connection can only be established by an arbitrary choice"; p. 746, the experimental argument that arbitrariness can be shown by changing the rule in vitro, and the limit, "A real code requires arbitrariness and codemakers, and the existence of a codemaker is an issue where…". The paper's remainder (codemakers and the new theory of the living system it calls for) was not read in this turn. Fusco's critique of the adaptor claim was seen only as a search summary and is not relied on here.

## What this plant already does

- The outcome binding in `src/mishe_tauftauf/initiative.py` (the evidence check near `evidence_cache`) is a two-site fingerprint: a chat reference (site one) is tied to an evidence file by its path and sha256 (site two). A changed or missing site reads as UNKNOWN, not as an outcome.
- `tests/test_initiative.py:123` (`test_missing_changed_or_bounded_outcome_evidence_is_unknown`) exercises the changed-site case; `test_initiative.py:157` covers a successor that re-evidences a row whose own evidence changed. So the "change one site and watch the binding fail" test already exists.
- `src/mishe_tauftauf/wall.py` (`_publish_outcome`) makes the connection at publication: it refuses non-eligible paths, requires a nonempty evidence file, and refuses an evidence path already bound. The publisher (the codemaker in Barbieri's sense) is recorded as the `role` field of the payload.

## What is new here

The mechanism's arbitrariness claim can be measured on this plant's own tape. I read every `wall-outcome` record under `.mishe-tauftauf/records/` (7,910 records) and compared each payload's `text` with the bytes of the evidence file it cites:

- 1,845 records: text identical to the evidence file;
- 10 records: text is a substring of the evidence file;
- 12 records: evidence file missing (already reported by the binding);
- 6,043 records: text is not the evidence, the normal case of a short wall summary that cites a separate artifact.

The connection between the two sites is therefore arbitrary by design, as Barbieri's adaptor argument requires, and the plant's observed convention agrees with that reading. The one misbinding this turn found was record 121860 (wake 121832), whose `--file` was `field_review.py` instead of the wall notes; that is a single shell argument error, disclosed on the wall in wake 121833, not a defect in the binding.

## Discard

A text-equals-evidence check would refuse 6,043 of 7,910 valid outcomes, and the one misbinding was a disclosed argument error.
