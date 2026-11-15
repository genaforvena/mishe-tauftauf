#!/usr/bin/env python3
"""Detector: the DEV write path refuses a draft that breaks the private/public rule.

The gap this closes.  The rule is the one Ilya confirmed on 2026-11-12 13:46Z:
no published field may name a private person, a device, a place or an internal
id.  It was enforced in one place only -- the standing lane
``checks/pub-privacy-scan.py --all``, which reads what is ALREADY published, on
an hourly timer.  The write path itself had one pre-flight (paragraph
duplication) and not the other, even though the same check has a per-file mode
(``--file``) that reads the title, the tags, the description, the canonical URL,
the cover image and the body as the fields the publisher sends them.  So a
draft naming a device reached every reader first, and the lane noticed it
afterwards, at the next timer run.

The publisher is installed at ``SITE/pub/bin/mesh-devto-publish`` and the site
home is gitignored, so the change that closes this gap is not reviewable bytes
on its own.  This check is: it reads the INSTALLED publisher and asks whether
the privacy pre-flight is really in front of the write.

**What it asks, behaviourally.**  A shape check would answer "the file mentions
a privacy check"; this one answers "the write is refused".  It runs the
installed publisher against a controlled draft with the DEV API pointed at a
dead port, so no request can reach the account, and it asks three questions:

* a draft whose TITLE carries a private term is REFUSED, with the refusal
  naming the rule (this is the case the gap is about: a title is a published
  field, and a term in it reaches every reader of the article);
* a clean draft is NOT refused by the gate -- it goes on to the API stage and
  fails there.  A gate that refuses everything is not a gate;
* a draft whose rule terms cannot be read is REFUSED, because "no rule was
  applied" is not "no rule was broken";
* a draft whose rule CHECK cannot be found at all is REFUSED too, because
  that branch of the publisher is a different branch, and a gate whose
  "cannot find the rule" path is untested is a gate whose behaviour on that
  path is unknown.

The refusal is distinguished from the API failure by the message, not by the
exit code: both are non-zero, and a publisher with no gate at all still fails
on the dead port.

Exit status:
  0  the installed publisher refuses a rule break and lets a clean draft pass
  2  the publisher does not refuse, or refuses a clean draft
  3  the question could not be decided: the publisher, the terms file or the
     privacy check could not be read or run
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

GREEN = "GREEN"
RED = "RED"
UNKNOWN = "UNKNOWN"

PUBLISHER = "pub/bin/mesh-devto-publish"
TERMS = "pub/privacy-terms.json"
CREDENTIALS = "pub/access/devto.env"

#: A port nothing listens on.  The DEV API is redirected to it so a run of the
#: publisher cannot reach the account: the check observes the GATE, not the
#: write, and it must not publish a probe.
DEAD_API = "http://127.0.0.1:9/api"

#: The words the refusal has to carry.  A publisher without the gate fails on
#: the dead port with a connection error, so the message is the discriminator:
#: the gate's refusal names the rule it applied, the API's failure does not.
REFUSAL_MARKERS = ("refusing:",)
RULE_MARKERS = ("privacy", "private/public", "rule list")
#: The sibling gate's rule.  The duplication pre-flight refuses in its own
#: words, so a probe about that gate looks for these and not for the privacy
#: markers.
DUP_MARKERS = ("duplication", "paragraph")


def default_home() -> Path:
    """The site home, from the environment the plant itself uses."""
    for name in ("MISHE_SEED_HOME", "MISHE_TAUFTAUF_HOME"):
        value = os.environ.get(name)
        if value:
            return Path(value)
    return Path(__file__).resolve().parents[1] / ".mishe-tauftauf"


def a_private_term(terms_file: Path) -> str:
    """One term the rule list forbids, taken from the site-local terms file.

    The terms are site-local and never committed, so this check reads the file
    the rule itself reads.  A term is taken from the groups the rule treats as
    private; when the file names none, the question cannot be decided and the
    caller reports UNKNOWN rather than testing nothing.
    """
    try:
        raw = json.loads(terms_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return ""
    for group in ("devices", "places", "names", "household", "projects"):
        for term in raw.get(group, []):
            if isinstance(term, str) and term.strip():
                return term.strip()
    return ""


def draft(title: str, body: str = "A controlled probe. Nothing private in this body.") -> str:
    """A publishable draft: the front matter the publisher reads, and a body."""
    return ("---\n"
            f"title: {title}\n"
            "tags: mishe, devops\n"
            "description: A controlled probe of the publication write-path gate.\n"
            "canonical_url: https://example.invalid/probe\n"
            "---\n\n" + body + "\n")


def run_publisher(publisher: Path, env_file: Path, home: Path, article: Path,
                  terms: Path,
                  extra: dict | None = None) -> subprocess.CompletedProcess:
    """Run the installed publisher against a draft, with the API unreachable.

    ``extra`` sets variables for one run.  It is how a probe asks the
    publisher a specific question: pointing ``MESH_DEVTO_PRIVACY_CHECK`` at
    a path that does not exist asks "what happens when the rule cannot be
    found at all?".
    """
    environment = dict(os.environ)
    environment.update({
        "MESH_DEVTO_ENV": str(env_file),
        "MESH_DEVTO_API": DEAD_API,
        "MISHE_SEED_HOME": str(home),
        "MISHE_TAUFTAUF_HOME": str(home),
    })
    if extra:
        environment.update(extra)
    environment.pop("DEVTO_API_KEY", None)
    return subprocess.run([str(publisher), "--draft", str(article)],
                          capture_output=True, text=True, timeout=180,
                          env=environment)


def refused(output: str) -> bool:
    """Whether the output is the gate's refusal rather than the API's failure.

    The gate's refusal says "refusing" and names the rule it applied; a
    publisher with no gate fails on the dead port with a connection error,
    which is neither.
    """
    return refused_naming(output, RULE_MARKERS)


def refused_naming(output: str, markers: tuple[str, ...]) -> bool:
    """Whether the output is a refusal that names the given gate.

    ``refused`` is the privacy gate's refusal.  The write path has a second
    gate -- the paragraph-duplication pre-flight -- whose refusal names a
    different rule, so a probe about that gate asks for its own markers
    rather than the privacy ones.  Sharing the mechanics keeps the two
    readings of "refusal" from drifting apart.
    """
    lowered = output.lower()
    if "connection refused" in lowered or "urlerror" in lowered:
        return False
    if not all(marker in lowered for marker in REFUSAL_MARKERS):
        return False
    return any(marker in lowered for marker in markers)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", default="", help="the site home")
    parser.add_argument("--repo", default="", help="the repository checkout")
    args = parser.parse_args(argv)

    home = Path(args.home) if args.home else default_home()
    # The repository is where this check's own siblings live: the committed
    # privacy check the gate runs.  When the duty runs this check from a
    # RELEASE copy, the release carries the same committed privacy check, so
    # either repository answers the question.
    repo = Path(args.repo) if args.repo else Path(__file__).resolve().parents[1]
    if not (repo / "checks" / "pub-privacy-scan.py").is_file():
        sibling = Path(__file__).resolve().parents[1]
        if (sibling / "checks" / "pub-privacy-scan.py").is_file():
            repo = sibling

    publisher = home / PUBLISHER
    terms_file = home / TERMS
    env_file = home / CREDENTIALS
    for path, what in ((publisher, "the installed publisher"),
                       (terms_file, "the rule terms"),
                       (env_file, "the credentials")):
        if not path.is_file():
            print(f"UNKNOWN: {what} could not be read at {path}")
            return 3

    term = a_private_term(terms_file)
    if not term:
        print("UNKNOWN: the rule terms name no private term to test the gate with")
        return 3
    if not (repo / "checks" / "pub-privacy-scan.py").is_file():
        print("UNKNOWN: the privacy check the gate runs is not committed")
        return 3

    with tempfile.TemporaryDirectory(prefix="pub-privacy-gate-") as work:
        work_dir = Path(work)
        broken = work_dir / "broken.md"
        broken.write_text(draft(f"A controlled probe naming {term} in the title"),
                          encoding="utf-8")
        clean = work_dir / "clean.md"
        clean.write_text(draft("A controlled probe that carries nothing private"),
                         encoding="utf-8")

        try:
            red = run_publisher(publisher, env_file, home, broken, terms_file)
        except subprocess.TimeoutExpired:
            print("UNKNOWN: the publisher did not answer on the refusing draft")
            return 3
        except OSError as error:
            print(f"UNKNOWN: the publisher could not be run ({error})")
            return 3
        output = (red.stdout or "") + (red.stderr or "")
        if not refused(output):
            print("RED: the publication write path does not refuse a draft that "
                  "breaks the private/public rule. The rule was enforced only "
                  "over what was already published, so a rule break reached "
                  "every reader before the standing lane noticed it.")
            print("--- the publisher said ---")
            print(output.strip()[-1200:])
            return 2

        try:
            green = run_publisher(publisher, env_file, home, clean, terms_file)
        except (subprocess.TimeoutExpired, OSError) as error:
            print(f"UNKNOWN: the publisher did not answer on the clean draft ({error})")
            return 3
        green_output = (green.stdout or "") + (green.stderr or "")
        if refused(green_output):
            print("RED: the publication write path refuses a draft that breaks "
                  "nothing. A gate that refuses everything is not a gate.")
            print("--- the publisher said ---")
            print(green_output.strip()[-1200:])
            return 2

        # The rule terms unreadable: the gate must refuse, because a rule that
        # could not be read is not a rule that was satisfied.
        empty_home = work_dir / "site"
        (empty_home / "pub").mkdir(parents=True)
        (empty_home / PUBLISHER).parent.mkdir(parents=True, exist_ok=True)
        try:
            unknown = run_publisher(publisher, env_file, empty_home, clean, terms_file)
        except (subprocess.TimeoutExpired, OSError) as error:
            print(f"UNKNOWN: the publisher did not answer on the unreadable-terms draft ({error})")
            return 3
        unknown_output = (unknown.stdout or "") + (unknown.stderr or "")
        if not refused(unknown_output):
            print("RED: the publication write path does not refuse when the rule "
                  "terms cannot be read. 'No rule was applied' is not 'no rule "
                  "was broken'.")
            print("--- the publisher said ---")
            print(unknown_output.strip()[-1200:])
            return 2

        # The rule check itself is not on disk.  This is a different branch
        # from the one above: there the rule exists and cannot be read, here
        # the rule cannot be found at all.  Both must refuse the write.
        #
        # This probe exists because the two were confused.  The third probe
        # (terms unreadable) reached the publisher's UNKNOWN branch, and the
        # branch that handles a MISSING check was never run, so it could
        # crash instead of refusing: on 2026-11-15 that branch's message
        # named a variable the script did not define, so the tool died with a
        # NameError instead of refusing.  The write still did not happen, so
        # the account was safe -- but the gate's own contract says a refusal
        # is what a reader of the log should see, and a traceback is not one.
        # A gate whose refusal path is untested is a gate whose refusal path
        # is unknown.
        missing = run_publisher(publisher, env_file, home, clean, terms_file,
                                extra={"MESH_DEVTO_PRIVACY_CHECK":
                                       str(work_dir / "no-such-check.py")})
        missing_output = (missing.stdout or "") + (missing.stderr or "")
        if not refused(missing_output):
            print("RED: the publication write path does not refuse when the "
                  "rule check cannot be found at all. A gate that cannot find "
                  "its rule must refuse the write, not crash on the way to "
                  "the API.")
            print("--- the publisher said ---")
            print(missing_output.strip()[-1200:])
            return 2

        # The DUPLICATION gate, the sibling gate on the same write path.  It
        # is here because the publisher is installed under the gitignored
        # site home: a defect in it is invisible to every other committed
        # check, and this one failed OPEN.  Its whole block was guarded by
        # "if the detector is there", so when the detector was not there the
        # write went through unchecked -- the one defect that reaches a
        # reader twice, in a paragraph the author read and approved once.
        # Measured 2026-11-15 with MESH_DEVTO_DUP_CHECK pointing at a path
        # that does not exist: the tool went straight to the API stage.
        missing_dup = run_publisher(publisher, env_file, home, clean, terms_file,
                                    extra={"MESH_DEVTO_DUP_CHECK":
                                           str(work_dir / "no-such-detector.py")})
        missing_dup_output = (missing_dup.stdout or "") + (missing_dup.stderr or "")
        if not refused_naming(missing_dup_output, DUP_MARKERS):
            print("RED: the publication write path does not refuse when the "
                  "paragraph-duplication detector cannot be found. The two "
                  "gates on this write path must not disagree about what "
                  "'the rule could not be applied' means: a missing detector "
                  "is an unchecked write, not a clean one.")
            print("--- the publisher said ---")
            print(missing_dup_output.strip()[-1200:])
            return 2

    print(f"{GREEN}: the publication write path refuses a rule break, lets a "
          f"clean draft through, and refuses when the rule cannot be read or "
          f"cannot be found (tested term: {term})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
