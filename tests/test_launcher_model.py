"""Model identity is recorded per invocation so a fresh successor can attribute a
continuity change to a model replacement without reading /proc or guessing.

The launcher bytes are the durable record of which model an invocation runs on;
these tests pin that seam so a swap stays observable.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "src"


def write_mind(directory: str, slug: str, text: str) -> Path:
    path = Path(directory) / "minds" / slug
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


class LauncherModelTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(SOURCE))
        from mishe_tauftauf import seed

        self.seed = seed
        self.directory = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, self.directory, True)

    def test_reads_selected_model(self):
        path = write_mind(self.directory, "probe", "#!/bin/sh\nexec omp --model vendor/model-a --cwd /repo\n")
        self.assertEqual(self.seed._launcher_model(path), "vendor/model-a")

    def test_follows_a_launcher_swap(self):
        """The point of the seam: a launcher change is what records the replacement."""
        path = write_mind(self.directory, "probe", "#!/bin/sh\nexec omp --model vendor/model-a --cwd /repo\n")
        self.assertEqual(self.seed._launcher_model(path), "vendor/model-a")
        path.write_text("#!/bin/sh\nexec omp --model vendor/model-b --cwd /repo\n")
        self.assertEqual(self.seed._launcher_model(path), "vendor/model-b")

    def test_ignores_comments_and_finds_the_real_flag(self):
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\n# --model vendor/decoy is only a comment\nexec omp --cwd /repo --model vendor/real\n",
        )
        self.assertEqual(self.seed._launcher_model(path), "vendor/real")

    def test_reports_absence_when_no_model_flag(self):
        path = write_mind(self.directory, "probe", "#!/bin/sh\nexec omp --cwd /repo\n")
        self.assertIsNone(self.seed._launcher_model(path))

    def test_reads_the_equals_spelling(self):
        """The shell accepts both spellings; a launcher that uses one is not nameless.

        The plant's launchers are written ``--model=vendor/model-a``. A reader
        that knows only ``--model value`` records "no model" for a launcher that
        plainly names one, which is how the plant's own model record went blind
        for every role but one.
        """
        path = write_mind(self.directory, "probe", "#!/bin/sh\nexec omp --model=vendor/model-a --cwd /repo\n")
        self.assertEqual(self.seed._launcher_model(path), "vendor/model-a")

    def test_follows_a_launcher_swap_in_the_equals_spelling(self):
        path = write_mind(self.directory, "probe", "#!/bin/sh\nexec omp --model=vendor/model-a --cwd /repo\n")
        self.assertEqual(self.seed._launcher_model(path), "vendor/model-a")
        path.write_text("#!/bin/sh\nexec omp --model=vendor/model-b --cwd /repo\n")
        self.assertEqual(self.seed._launcher_model(path), "vendor/model-b")

    def test_ignores_a_comment_decoy_in_the_equals_spelling(self):
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\n# --model=vendor/decoy is only a comment\nexec omp --cwd /repo --model=vendor/real\n",
        )
        self.assertEqual(self.seed._launcher_model(path), "vendor/real")

    def test_an_empty_model_value_is_no_model(self):
        path = write_mind(self.directory, "probe", "#!/bin/sh\nexec omp --model= --cwd /repo\n")
        self.assertIsNone(self.seed._launcher_model(path))

    def test_an_option_in_the_value_place_is_no_model(self):
        path = write_mind(self.directory, "probe", "#!/bin/sh\nexec omp --model --thinking high --cwd /repo\n")
        self.assertIsNone(self.seed._launcher_model(path))

    def test_an_unterminated_quote_is_no_model(self):
        path = write_mind(self.directory, "probe", "#!/bin/sh\nexec omp --model \"unterminated\n")
        self.assertIsNone(self.seed._launcher_model(path))

    def test_a_value_wearing_source_punctuation_is_no_model(self):
        """A record that names ``vendor/model,`` as the model is a wrong fact."""
        path = write_mind(self.directory, "probe", "#!/bin/sh\nexec omp --model=vendor/model, --cwd /repo\n")
        self.assertIsNone(self.seed._launcher_model(path))

    def test_a_hash_inside_a_word_is_not_a_comment(self):
        """The shell reads ``vendor/model#suffix`` as one word, not a truncated one.

        A reader that treats every ``#`` as a comment start records a plausible
        but wrong model name; the shell's own rule is that a ``#`` which begins
        a word starts a comment and one inside a word is an ordinary character.
        """
        path = write_mind(self.directory, "probe", "#!/bin/sh\nexec omp --model=vendor/model#suffix --cwd /repo\n")
        self.assertIsNone(self.seed._launcher_model(path))

    def test_a_decoy_in_another_command_is_not_the_model(self):
        """The model belongs to the command that runs omp."""
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\nprintf '%s\\n' --model=vendor/decoy\nexec omp --model=vendor/real\n",
        )
        self.assertEqual(self.seed._launcher_model(path), "vendor/real")

    def test_a_flag_at_the_end_of_its_command_is_no_model(self):
        """A bare newline ends the command, so the next line is not its value."""
        path = write_mind(self.directory, "probe", "#!/bin/sh\nexec omp --model\nvendor/model\n")
        self.assertIsNone(self.seed._launcher_model(path))

    def test_a_launcher_that_reaches_omp_through_a_wrapper(self):
        """The plant's own genome launcher names a wrapper, not omp."""
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\nexec /usr/bin/python3 /site/longcat-launch.py --role probe "
            "--model=vendor/model-a --thinking high\n",
        )
        self.assertEqual(self.seed._launcher_model(path), "vendor/model-a")

    def test_a_data_argument_that_names_omp_is_not_a_launch(self):
        """A path in an argument is an argument; the command runs no omp."""
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\nprintf %s /home/user/bin/omp --model=vendor/decoy\n"
            "exec /usr/bin/python3 /site/longcat-launch.py --model=vendor/real\n",
        )
        self.assertEqual(self.seed._launcher_model(path), "vendor/real")

    def test_a_command_after_an_exec_never_runs(self):
        """``exec`` replaces the shell, so the lines after it are unreachable."""
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\nexec /usr/bin/python3 /site/longcat-launch.py --model=vendor/real\n"
            "/bin/echo --model=vendor/decoy\n",
        )
        self.assertEqual(self.seed._launcher_model(path), "vendor/real")

    def test_an_exec_without_a_model_is_no_model(self):
        """The pane's own command decides, even when it names no model."""
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\nprintf '%s\\n' --model=vendor/decoy\nexec /home/user/bin/omp --cwd /repo\n",
        )
        self.assertIsNone(self.seed._launcher_model(path))

    def test_an_exec_behind_an_assignment_still_replaces_the_shell(self):
        """``MODE=x exec ...`` is the shell's own exec, and it ends the script."""
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\nMODE=x exec /usr/bin/python3 /site/longcat-launch.py --model=vendor/real\n"
            "/bin/echo --model=vendor/decoy\n",
        )
        self.assertEqual(self.seed._launcher_model(path), "vendor/real")

    def test_a_command_that_is_not_the_pane_does_not_compete(self):
        """Only the pane's own program competes for the reading."""
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\n/home/user/bin/omp --model=vendor/real\n/bin/echo --model=vendor/decoy\n",
        )
        self.assertEqual(self.seed._launcher_model(path), "vendor/real")

    def test_two_launches_without_an_exec_settle_nothing(self):
        """The shell runs them in turn; which one the pane runs is not in the bytes."""
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\n/home/user/bin/omp --model=vendor/real\n/home/user/bin/omp --model=vendor/decoy\n",
        )
        self.assertIsNone(self.seed._launcher_model(path))

    def test_a_separator_on_one_line_is_refused(self):
        """A construct the reader does not model makes the reading unknown."""
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\n/bin/echo --model=vendor/decoy ; exec omp --model=vendor/real\n",
        )
        self.assertIsNone(self.seed._launcher_model(path))

    def test_a_conditional_is_refused(self):
        """Which branch the shell takes is not in the bytes the reader reads."""
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\nif false; then\n  exec omp --model=vendor/decoy\nelse\n  exec omp --model=vendor/real\nfi\n",
        )
        self.assertIsNone(self.seed._launcher_model(path))

    def test_a_here_document_is_refused(self):
        """The body of a here-document is data, not commands."""
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\n/bin/cat <<EOF\nexec omp --model=vendor/decoy\nEOF\nexec omp --model=vendor/real\n",
        )
        self.assertIsNone(self.seed._launcher_model(path))

    def test_a_pipeline_is_refused(self):
        """An exec inside a pipeline replaces that process, not the launcher."""
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\nexec /bin/echo --model=vendor/decoy | /bin/cat\nexec omp --model=vendor/real\n",
        )
        self.assertIsNone(self.seed._launcher_model(path))

    def test_an_and_list_is_refused(self):
        """An AND-list's second command runs only if the first succeeds."""
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\n/bin/echo --model=vendor/decoy && exec omp --model=vendor/real\n",
        )
        self.assertIsNone(self.seed._launcher_model(path))

    def test_a_backslash_with_spaces_does_not_continue_a_line(self):
        """A backslash escapes the space after it; the line ends there."""
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\nexec omp \\   \n/bin/echo --model=vendor/decoy\n",
        )
        self.assertIsNone(self.seed._launcher_model(path))

    def test_a_quoted_hash_is_refused(self):
        """A quoted ``#`` is data, and this reader does not model it."""
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\n/bin/echo \"#\" ; exec omp --model=vendor/real\n",
        )
        self.assertIsNone(self.seed._launcher_model(path))

    def test_a_redirection_before_the_program_is_refused(self):
        """A redirection changes what the program runs; the reader refuses it."""
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\nexec </dev/null omp --model=vendor/real\n",
        )
        self.assertIsNone(self.seed._launcher_model(path))

    def test_a_long_env_option_takes_its_operand(self):
        """``env --unset OLD /path/omp`` runs omp."""
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\nexec env --unset OLD /home/user/bin/omp --model vendor/real\n",
        )
        self.assertEqual(self.seed._launcher_model(path), "vendor/real")

    def test_an_unwordable_line_refuses_the_script(self):
        """The shell rejects such a script, so no line of it runs."""
        path = write_mind(
            self.directory,
            "probe",
            '#!/bin/sh\nexec omp --model="unterminated\nexec omp --model=vendor/decoy\n',
        )
        self.assertIsNone(self.seed._launcher_model(path))

    def test_a_program_that_is_not_the_pane_is_refused(self):
        """``/bin/echo --model=...`` prints a model and runs no pane."""
        path = write_mind(self.directory, "probe", "#!/bin/sh\nexec /bin/echo --model=vendor/decoy\n")
        self.assertIsNone(self.seed._launcher_model(path))

    def test_a_parameter_expansion_is_refused(self):
        """An expansion can name another program or inject a flag."""
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\nBIN=echo\nexec /bin/$BIN --model=vendor/decoy\n",
        )
        self.assertIsNone(self.seed._launcher_model(path))

    def test_an_expanded_flag_is_refused(self):
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\nEXTRA=--model=vendor/decoy\nexec omp $EXTRA --model=vendor/real\n",
        )
        self.assertIsNone(self.seed._launcher_model(path))

    def test_an_unknown_env_option_is_refused(self):
        """``env`` rejects the option, so no program runs at all."""
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\nexec env --not-an-env-option /home/user/bin/omp --model vendor/real\n",
        )
        self.assertIsNone(self.seed._launcher_model(path))

    def test_a_wrapper_after_env_is_the_program(self):
        """``env`` runs an external program, not another shell wrapper."""
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\nexec env command /bin/echo --model=vendor/decoy\n",
        )
        self.assertIsNone(self.seed._launcher_model(path))

    def test_an_unmodelled_wrapper_is_refused(self):
        """``command``, ``nohup`` and ``setsid`` have their own option grammars."""
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\ncommand -p omp --model=vendor/real\n",
        )
        self.assertIsNone(self.seed._launcher_model(path))

    def test_a_continued_word_is_one_word(self):
        """The shell deletes the backslash-newline pair and puts nothing in its place."""
        path = write_mind(self.directory, "probe", "#!/bin/sh\nexec omp --model=vendor/mo\\\ndel\n")
        self.assertEqual(self.seed._launcher_model(path), "vendor/model")

    def test_a_control_builtin_is_refused(self):
        """``exit`` ends the script before the launcher reaches its program."""
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\nexit 0\nexec omp --model=vendor/decoy\n",
        )
        self.assertIsNone(self.seed._launcher_model(path))

    def test_a_second_exec_is_a_program_name(self):
        """``exec`` takes the command as its operand, so a second one is a name."""
        path = write_mind(self.directory, "probe", "#!/bin/sh\nexec exec omp --model=vendor/decoy\n")
        self.assertIsNone(self.seed._launcher_model(path))

    def test_an_assignment_after_exec_is_a_program_name(self):
        path = write_mind(self.directory, "probe", "#!/bin/sh\nexec FOO=bar omp --model=vendor/decoy\n")
        self.assertIsNone(self.seed._launcher_model(path))

    def test_a_unicode_name_is_not_an_assignment(self):
        """The shell's assignment names are ASCII."""
        path = write_mind(self.directory, "probe", "#!/bin/sh\nλ=x exec omp --model=vendor/decoy\n")
        self.assertIsNone(self.seed._launcher_model(path))

    def test_the_model_comes_from_the_pane_argument_list(self):
        """An ``env`` operand named ``--model`` is not the pane's flag."""
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\nexec env -u --model omp --model=vendor/real\n",
        )
        self.assertEqual(self.seed._launcher_model(path), "vendor/real")

    def test_an_arbitrary_python_script_is_not_the_pane(self):
        """Only the plant's own launcher script reaches the pane."""
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\nexec /usr/bin/python3 /not/the/plant/report.py --model=vendor/decoy\n",
        )
        self.assertIsNone(self.seed._launcher_model(path))

    def test_an_env_flag_with_a_word_attached_is_refused(self):
        """``env`` rejects a word attached to a flag that takes none."""
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\nexec env --debug=bogus omp --model=vendor/real\n",
        )
        self.assertIsNone(self.seed._launcher_model(path))

    def test_env_assignments_are_not_the_program(self):
        """``env ROLE=probe /path/omp`` runs omp."""
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\nexec env ROLE=probe /home/user/bin/omp --model=vendor/real\n",
        )
        self.assertEqual(self.seed._launcher_model(path), "vendor/real")

    def test_a_python_flag_before_the_launcher_script(self):
        """``python3 -u launcher.py`` is the plant's own wrapper shape."""
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\nexec /usr/bin/python3 -u /site/longcat-launch.py --model=vendor/real\n",
        )
        self.assertEqual(self.seed._launcher_model(path), "vendor/real")

    def test_a_backslash_continues_a_line(self):
        """The flag on the continuation line is the flag of the command."""
        path = write_mind(self.directory, "probe", "#!/bin/sh\nexec omp \\\n  --model vendor/real\n")
        self.assertEqual(self.seed._launcher_model(path), "vendor/real")

    def test_env_options_are_not_the_program(self):
        """``env -u OLD /path/omp`` runs omp."""
        path = write_mind(
            self.directory,
            "probe",
            "#!/bin/sh\nexec env -u OLD /home/user/bin/omp --model vendor/real\n",
        )
        self.assertEqual(self.seed._launcher_model(path), "vendor/real")

    def test_missing_launcher_is_none(self):
        self.assertIsNone(self.seed._launcher_model(Path(self.directory) / "minds" / "absent"))


def test_respawn_reads_the_model_the_new_process_runs(tmp_path):
    """A respawn records the launcher's current bytes, which is what the pane runs."""
    import sys

    sys.path.insert(0, str(SOURCE))
    from mishe_tauftauf import seed
    from mishe_tauftauf.feed import Feed

    home = tmp_path / "site"
    write_mind(str(home), "probe", "#!/bin/sh\nexec omp --model vendor/model-a --cwd /repo\n")
    seed._record_mind_model(home, "probe", home / "minds" / "probe", "start")
    bodies = [e.body.splitlines()[0] for e in Feed(home).entries() if e.source == "mishe-tauftauf"]
    assert bodies == [f"mind model top-pain probe launcher={home / 'minds' / 'probe'} reason=start model=vendor/model-a"]


def test_a_launcher_swap_replaces_the_recorded_model(tmp_path):
    """A model change at a respawn is exactly what the record must make visible."""
    import sys

    sys.path.insert(0, str(SOURCE))
    from mishe_tauftauf import seed
    from mishe_tauftauf.feed import Feed

    home = tmp_path / "site"
    launcher = write_mind(str(home), "probe", "#!/bin/sh\nexec omp --model vendor/model-a --cwd /repo\n")
    seed._record_mind_model(home, "probe", launcher, "clear")
    launcher.write_text("#!/bin/sh\nexec omp --model vendor/model-b --cwd /repo\n")
    seed._record_mind_model(home, "probe", launcher, "clear")
    bodies = [e.body.splitlines()[0] for e in Feed(home).entries() if e.source == "mishe-tauftauf"]
    assert bodies == [
        f"mind model top-pain probe launcher={launcher} reason=clear model=vendor/model-a",
        f"mind model top-pain probe launcher={launcher} reason=clear model=vendor/model-b",
    ]


def test_the_same_model_does_not_add_a_record_per_idle_rotation(tmp_path):
    """A rotation that lands on the same model is not a new continuity fact."""
    import sys

    sys.path.insert(0, str(SOURCE))
    from mishe_tauftauf import seed
    from mishe_tauftauf.feed import Feed

    home = tmp_path / "site"
    launcher = write_mind(str(home), "probe", "#!/bin/sh\nexec omp --model vendor/model-a --cwd /repo\n")
    seed._record_mind_model(home, "probe", launcher, "clear")
    seed._record_mind_model(home, "probe", launcher, "clear")
    bodies = [e.body.splitlines()[0] for e in Feed(home).entries() if e.source == "mishe-tauftauf"]
    assert bodies == [f"mind model top-pain probe launcher={launcher} reason=clear model=vendor/model-a"]


def test_a_launcher_without_a_model_is_recorded_as_none_not_silence(tmp_path):
    """Losing ``--model`` is a model change, so it must not pass unrecorded."""
    import sys

    sys.path.insert(0, str(SOURCE))
    from mishe_tauftauf import seed
    from mishe_tauftauf.feed import Feed

    home = tmp_path / "site"
    launcher = write_mind(str(home), "probe", "#!/bin/sh\nexec omp --cwd /repo\n")
    seed._record_mind_model(home, "probe", launcher, "start")
    bodies = [e.body.splitlines()[0] for e in Feed(home).entries() if e.source == "mishe-tauftauf"]
    assert bodies == [f"mind model top-pain probe launcher={launcher} reason=start model=none"]


def test_a_missing_launcher_records_none_without_failing(tmp_path):
    """A site that lacks the launcher still records the respawn, without raising."""
    import sys

    sys.path.insert(0, str(SOURCE))
    from mishe_tauftauf import seed
    from mishe_tauftauf.feed import Feed

    home = tmp_path / "site"
    home.mkdir()
    seed._record_mind_model(home, "probe", home / "minds" / "probe", "start")
    bodies = [e.body.splitlines()[0] for e in Feed(home).entries() if e.source == "mishe-tauftauf"]
    assert bodies == [f"mind model top-pain probe launcher={home / 'minds' / 'probe'} reason=start model=none"]


if __name__ == "__main__":
    unittest.main()
