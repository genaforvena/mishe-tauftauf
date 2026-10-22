"""The band vocabulary and the one computation that decides a row's band.

The publisher (``discovery.py``) and any reader that classifies coverage-gap
rows need the same answer to the same question: which band does this row
belong to, and what does that band claim? When the publisher computes the band
and the reader re-derives it, the two can disagree about a row that decides a
task, so the computation lives here once and both sides import it.

This module has no dependency on ``discovery``: it takes the facts of a row
(its link kind, its defect classes, whether the row was compliant under the
policy in force at binding time, and how its bytes stand now) and returns the
band's name. A reader can therefore classify a published row without importing
the producer, and the producer can publish the band without a second reader
re-deriving it from a link kind.
"""

from __future__ import annotations

#: The byte relations a rebound row can carry. The band decides a **link**
#: relation — a remedy-named successor exists — so the byte relation is
#: published beside it, per row, rather than folded into the band's name.
#: ``restored`` — the successor's bytes are the row's published bytes, so the
#: content came back. ``copy-of-current`` — the successor's bytes are the lost
#: path's current bytes, so the work was carried forward and the published
#: bytes did not come back. ``unrelated`` — the successor's bytes are neither.
#: ``unknown`` — the relation cannot be decided from the bytes at hand.
EVIDENCE_BYTE_RELATIONS = ("restored", "copy-of-current", "unrelated", "unknown")

#: The relation a link that is not a byte relation carries, so every linked
#: row publishes the same field with an honest value.
EVIDENCE_BYTE_RELATION_NA = "not-a-byte-relation"

#: The relation each band decides, published with the band table so a reader
#: does not have to infer what a band's name claims. A band is a relation on
#: rows; the byte relation of a row that carries a link is published per row
#: beside it.
EVIDENCE_BAND_RELATIONS = {
    "owed": "the work was lost and no later intact outcome re-evidenced it",
    "recoverable": "the bound bytes survive verbatim as a prefix of the current file",
    "disposed": "a later intact outcome declared the row's record digest in its "
                "structured supersedes field",
    "rebound": "a later intact outcome bound a remedy-named successor of the lost "
               "artifact; the byte relation of that successor is published per row",
    "cited": "a later intact outcome's artifact carries the row's record digest",
    "versioned": "the row was bound before the allowlist amendment and was "
                 "compliant under the rule then in force",
    "permanent": "no later rule can repair the defect",
}

#: Every band a violating row can fall into, with the count the publisher
#: starts from. The order is the order the bands are decided in, so a reader
#: that prints them reads them in the plant's own order.
EVIDENCE_BAND_NAMES = ("versioned", "permanent", "disposed", "rebound", "cited",
                       "recoverable", "owed")

#: The relation each discharge carries. A discharge is a link that took a row
#: out of the gate: a supersession, a remedy-named successor, or a digest
#: citation. A remedy-named successor and a supersession say something about
#: the row's published bytes -- the plant compares the successor's bytes
#: against the bytes the row published -- so both carry that byte relation. A
#: digest citation carries the relation that says so plainly: the link is not
#: a byte relation, so no claim is made about the bytes that were published.
#:
#: ``byte_relation`` says the relation is a claim about bytes at all, and
#: ``restores`` says the discharge returned the row's published bytes. Both
#: are published with the counts, so a reader re-derives the exposure from
#: published fields alone and does not need this module's vocabulary.
EVIDENCE_DISCHARGE_RELATIONS = {
    "restored": {
        "claims": "the successor's bytes are the bytes the outcome published, so "
                  "the published content came back",
        "byte_relation": True,
        "restores": True},
    "copy-of-current": {
        "claims": "the successor carries the file as it now stands, so the work "
                  "was carried forward and the published bytes did not come back",
        "byte_relation": True,
        "restores": False},
    "unrelated": {
        "claims": "the successor's bytes are neither the published bytes nor "
                  "the current ones",
        "byte_relation": True,
        "restores": False},
    "unknown": {
        "claims": "the relation cannot be decided from the bytes at hand",
        "byte_relation": True,
        "restores": False},
    "not-a-byte-relation": {
        "claims": "the discharge is a digest citation: it clears the row "
                  "without making any claim about the published bytes",
        "byte_relation": False,
        "restores": False},
}

#: The scopes the discharge exposure can be read over. The scope is the
#: population of discharges the counterfactual counts, and it is published as
#: data: a second reader checks the figure against the scope instead of
#: against prose, so a number and the definition that accompanies it cannot
#: be read two ways.
EVIDENCE_EXPOSURE_SCOPES = ("all_discharged_rows", "byte_relation_discharges")

#: The published field each scope's population is counted over. A scope names
#: its population field, so the figure is re-derivable from published fields
#: alone and the scope cannot be counted one way and read another.
EVIDENCE_EXPOSURE_SCOPE_POPULATION = {
    "all_discharged_rows": "discharged_rows",
    "byte_relation_discharges": "byte_relation_discharges",
}

#: How each scope's exposure is defined, published with the numbers so a
#: reader cannot mistake a counterfactual for the gate. Each definition names
#: the counterfactual it defines and the scope it is over, and names where
#: the supersessions and the citations are counted, so the definition that
#: accompanies a figure is the definition of that figure's scope.
EVIDENCE_EXPOSURE_DEFINITIONS = {
    "all_discharged_rows": (
        "the gate size if only a discharge that returned the row's published "
        "bytes counted as clearing the row, over every discharge that emptied "
        "the gate; a discharge that returned no published bytes returns its "
        "row to the gate, and a digest citation returns no published bytes at "
        "all; the remedy-named successors and the supersessions are counted "
        "in discharged_rows and in byte_relation_discharges, and the citations "
        "in discharged_rows only"),
    "byte_relation_discharges": (
        "the gate size if only a discharge that returned the row's published "
        "bytes counted as clearing the row, over the discharges that carry a "
        "byte relation at all; a digest citation carries no byte relation and "
        "is outside this scope, so it neither restores nor re-opens a row "
        "here; the remedy-named successors and the supersessions are counted "
        "in discharged_rows and in byte_relation_discharges, and the citations "
        "in discharged_rows only"),
}


def discharge_strength(relations: list[str]) -> dict[str, int]:
    """How many discharges carry each relation, over the plant's vocabulary.

    Every relation the plant defines is counted, so a reader sees a relation
    that never happened as a zero rather than as an absence.
    """
    counts = {name: 0 for name in EVIDENCE_DISCHARGE_RELATIONS}
    for relation in relations:
        counts[relation] = counts.get(relation, 0) + 1
    return counts


def discharge_exposure(*, gate_rows: int, discharged_rows: int,
                       restoring_discharges: int,
                       byte_relation_discharges: int,
                       scope: str = "all_discharged_rows") -> dict:
    """The gate size the discharges imply, over a named scope.

    The counterfactual the plant's own verdict does not state: if only a
    discharge that returned the row's published bytes counted as clearing the
    row, the gate would hold every row a discharge cleared without restoring
    those bytes.

    The scope says which discharges the counterfactual counts, and it is data
    rather than prose: every scope names the published field its population is
    counted over, so a second reader re-derives the figure from published
    fields alone and cannot read one scope's number against another scope's
    definition.

    ``all_discharged_rows`` counts every discharge that emptied the gate. A
    discharge that makes no byte claim is precisely a discharge that moved
    the certifier without repairing bytes, so it belongs in the exposure.
    ``byte_relation_discharges`` counts only the discharges that carry a byte
    relation at all, which is the strength of the byte-claiming repairs.

    A discharge whose relation could not be decided counts as non-restoring,
    so the exposure overstates the debt rather than hiding it.

    It is a measurement of the strength of the discharges, not a claim that
    any row is repairable -- the gate stays as it is, and the standing
    audit's ``permanent_loss`` counter is untouched by this.
    """
    if scope not in EVIDENCE_EXPOSURE_SCOPES:
        raise ValueError("unknown discharge exposure scope: " + str(scope))
    populations = {"discharged_rows": discharged_rows,
                   "byte_relation_discharges": byte_relation_discharges}
    scopes: dict[str, dict] = {}
    for name in EVIDENCE_EXPOSURE_SCOPES:
        population_field = EVIDENCE_EXPOSURE_SCOPE_POPULATION[name]
        population = populations[population_field]
        non_restoring = max(0, population - restoring_discharges)
        scopes[name] = {
            "population_field": population_field,
            "population": population,
            "non_restoring_discharges": non_restoring,
            "gate_if_only_restoring_counted": gate_rows + non_restoring,
            "definition": EVIDENCE_EXPOSURE_DEFINITIONS[name],
        }
    named = scopes[scope]
    return {
        "gate_rows": gate_rows,
        "discharged_rows": discharged_rows,
        "byte_relation_discharges": byte_relation_discharges,
        "restoring_discharges": restoring_discharges,
        "exposure_scope": scope,
        "non_restoring_discharges": named["non_restoring_discharges"],
        "gate_if_only_restoring_counted": named["gate_if_only_restoring_counted"],
        "exposure_scopes": scopes,
    }


def byte_relation(lost_published: object, lost_current: object,
                  successor_current: object) -> str:
    """How a successor's bytes relate to the lost row's bytes.

    The band says a remedy-named successor exists. That link says nothing
    about the bytes: the remedy-named copy may hold the artifact as it was
    published, or a copy of the file as it now stands after the edit that
    lost the row. The two are decidable from digests alone, so the relation
    is published per row instead of being left inside the band's name.
    """
    if not isinstance(lost_published, str) or not lost_published:
        return "unknown"
    if not isinstance(successor_current, str) or not successor_current:
        return "unknown"
    if successor_current == lost_published:
        return "restored"
    if isinstance(lost_current, str) and lost_current and successor_current == lost_current:
        return "copy-of-current"
    return "unrelated"


def classify_remediation_band(*, link_kind: str | None, classes: str,
                              versioned_row: bool, amendment: str) -> str:
    """The band a violating row falls into, from the row's own facts.

    ``classes`` carries the clause's defect classes for the row (``A``
    ineligible path, ``B`` amended content, ``C`` absent content, ``D`` doubly
    bound path); ``link_kind`` is how a later intact outcome re-evidenced the
    row (``supersedes``, ``remedy-name``, ``digest-cited``); ``versioned_row``
    says the row was compliant under the rule in force when it was bound; and
    ``amendment`` says how the bytes stand now (``append`` means they survive
    verbatim).

    The order of the decision matters and is the plant's own: absence takes
    precedence over a binding defect, because the clause calls a deletion "a
    distinct and more severe class"; a link that re-evidences the row decides
    the band before any surviving-bytes question does; and an ineligible path
    is a policy version only when it was compliant when it was bound.
    """
    if "B" in classes or "C" in classes:
        # The content was lost (amended) or is absent (deleted), so the
        # row's band says what re-evidenced it.
        if link_kind == "supersedes":
            # A later, intact outcome of the same role declared this row's
            # record digest: the role acknowledged and re-evidenced the work.
            return "disposed"
        if link_kind == "remedy-name":
            # A later, intact outcome bound a remedy-named successor of the
            # lost artifact: the work was re-evidenced under the clause's
            # naming. The link does not claim the published bytes came back,
            # so the row's byte relation is published beside it.
            return "rebound"
        if link_kind == "digest-cited":
            # A later, intact outcome's artifact carries this row's record
            # digest: the work was cited, and the citation is published with
            # the row so a reader can audit it.
            return "cited"
        if "B" in classes and amendment == "append":
            # The bound bytes survive verbatim at the bound path: the content
            # is intact and only the binding is broken. Nothing was lost, so
            # the plant owes no work on it. A deleted file never reaches here:
            # its bytes do not survive anywhere.
            return "recoverable"
        return "owed"
    if "D" in classes:
        # The path is bound by two outcomes and the store is append-only, so
        # the second binding can never be undone.
        return "permanent"
    if "A" in classes:
        # An ineligible path. Bound before the amendment and inside the owned
        # site, it was compliant then: a policy version. Bound after it, or
        # outside the site, no later rule can make it eligible.
        return "versioned" if versioned_row else "permanent"
    return "owed"
