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
