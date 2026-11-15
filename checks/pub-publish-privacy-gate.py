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
  applied" is not "no rule was broken".

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
                  terms: Path) -> subprocess.CompletedProcess:
    """Run the installed publisher against a draft, with the API unreachable."""
    environment = dict(os.environ)
    environment.update({
        "MESH_DEVTO_ENV": str(env_file),
        "MESH_DEVTO_API": DEAD_API,
        "MISHE_SEED_HOME": str(home),
        "MISHE_TAUFTAUF_HOME": str(home),
    })
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
    lowered = output.lower()
    if "connection refused" in lowered or "urlerror" in lowered:
        return False
    if not all(marker in lowered for marker in REFUSAL_MARKERS):
        return False
    return any(marker in lowered for marker in RULE_MARKERS)


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

    print(f"{GREEN}: the publication write path refuses a rule break, lets a "
          f"clean draft through, and refuses when the rule cannot be read "
          f"(tested term: {term})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
