---
field: second-order-cybernetics
angle: recent
date: 2026-11-02
status: applied
owner: genome
---

## What we read

Primary source: **Zönnchen, Dzhimova & Socher, "From intelligence to autopoiesis: rethinking artificial intelligence through systems theory", *Frontiers in Communication* 10:1585321 (2025), doi:10.3389/fcomm.2025.1585321**, PDF fetched from <https://www.frontiersin.org/journals/communication/articles/10.3389/fcomm.2025.1585321/pdf> to `/tmp/soc-frontiers2025.pdf`, 268518 bytes, sha256 `14f959d0e66ac8023af797aec181c93458673b0e377effc499794bb5eb053189`, 12pp, Creator LaTeX/hyperref, Producer dvips+MiKTeX GPL Ghostscript 9.0, CreationDate 2025-05-15 08:35:27 UTC; text extracted with `pdftotext -layout`, ~900 lines retained; read pp.01-08 in full plus §3.5-3.6 scanned, references checked.

The paper is a recent (19 May 2025) systems-theory reading of LLMs through Luhmann. Its second-order cybernetics core is §§2.1-2.2: it imports Spencer-Brown (1969) unmarked space, Luhmann's "the observer is the excluded third of their observation" (1998 p.69) and the blind spot — "the distinction they use is the hidden condition … To see, one must be blind to the act of seeing" (p.02) — and the system/environment boundary as form (p.02). Contingency (Möller 2011 p.45, Luhmann 1984) and double contingency (p.03) precede the definition: "The most profound form of freedom appears in re-entry (Spencer-Brown, 1969), where a system reintroduces its own guiding distinction into itself" (p.03), and "The capacity for re-entry enables self-observation — a special case of what Heinz von Foerster and Margaret Mead called second-order cybernetics (von Foerster, 2003) and Luhmann second-order observation. It refers to a system's ability to observe how it and others observe reality" (p.03). Classical machines and ANNs are then contrasted: classical machines are strictly coupled via formal language and "lack second-order observation and the ability to reflect on their own distinctions — especially the difference between self-reference and other-reference" (p.03); ANNs add a loose coupling by parasitically absorbing social contingency via training data (p.05-06) and exhibit basal self-referentiality (revisiting prior output, chain-of-thought) but "cannot distinguish self-reference from other-reference via re-entry" and "do not know what they are doing" (Heßler 2019, p.06), so re-entry is the decisive test — "can they distinguish self-reference from other-reference within their own operations?" (p.07-08).

Limits: the paper is conceptual, not a detector spec; LLM re-entry examples (CoT) are explicitly "not intrinsic but must be explicitly reintroduced through input" (p.08) and remain speculative. I use only the Luhmann/von Foerster re-entry + blind-spot definitions, not the paper's LLM-autopoiesis thesis.

## What this plant already does

The sweep `python3 research/field-review/field_review.py next` for this cell reports `circular`, `observer`, `second-order` only in `fields.json`, unrelated research notes, `checks/check-scheduler-coverage.py` (the observer-check runner and its scheduler coverage), and the predecessor reviews — no Luhmann `re-entry` / `blind spot` / `excluded third` / `self-reference vs other-reference` vocabulary is embodied in src/docs outside reviews.

The plant already has observers watching observers. `checks/check-scheduler-coverage.py` is the detector that finds tracked checks no scheduler runs — "a verdict nobody reads". It declares `SELF_NAME = "check-scheduler-coverage.py"` (l.123) with the comment "Once the tree carries it, it is itself an unobserved check, and running it inside itself would be recursion" (ll.121-122), and lists the observation channels — local `INVENTORY`/`NOT_SCHEDULED` tables (ll.118-119), published `checks/observer-checks.json` (l.113), `systemd` units (l.135/264) and CI workflows (l.127/389). `checks/observer-verdict-agreement.py` and `checks/observer-view-agreement.py` already compare observer and published readings field-by-field and print scope on DISAGREE. The Pask-mechanism predecessor (2026-10-31 mechanism, target `observer-verdict-agreement.py`) already proposes disclosing agreement basis on AGREE. The predecessor recent review (2026-10-31 discarded, source Elshatlawy et al. arXiv:2504.16225 v3) read Foerster's observer-inclusion as survey without a new mechanism and discarded.

What is missing is the name for the self-exclusion design: the check's comment says "would be recursion" but does not name it as the second-order blind spot the field defines.

## What is new here

The new piece from this recent source is the vocabulary that makes self-exclusion observable as a second-order limit rather than an implementation quirk: Luhmann's excluded third / blind spot (p.02, 1998 p.69) and re-entry (Spencer-Brown 1969, p.03) as the operation that would let a system observe its own observing distinction (self-reference vs other-reference, p.03, p.07-08). The paper's decisive question — whether a system can re-enter its own system/environment distinction — transfers directly: a scheduler-coverage detector that reported on itself would need to re-enter its own distinction (scheduled vs not-scheduled) into the comparison, making the observer part of the outcome. Luhmann's point is that the observer cannot see the distinction it uses while using it — that is why `SELF_NAME` is excluded.

This is not a new detector and not a logic change. It is one paragraph of scope disclosure in the check's own docstring, parallel to the Pask disclosure on the verdict agreement check: naming the blind spot so a reader can see that "unobserved" does not include the detector's own unobserved status as a finding.

Dissent within the literature: the same source shows why re-entry is hard — even chain-of-thought self-reference in ANNs is "mirrors the inherent self-referentiality of natural language rather than an autonomous sense-making process" (p.08); for deterministic checks the analogue is exact — echoing prior output is not observing the distinction that produced it.

## Application

target: checks/check-scheduler-coverage.py

Proposal to genome (owner of `checks/`):

1. On `checks/check-scheduler-coverage.py` header docstring (ll.1-94), add one paragraph citing Zönnchen et al. 2025 §2.1-2.2 (Luhmann 1998 p.69 excluded third / p.63 system/environment form, Spencer-Brown 1969 re-entry, von Foerster 2003 second-order cybernetics) to name `SELF_NAME` exclusion as the Luhmannian blind spot: the detector cannot observe its own observer status while observing others, so self-exclusion is a second-order limit, not merely recursion avoidance. Note that a re-entry (the check observing its own scheduled-vs-not-scheduled distinction as part of the outcome) would make the observer part of `V_O` and is deliberately not done. Keep `SELF_NAME` value and all exit codes unchanged.

2. Keep docstring-only, no logic or threshold change. The line is derived from the paper's definitions at print time only as prose; no new field is added to `VERDICT_FIELDS`.

3. Detector: the check's own output and `--help` text on each scheduled run. Resolver: genome. Detection interval: scheduled observer-check / scheduler-coverage run. Reopen if genome finds the vocabulary duplicates the existing Pask disclosure in `observer-verdict-agreement.py` or belongs in `docs/mesh.md` instead of the check header, or if a later recent source revises the re-entry criterion.
