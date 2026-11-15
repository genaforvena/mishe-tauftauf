"""The publication write path must refuse a draft that breaks the private/public rule.

The rule Ilya confirmed on 2026-11-12 13:46Z was enforced in one place only:
the standing lane, over what was ALREADY published, on an hourly timer.  The
write path itself had one pre-flight (paragraph duplication) and not the other,
so a draft naming a device reached every reader first and the lane noticed it
afterwards.

The publisher is installed under the site home, which is gitignored, so the
durable record is `checks/pub-publish-privacy-gate.py`: it reads the INSTALLED
publisher and asks whether the write is refused.

Every test here names the mutation it pins.  A test that cannot name one is
not pinning anything, and the first version of this file proved the point: an
earlier detector that only looked for the string "privacy" in the publisher
would have read GREEN on a publisher whose gate scanned the body alone and
missed every title, tag, description, canonical URL and cover image.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "checks" / "pub-publish-privacy-gate.py"
SITE = Path(os.environ.get("MISHE_SEED_HOME", ROOT / ".mishe-tauftauf"))
PUBLISHER = SITE / "pub" / "bin" / "mesh-devto-publish"
TERMS = SITE / "pub" / "privacy-terms.json"
CREDENTIALS = SITE / "pub" / "access" / "devto.env"


def _load():
    spec = importlib.util.spec_from_file_location("pub_publish_privacy_gate", CHECK)
    module = importlib.util.module_from_spec(spec)
    sys.modules["pub_publish_privacy_gate"] = module
    spec.loader.exec_module(module)
    return module


check = _load()


def _installed_publisher() -> str:
    """The installed publisher's source, or "" when it is not there."""
    try:
        return PUBLISHER.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _site_copy(tmp_path: Path, publisher_source: str) -> Path:
    """A disposable site home holding this publisher, the terms and the key."""
    home = tmp_path / "site"
    (home / "pub" / "bin").mkdir(parents=True)
    (home / "pub" / "access").mkdir(parents=True)
    tool = home / "pub" / "bin" / "mesh-devto-publish"
    tool.write_text(publisher_source, encoding="utf-8")
    tool.chmod(0o755)
    shutil.copy(TERMS, home / "pub" / "privacy-terms.json")
    shutil.copy(CREDENTIALS, home / "pub" / "access" / "devto.env")
    # The live site keeps the committed privacy check beside pub/ (SITE/checks/),
    # and the publisher finds its pre-flight there.  Mirror that layout.
    (home / "checks").mkdir()
    shutil.copy(ROOT / "checks" / "pub-privacy-scan.py", home / "checks" / "pub-privacy-scan.py")
    return home


def _without_gate(source: str) -> str:
    """The publisher with the privacy pre-flight cut out of it."""
    start = source.index("# PRIVACY PRE-FLIGHT (2026-11-15).")
    end = source.index('if cover: article["main_image"] = cover')
    return source[:start] + source[end:]


def test_the_check_is_committed_so_a_reviewer_can_see_its_contract():
    """A check outside the repository can never be reviewed.

    The same reason the privacy scanner itself moved into `checks/` on
    2026-11-14: the delivery gate's reviewer refused a change whose detector
    lived outside the repository it reads.
    """
    assert CHECK.is_file(), "the gate's detector must be a committed check under checks/"
    assert (ROOT / "checks" / "pub-privacy-scan.py").is_file(), (
        "the check the gate runs must be committed too")


def test_a_publisher_without_the_gate_is_red(tmp_path, capsys):
    """The mutation: the privacy pre-flight is deleted from the publisher.

    This is the shape the account actually had: one gate (duplication) and not
    the other, with the rule enforced only over what was already published.
    """
    source = _installed_publisher()
    if not source:
        import pytest
        pytest.skip("the publisher is not installed on this host")
    home = _site_copy(tmp_path, _without_gate(source))
    code = check.main(["--home", str(home), "--repo", str(ROOT)])
    assert code == 2
    assert "does not refuse" in capsys.readouterr().out


def test_a_publisher_whose_gate_reads_only_the_body_is_red(tmp_path, capsys):
    """The mutation: the gate scans the file as one body and misses the title.

    This is the defect the `--file` mode itself carried until 2026-11-15: it
    read the whole file as one body, so a term in the TITLE was found only
    because the same term also happened to be in the body.  A gate that reads
    the body alone therefore lets a title through, and a title is the field
    DEV prints above every reader's article.
    """
    source = _installed_publisher()
    if not source:
        import pytest
        pytest.skip("the publisher is not installed on this host")
    body_only = tmp_path / "body-only.md"
    body_only.write_text("---\ntitle: clean title\ntags: mishe\n---\n\n"
                         "A clean body with nothing private in it.\n", encoding="utf-8")
    mutated = source.replace(
        '[sys.executable, _pscript, "--file", art, "--terms", _TERMS],',
        '[sys.executable, _pscript, "--file", _BODY_ONLY, "--terms", _TERMS],')
    mutated = mutated.replace(
        '_TERMS = os.path.join(_HOME, "pub", "privacy-terms.json")',
        '_TERMS = os.path.join(_HOME, "pub", "privacy-terms.json")\n'
        '_BODY_ONLY = os.environ.get("MESH_DEVTO_BODY_ONLY_FILE", "")')
    assert mutated != source, "the mutation did not apply; the test would pin nothing"
    home = _site_copy(tmp_path, mutated)
    environment = dict(os.environ)
    environment["MESH_DEVTO_BODY_ONLY_FILE"] = str(body_only)
    old = os.environ.get("MESH_DEVTO_BODY_ONLY_FILE")
    os.environ["MESH_DEVTO_BODY_ONLY_FILE"] = str(body_only)
    try:
        code = check.main(["--home", str(home), "--repo", str(ROOT)])
    finally:
        if old is None:
            os.environ.pop("MESH_DEVTO_BODY_ONLY_FILE", None)
        else:
            os.environ["MESH_DEVTO_BODY_ONLY_FILE"] = old
    assert code == 2
    assert "does not refuse" in capsys.readouterr().out


def test_the_installed_publisher_is_green(tmp_path, capsys):
    """The real publisher, with the gate in place, passes."""
    source = _installed_publisher()
    if not source:
        import pytest
        pytest.skip("the publisher is not installed on this host")
    home = _site_copy(tmp_path, source)
    code = check.main(["--home", str(home), "--repo", str(ROOT)])
    assert code == 0, capsys.readouterr().out
    assert "refuses a rule break" in capsys.readouterr().out


def test_a_missing_publisher_is_unknown_not_green(tmp_path, capsys):
    """A site home without the publisher cannot decide the question.

    UNKNOWN, never GREEN: an absent publisher is not a publisher that refuses.
    """
    home = tmp_path / "site"
    (home / "pub" / "access").mkdir(parents=True)
    shutil.copy(TERMS, home / "pub" / "privacy-terms.json")
    shutil.copy(CREDENTIALS, home / "pub" / "access" / "devto.env")
    code = check.main(["--home", str(home), "--repo", str(ROOT)])
    assert code == 3
    assert "UNKNOWN" in capsys.readouterr().out


def test_a_terms_file_with_no_private_term_is_unknown(tmp_path, capsys):
    """A rule list that forbids nothing cannot test the gate.

    The terms are site-local, so a host whose terms file names no private term
    has no probe to write.  UNKNOWN, never a GREEN that tested nothing.
    """
    source = _installed_publisher()
    if not source:
        import pytest
        pytest.skip("the publisher is not installed on this host")
    home = _site_copy(tmp_path, source)
    (home / "pub" / "privacy-terms.json").write_text(
        json.dumps({"names": [], "devices": [], "places": []}), encoding="utf-8")
    code = check.main(["--home", str(home), "--repo", str(ROOT)])
    assert code == 3
    assert "UNKNOWN" in capsys.readouterr().out


def test_the_refusal_is_distinguished_from_the_api_failure():
    """The gate's refusal and the dead port's failure are different messages.

    Both exit non-zero, so the discriminator has to be the message: a
    publisher with no gate at all still fails on the dead port.
    """
    assert check.refused("refusing: the privacy pre-flight is UNKNOWN (exit 3)") is True
    assert check.refused("refusing: the body repeats a paragraph verbatim") is False
    assert check.refused("urllib.error.URLError: <urlopen error [Errno 111] "
                         "Connection refused>") is False


def test_a_publisher_whose_missing_check_branch_crashes_is_red(tmp_path, capsys):
    """The mutation: the "the rule check is not there" branch dies instead of refusing.

    This is the defect this wake found in the publisher installed on
    2026-11-15.  The branch that handles a missing privacy check built its
    refusal message from a variable the script never defined, so the tool
    raised NameError instead of refusing the write.  The write still did not
    happen -- the crash stopped it -- so the account was safe, but the
    contract the check asserts ("the write path REFUSES") was not met: a
    traceback is not a refusal, and the operator reading the log could not
    tell a refused draft from a broken tool.

    The third probe (terms unreadable) did not catch it: it reached the
    publisher's UNKNOWN branch, which is a different branch.  The fourth
    probe reaches this one.
    """
    source = _installed_publisher()
    if not source:
        import pytest
        pytest.skip("the publisher is not installed on this host")
    mutated = source.replace("_HOME or 'the site home'", "_SITE_HOME or 'the site home'")
    assert mutated != source, "the mutation did not apply; the test would pin nothing"
    home = _site_copy(tmp_path, mutated)
    code = check.main(["--home", str(home), "--repo", str(ROOT)])
    assert code == 2
    assert "cannot be found" in capsys.readouterr().out


def test_a_publisher_whose_duplication_gate_fails_open_is_red(tmp_path, capsys):
    """The mutation: the duplication pre-flight lets the write through when its detector is absent.

    This is the defect this wake found in the publisher installed on
    2026-11-15, and it is the sibling of the one above: the whole
    duplication block was guarded by "if the detector is there", so a
    missing detector meant NO CHECK AT ALL on the body.  The one defect that
    reaches a reader twice -- in a paragraph the author read and approved
    once -- would have been published unchecked.

    The privacy gate refused in this case, and the two gates sit on the same
    write path, so they must not disagree about what "the rule could not be
    applied" means.  This probe is what makes the disagreement visible: the
    publisher is installed under the gitignored site home, so nothing else in
    the repository can see it.
    """
    source = _installed_publisher()
    if not source:
        import pytest
        pytest.skip("the publisher is not installed on this host")
    mutated = source.replace(
        "if _DUP_CHECK and os.path.isfile(_DUP_CHECK):",
        "if False:")
    assert mutated != source, "the mutation did not apply; the test would pin nothing"
    home = _site_copy(tmp_path, mutated)
    code = check.main(["--home", str(home), "--repo", str(ROOT)])
    assert code == 2
    assert "duplication" in capsys.readouterr().out


def test_the_check_names_a_term_the_rule_list_actually_forbids():
    """The probe term comes from the rule list, not from the check.

    The terms are site-local and never committed, so the check reads the file
    the rule itself reads.  A term from the allowed groups would make the
    probe pass for the wrong reason.
    """
    raw = json.loads(TERMS.read_text(encoding="utf-8"))
    allowed = {str(n).casefold() for n in raw.get("allowed", {}).get("own_names", [])}
    term = check.a_private_term(TERMS)
    assert term
    assert term.casefold() not in allowed
