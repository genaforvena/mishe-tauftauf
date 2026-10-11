"""The outcome-publication dialect: what decodes, and what stays a gap.

The classifier (``outcome_events``) recognizes a candidate publication by
prefix and then validates it, so a damaged publication is reported as a
coverage gap; the advisor (``witness_analysis``) needs the whole publication or
nothing. These tests pin the division: the adapter decodes syntax and returns
``None`` for anything the dialect does not describe, while the reserved-prefix
recognition that makes a damaged publication *visible* stays a separate answer.
"""
from __future__ import annotations

import hashlib

import pytest

from mishe_tauftauf.outcome_publication import (
    OUTCOME_KINDS,
    body_lines,
    decode_evidence,
    decode_excerpt,
    decode_publication_body,
    decode_text,
    decode_timestamp,
    decode_work_handoff,
    first_significant_line,
    has_outcome_prefix,
    parse_header,
    publication_prose,
    reference_digest,
    reference_lines,
    sole_final_reference,
    starts_outcome_publication,
)

DIGEST = hashlib.sha256(b'evidence').hexdigest()
REFERENCE = f'[record] records/{DIGEST}.json sha256={DIGEST}'


def reference_for(digest: str = DIGEST) -> str:
    return f'[record] records/{digest}.json sha256={digest}'


def publication(kind: str = 'accepted', role: str = 'witness',
                text: str = 'measured finding') -> str:
    return f'Wall outcome {kind} by {role}\n{text}\n{reference_for()}'


@pytest.mark.parametrize('kind', OUTCOME_KINDS)
def test_header_decodes_every_supported_kind(kind):
    assert parse_header(f'Wall outcome {kind} by discover') == (kind, 'discover')


@pytest.mark.parametrize('line', [
    'Wall outcome accepted by Witness',            # role grammar is lowercase
    'Wall outcome accepted by ',                   # role is required
    'Wall outcome accepted-by discover',           # kind is a discrete word
    'Wall outcome pending by discover',            # unsupported kind
    ' Wall outcome accepted by discover',          # the header is the whole line
    'Wall outcome accepted by discover, extra',    # trailing prose displaces it
    'Wall outcome accepted by discover\n',         # a line, not a paragraph
    'wall outcome accepted by discover',           # the prefix is reserved case
])
def test_malformed_header_is_not_a_header(line):
    assert parse_header(line) is None


def test_indented_outcome_prefix_is_a_candidate_but_not_a_body_prefix():
    # A publication quoted inside a larger document is still a candidate the
    # classifier must inspect; it is not a bare publication on the tape.
    assert has_outcome_prefix('  Wall outcome accepted by discover')
    assert not starts_outcome_publication('  Wall outcome accepted by discover')
    assert starts_outcome_publication(publication())
    assert not starts_outcome_publication('A body that only mentions outcomes')


def test_first_significant_line_skips_blanks_and_reports_none():
    assert first_significant_line(['', '   ', 'Wall outcome accepted by discover']) == \
        'Wall outcome accepted by discover'
    assert first_significant_line(['', '  ']) == ''


def test_reference_lines_recognize_independent_of_indent():
    # Recognition ignores leading blanks, but the line itself is returned as
    # the body carries it, so a later digest read fails on an indented
    # reference exactly as the record reader does.
    assert reference_lines(body_lines(f'prose\n  {reference_for()}\n')) == [f'  {reference_for()}']
    assert reference_lines(body_lines('prose only\n')) == []


def test_sole_final_reference_requires_one_reference_and_nothing_after_it():
    lines = body_lines(publication())
    assert sole_final_reference(lines) == reference_for()
    assert sole_final_reference(body_lines(f'{publication()}\ntrailing prose')) is None
    assert sole_final_reference(body_lines(f'{publication()}\n{reference_for()}')) is None
    assert sole_final_reference(body_lines('Wall outcome accepted by witness\ntext')) is None


def test_reference_digest_names_the_record_it_cites():
    assert reference_digest(reference_for(DIGEST)) == DIGEST
    # The pair is validated by the immutable-record reader, not here: a
    # reference naming two different digests still decodes to its first name,
    # and the mismatch is caught when the record itself is read.
    other = hashlib.sha256(b'other').hexdigest()
    assert reference_digest(f'[record] records/{DIGEST}.json sha256={other}') == DIGEST
    assert reference_digest('[record] records/nothex.json sha256=nothex') is None
    assert reference_digest('[record] records/' + 'a' * 63 + '.json sha256=' + 'a' * 63) is None


def test_publication_body_decodes_the_canonical_shape():
    decoded = decode_publication_body(publication('hypothesis-changed', 'discover', 'two\nlines'))
    assert (decoded.kind, decoded.role, decoded.text) == ('hypothesis-changed', 'discover', 'two\nlines')
    assert decoded.digest == DIGEST
    assert decoded.reference == REFERENCE


@pytest.mark.parametrize('body', [
    'Wall outcome accepted by witness\ntext',                    # no reference at all
    'Wall outcome accepted by witness\ntext\n[record] x',        # undecodable reference
    'prose first\nWall outcome accepted by witness\ntext\n' + REFERENCE,   # displaced header
    'Wall outcome accepted by witness\ntext\n' + REFERENCE + '\nmore',     # trailing prose
    '',                                                          # empty body
])
def test_publication_body_refuses_anything_else(body):
    assert decode_publication_body(body) is None


def test_publication_prose_is_header_and_reference_excluded():
    assert publication_prose(body_lines(publication(text='a\nb'))) == 'a\nb'


def test_timestamp_decoding_keeps_the_offset_the_tape_carries():
    from datetime import datetime, timezone
    assert decode_timestamp('2026-10-06T17:00:00Z') == \
        datetime(2026, 10, 6, 17, 0, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        decode_timestamp('not a timestamp')


def test_text_decode_rejects_blank_and_non_string_prose():
    assert decode_text('a finding') == 'a finding'
    assert decode_text('   \n') is None
    assert decode_text('') is None
    assert decode_text(None) is None
    assert decode_text(17) is None


def test_evidence_decode_requires_a_bound_path_and_a_hex_digest():
    assert decode_evidence({'path': 'artifacts/wake.md', 'sha256': DIGEST}) == \
        {'path': 'artifacts/wake.md', 'sha256': DIGEST}
    assert decode_evidence({'path': '', 'sha256': DIGEST}) is None
    assert decode_evidence({'path': 'artifacts/wake.md'}) is None
    assert decode_evidence({'path': 'artifacts/wake.md', 'sha256': 'abc'}) is None
    assert decode_evidence({'path': 'artifacts/wake.md', 'sha256': 17}) is None
    assert decode_evidence(['artifacts/wake.md', DIGEST]) is None


def test_work_handoff_decode_is_channel_scoped():
    assert decode_work_handoff('[work] channel=witness wake=2\nHANDOFF:\nOld analysis') == 'Old analysis'
    # A handoff whose marker is missing keeps the channel header in its text:
    # the channel prefix decides recognition, the marker only splits, and a
    # handoff without a marker is not rewritten into a shorter one.
    assert decode_work_handoff('[work] channel=witness wake=2\nno marker here') == \
        '[work] channel=witness wake=2\nno marker here'
    assert decode_work_handoff('Wall outcome accepted by witness\nprose') is None


def test_excerpt_decode_drops_framed_json_only():
    assert decode_excerpt('{"identity": "x"}\nreadable prose') == 'readable prose'
    assert decode_excerpt('prose with {braces} inline') == 'prose with {braces} inline'
