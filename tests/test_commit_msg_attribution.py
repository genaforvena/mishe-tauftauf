"""The commit-msg attribution hook (tools/commit-msg-attribution.py).

The defect it repairs, measured 2026-11-15: two commits whose Mishe trailer lines run
straight into the body paragraph, so ``git interpret-trailers --parse`` prints nothing
and ``work_boundary.new_commits`` records them observed but unattributed.
"""
import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("commit_msg_attribution",
                                              ROOT / "tools/commit-msg-attribution.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def parse_trailers(text):
    return [line for line in subprocess.run(["git", "interpret-trailers", "--parse"],
                                            input=text, capture_output=True, text=True,
                                            check=True).stdout.splitlines() if line]


# A real message (ac98aaf7): body paragraph, then the trails with NO blank line.
NO_BLANK_LINE = (
    "footprint: the value carries no sample age\n"
    "\n"
    "Drops sample_age from the footprint value so the signal stops changing with the clock.\n"
    "Mishe-Wake: wake-genome-123\n"
    "Mishe-Role: genome\n"
    "Co-Authored-By: Claude Haiku 5.5 <noreply@anthropic.com>\n"
)


def test_no_blank_line_message_parses_nothing_before_the_fix():
    assert parse_trailers(NO_BLANK_LINE) == []


def test_normalize_moves_them_into_the_final_block():
    new, changed = module.normalize(NO_BLANK_LINE, "", "")
    assert changed is True
    assert parse_trailers(new) == ["Mishe-Wake: wake-genome-123", "Mishe-Role: genome",
                                   "Co-Authored-By: Claude Haiku 5.5 <noreply@anthropic.com>"]
    # The body is preserved, not dropped.
    assert "Drops sample_age from the footprint value" in new


def test_a_valid_message_is_left_unchanged():
    valid = "subject\n\nbody\n\nMishe-Wake: wake-x-1\nMishe-Role: docs\n"
    assert module.normalize(valid, "wake-ignored", "ignored") == (valid, False)


def test_missing_mishe_lines_are_supplied_from_the_wake_environment():
    """65bc07a0/7e86e57f: only a coauthor trailer, no Mishe lines at all."""
    bare = "subject\n\nbody\n\nCo-Authored-By: Someone <a@b>\n"
    assert parse_trailers(bare) == ["Co-Authored-By: Someone <a@b>"]
    new, changed = module.normalize(bare, "wake-genome-9", "genome")
    assert changed is True
    assert parse_trailers(new) == ["Mishe-Wake: wake-genome-9", "Mishe-Role: genome",
                                   "Co-Authored-By: Someone <a@b>"]


def test_unattributable_message_with_no_environment_is_untouched():
    bare = "subject\n\nbody\n"
    assert module.normalize(bare, "", "") == (bare, False)


def test_normalize_is_idempotent():
    new, _ = module.normalize(NO_BLANK_LINE, "", "")
    assert module.normalize(new, "", "") == (new, False)


def test_prose_that_merely_names_the_trailers_is_not_hoisted():
    """A commit about this format (like 5869b8eb) keeps its prose."""
    text = ("docs: a commit trailer block needs a blank line\n"
            "\n"
            "A body paragraph running into the Mishe-Wake/Mishe-Role/Co-Authored-By lines\n"
            "is one paragraph.\n"
            "\n"
            "Mishe-Wake: wake-field-review-1\n"
            "Mishe-Role: field-review\n")
    assert parse_trailers(text) == ["Mishe-Wake: wake-field-review-1", "Mishe-Role: field-review"]
    assert module.normalize(text, "", "") == (text, False)
