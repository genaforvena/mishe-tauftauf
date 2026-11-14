"""Tests for the Pub article paragraph-duplication detector.

The detector answers one question about an article body: does it repeat a
paragraph verbatim?  On 2026-11-14 the live article 4954898 carried the same
closing paragraph twice, in two different sections, and a reader read it
twice.  The publisher's read-back could not see it: the bytes it sent matched
the bytes the server stored, and those bytes repeated themselves.

Every test here names the mutation to the detector that would make it fail.
A test that cannot name one is not pinning anything.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check = _load("pub_article_duplication", "checks/pub-article-duplication.py")

# The defect itself: the paragraph as it appeared in article 4954898 before
# the 2026-11-14 correction, twice.
REPEATED = (
    "The claim that matters for an engineer is not any one of these lenses. "
    "It is that a single situation can be looked at through all three, and "
    "that looking through only one of them misreads it."
)


def _body(*paragraphs: str) -> str:
    return "\n\n".join(paragraphs)


def test_a_repeated_paragraph_is_found():
    """The defect the detector exists for is found, with both positions.

    Dropping the second occurrence from the finding would leave a report that
    names a repetition without saying where, and a reader of the report could
    not find the copy to remove.
    """
    body = _body("First.", REPEATED, "Second.", REPEATED, "Third.")
    findings = check.find_duplicates(body)
    assert len(findings) == 1
    assert findings[0]["count"] == 2
    assert findings[0]["positions"] == [1, 3]
    assert findings[0]["text"] == REPEATED


def test_a_distinct_body_has_no_finding():
    """A body whose paragraphs differ has no finding.

    A detector that reports every body would be noise, and its verdict would
    be ignored — the failure mode that leaves a real defect in place.
    """
    body = _body("First paragraph.", "Second paragraph.", "Third paragraph.")
    assert check.find_duplicates(body) == []


def test_whitespace_differences_do_not_hide_a_repetition():
    """A repetition that differs only in line wrapping is still a repetition.

    A rewrite that re-wraps a paragraph leaves the reader with the same
    words twice.  Comparing raw paragraph strings would miss that, and the
    defect would reach the live article unchanged.
    """
    wrapped = ("The claim that matters for an engineer is not any one of these\n"
               "lenses. It is that a single situation can be looked at through\n"
               "all three, and that looking through only one of them misreads it.")
    body = _body("First.", wrapped, "Second.", REPEATED)
    findings = check.find_duplicates(body)
    assert len(findings) == 1
    assert findings[0]["count"] == 2


def test_a_short_repeated_line_is_not_a_finding():
    """A repeated short line stays out of the report.

    Article 4894878 repeats a 60-character refrain nine times on purpose, and
    a rule that flagged it would cry wolf on the corpus the threshold was
    measured against.
    """
    refrain = "Noticed from the outside: not the check, and not its report."
    body = _body(*([refrain] * 9))
    assert len(refrain) < check.MIN_PARAGRAPH_CHARS
    assert check.find_duplicates(body) == []


def test_a_repeated_paragraph_inside_a_code_fence_is_not_a_finding():
    """A code block that repeats itself is not a repeated paragraph.

    A snippet that must appear twice — a command and its output, a fixture
    shown next to the failure it explains — is not a reader seeing the same
    prose twice, and refusing it would block a legitimate article.
    """
    snippet = REPEATED
    body = _body("First.", "```\n%s\n```" % snippet, "Second.",
                 "```\n%s\n```" % snippet)
    assert check.find_duplicates(body) == []


def test_a_long_repeated_paragraph_after_a_code_fence_is_still_found():
    """Removing code fences does not blind the detector to the prose around them.

    A rule that dropped everything after the first fence would miss the very
    defect it was written for whenever the article carries an example first.
    """
    body = _body("```\nexample\n```", "First.", REPEATED, "Second.", REPEATED)
    findings = check.find_duplicates(body)
    assert len(findings) == 1
    assert findings[0]["count"] == 2


def test_findings_are_ordered_by_severity():
    """The most repeated paragraph is reported first.

    A report that lists the repetitions in an arbitrary order asks the reader
    to find the worst one themselves.
    """
    thrice = "T" * 130 + " repeated three times."
    twice = "U" * 130 + " repeated twice."
    body = _body(thrice, twice, thrice, twice, thrice)
    findings = check.find_duplicates(body)
    assert [finding["count"] for finding in findings] == [3, 2]


def test_the_body_mode_exits_two_on_a_repeated_paragraph(tmp_path):
    """The write path is refused on a repeated paragraph."""
    path = tmp_path / "body.md"
    path.write_text(_body("First.", REPEATED, "Second.", REPEATED), encoding="utf-8")
    assert check.main(["--body", str(path)]) == 2


def test_the_body_mode_exits_zero_on_a_clean_body(tmp_path):
    """A clean body passes the pre-flight."""
    path = tmp_path / "body.md"
    path.write_text(_body("First.", "Second."), encoding="utf-8")
    assert check.main(["--body", str(path)]) == 0


def test_a_missing_body_is_unknown_not_a_pass(tmp_path):
    """A body that cannot be read is UNKNOWN, never a pass.

    The write path treats exit 0 as "no repetition"; a missing file that
    answered 0 would let the very body it could not read through.
    """
    assert check.main(["--body", str(tmp_path / "no-such-body.md")]) == 3


def test_a_malformed_capture_is_unknown_not_a_pass(tmp_path):
    """A capture that cannot be parsed is UNKNOWN, never a pass."""
    path = tmp_path / "capture.json"
    path.write_text("not json at all", encoding="utf-8")
    assert check.main(["--bodies", str(path)]) == 3


def test_the_capture_mode_reads_both_capture_shapes(tmp_path):
    """A capture holds bodies directly or holds whole article records.

    The plant's two captures differ: one maps an article id to the body text,
    the other to the article record that carries it.  A detector that read
    only one shape would answer UNKNOWN on the other and the observation
    would never run.
    """
    direct = tmp_path / "direct.json"
    direct.write_text(json.dumps({"1": _body("First.", REPEATED, "Second.",
                                             REPEATED)}), encoding="utf-8")
    records = tmp_path / "records.json"
    records.write_text(json.dumps({"1": {"title": "t", "body": _body(
        "First.", REPEATED, "Second.", REPEATED)}}), encoding="utf-8")
    assert check.main(["--bodies", str(direct)]) == 2
    assert check.main(["--bodies", str(records)]) == 2


def test_a_capture_entry_without_a_body_is_unknown(tmp_path):
    """A capture entry that carries no body is UNKNOWN, never a silent skip.

    Skipping the entry would answer "no repetition" about a body the detector
    never read.
    """
    path = tmp_path / "capture.json"
    path.write_text(json.dumps({"1": {"title": "no body here"}}), encoding="utf-8")
    assert check.main(["--bodies", str(path)]) == 3


def test_exactly_one_mode_is_required(tmp_path):
    """Naming neither mode, or both, is UNKNOWN.

    Running the detector over nothing would answer GREEN about every body.
    """
    path = tmp_path / "body.md"
    path.write_text("First.", encoding="utf-8")
    assert check.main([]) == 3
    assert check.main(["--body", str(path), "--bodies", str(path)]) == 3


def test_the_measured_live_corpus_reads_green_after_the_correction():
    """The captured live bodies hold exactly one repetition: the fixed defect.

    The capture is the plant's own evidence from 2026-11-14, taken while the
    defect was still live.  It is the corpus the threshold was measured
    against, so a change in its verdict is a change in the rule.
    """
    capture = Path.home() / ".mishe-tauftauf/pub/artifacts"
    candidates = sorted(capture.glob(
        "primary-text-reach-20261114-wake15/live-bodies-wake15.json"))
    if not candidates:
        import pytest
        pytest.skip("the live-body capture is not present on this host")
    assert check.main(["--bodies", str(candidates[0])]) == 2
