"""The commit-msg attribution hook (tools/commit-msg-attribution.py).

The defect it repairs, measured 2026-11-15: two commits whose Mishe trailer lines run
straight into the body paragraph, so ``git interpret-trailers --parse`` prints nothing
and ``work_boundary.new_commits`` records them observed but unattributed.
"""
import importlib.util
import os
import shutil
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


def test_repeated_coauthor_trailers_are_all_preserved():
    """The defect the reviewer found on release e6617410: the old ``setdefault``
    collapsed two ``Co-Authored-By`` lines to the first and silently dropped the
    rest, contradicting the "never removes a trailer value" contract."""
    text = ("subject\n\nbody\n"
            "Co-Authored-By: A <a@x>\n"
            "Co-Authored-By: B <b@x>\n")
    new, changed = module.normalize(text, "wake-x-1", "field-review")
    assert changed is True
    assert parse_trailers(new) == ["Mishe-Wake: wake-x-1", "Mishe-Role: field-review",
                                   "Co-Authored-By: A <a@x>", "Co-Authored-By: B <b@x>"]


def test_a_trailer_value_continuation_line_survives():
    """Git folds a whitespace-prefixed line after a trailer into that trailer's
    value. The hook must relocate the whole value, not only its first line, or it
    rewrites a recognised trailer value -- the same contract the repeated-key
    defect broke. Before the fix: the value became ``Co-Authored-By: A <a@x>`` and
    ``  continued note`` was stranded in the body."""
    text = ("subject\n\nbody text\n"
            "Mishe-Wake: wake-x-1\n"
            "Mishe-Role: field-review\n"
            "\n"
            "Co-Authored-By: A <a@x>\n"
            "  continued note\n")
    assert parse_trailers(text) == ["Co-Authored-By: A <a@x> continued note"]
    new, changed = module.normalize(text, "", "")
    assert changed is True
    assert parse_trailers(new) == ["Mishe-Wake: wake-x-1", "Mishe-Role: field-review",
                                   "Co-Authored-By: A <a@x> continued note"]
    assert "  continued note" not in new.split("Co-Authored-By: A <a@x>")[0]


def test_repeated_keys_and_continuations_together_survive():
    """A message with the Mishe lines run into the body, two coauthors and a
    continuation value on the second."""
    text = ("subject\n\nbody\n"
            "Mishe-Wake: wake-x-1\n"
            "Mishe-Role: field-review\n"
            "Co-Authored-By: A <a@x>\n"
            "Co-Authored-By: B <b@x>\n"
            "\textra detail\n")
    new, changed = module.normalize(text, "", "")
    assert changed is True
    assert parse_trailers(new) == ["Mishe-Wake: wake-x-1", "Mishe-Role: field-review",
                                   "Co-Authored-By: A <a@x>",
                                   "Co-Authored-By: B <b@x> extra detail"]


def test_unattributable_message_with_no_environment_is_untouched():
    bare = "subject\n\nbody\n"
    assert module.normalize(bare, "", "") == (bare, False)


def test_normalize_is_idempotent():
    new, _ = module.normalize(NO_BLANK_LINE, "", "")
    assert module.normalize(new, "", "") == (new, False)


def test_a_midbody_trailer_does_not_absorb_a_following_indented_line():
    """Git folds a continuation only inside the final paragraph. A matched
    trailer line elsewhere in the body is relocated by itself; the indented line
    after it is separate body text and must not become part of the value. The
    ``83d25263`` hook moved both, so the relocated value read
    ``Co-Authored-By: A <a@x> indented code``."""
    text = ("subject\n\n"
            "Co-Authored-By: A <a@x>\n"
            "    indented code\n"
            "\n"
            "fixes thing\n")
    new, changed = module.normalize(text, "wake-x-1", "field-review")
    assert changed is True
    assert parse_trailers(new) == ["Mishe-Wake: wake-x-1", "Mishe-Role: field-review",
                                   "Co-Authored-By: A <a@x>"]
    assert "    indented code" in new.split("Mishe-Wake:")[0]


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


def test_git_commit_runs_the_installed_hook_and_lands_attributed(tmp_path):
    """The production path, not just ``normalize``: in a checkout that carries the
    hook, a real ``git commit`` with the sloppy message must come out attributed.
    This is the recurrence detector for the failure the guard exists for -- the
    hook missing or not discovered (``.git/hooks`` unused, a foreign hook in
    place) leaves commits unattributed even though ``normalize`` is correct."""
    env = {**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@e",
           "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@e",
           "MISHE_WAKE_KEY": "", "MISHE_ROLE": ""}
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, env=env, check=True)
    # Stage the two files a checkout has, so the installer runs as written.
    (tmp_path / "tools").mkdir()
    for name in ("commit-msg-attribution.py", "install-commit-msg-attribution.sh"):
        shutil.copy(ROOT / "tools" / name, tmp_path / "tools" / name)
    subprocess.run([str(tmp_path / "tools" / "install-commit-msg-attribution.sh")],
                   cwd=tmp_path, env=env, check=True)
    msg = tmp_path / "msg"
    msg.write_text(NO_BLANK_LINE)
    (tmp_path / "f").write_text("x")
    subprocess.run(["git", "add", "f"], cwd=tmp_path, env=env, check=True)
    subprocess.run(["git", "commit", "-q", "-F", str(msg)], cwd=tmp_path, env=env, check=True)

    body = subprocess.run(["git", "log", "-1", "--format=%B"], cwd=tmp_path,
                          env=env, capture_output=True, text=True, check=True).stdout
    assert parse_trailers(body) == ["Mishe-Wake: wake-genome-123", "Mishe-Role: genome",
                                    "Co-Authored-By: Claude Haiku 5.5 <noreply@anthropic.com>"]
