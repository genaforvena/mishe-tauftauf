"""Every applicability branch of the observer-check runner must be runnable.

``SITE/checks-runner/observer-checks.py`` decides whether a scheduled check
applies to a target. The ``applies()`` helper has one branch per requirement
the INVENTORY table can name, and a branch that raises when it is reached is a
latent fault: the plant reads its schedule as healthy while the check it would
have run cannot start.

Measured on this plant (2026-11-10): the ``pin-verdict`` branch referenced
``PIN_VERDICT_RE``, a name that was never defined anywhere in the file. The
branch was unreachable only because every INVENTORY row carried no requirement,
so no cycle exercised it; the gate's review found it while the runner's bytes
became a reviewed commit. The name is now defined, and these tests hold every
branch to the same contract.

These tests read the committed runner's source and execute its ``applies()``
function with each requirement it names, so a branch that names an undefined
name again fails here rather than in a plant cycle.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COMMITTED_RUNNER = REPO / "checks-runner" / "observer-checks.py"


def load_runner():
    """The committed runner as a module, without installing it as a package."""
    spec = importlib.util.spec_from_file_location("_observer_checks_runner", COMMITTED_RUNNER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def requirements_in_use(module) -> set[str]:
    """The requirements the INVENTORY table actually names."""
    found = set()
    for row in module.INVENTORY:
        requirement = row[4]
        if isinstance(requirement, str):
            found.add(requirement)
    return found


def test_the_committed_runner_can_be_loaded_and_names_its_requirements():
    module = load_runner()
    assert module.INVENTORY, "the runner reads no checks at all"
    assert module.NOT_SCHEDULED, "the runner declares no unscheduled check"


def test_every_requirement_the_inventory_names_has_a_working_branch():
    """Each named requirement must decide, not raise.

    ``applies()`` returns None when the requirement is met and a reason string
    when the target does not carry what the check needs. A branch that raises
    is a check that cannot run.
    """
    module = load_runner()
    frame = (
        "HEADLINE: GREEN\n"
        "MIND WALL — shared follow-through evidence; not an authored wall\n"
        "RUNTIME: MATCH selection=PINNED source=/tmp/release sha256=abc\n"
    )
    for requirement in sorted(requirements_in_use(module)):
        met = module.applies(requirement, frame, "no published frame")
        assert met is None, (
            "requirement %r rejected a frame that carries what it needs: %s"
            % (requirement, met))
        unmet = module.applies(requirement, "HEADLINE: GREEN\n", "no published frame")
        assert isinstance(unmet, str) and unmet, (
            "requirement %r accepted a frame that carries nothing for it" % requirement)


def test_every_applicability_branch_runs_without_an_undefined_name():
    """No branch of ``applies()`` may reference a name the module does not define.

    The branches are the ones the requirements can select, so this walks each
    one with a target that carries every marker the runner knows.
    """
    module = load_runner()
    frame = (
        "HEADLINE: GREEN\n"
        "MIND WALL — shared follow-through evidence; not an authored wall\n"
        "RUNTIME: MATCH selection=PINNED source=/tmp/release sha256=abc\n"
    )
    for requirement in sorted(requirements_in_use(module) | {"evidence", "pin-verdict"}):
        module.applies(requirement, frame, "no published frame")


def test_the_pin_verdict_pattern_names_the_runtime_verdict_states():
    """The frame's runtime verdict line is what the pin-verdict branch reads."""
    module = load_runner()
    pattern = getattr(module, "PIN_VERDICT_RE", None)
    assert isinstance(pattern, re.Pattern), (
        "the runner's pin-verdict branch needs PIN_VERDICT_RE, a compiled pattern "
        "that names the runtime verdict states")
    assert pattern.match("RUNTIME: MATCH selection=PINNED source=/tmp/release sha256=abc")
    assert pattern.match("RUNTIME: DRIFT selection=PINNED source=/tmp/release sha256=abc")
    assert not pattern.match("RUNTIME: nonsense selection=PINNED")
    assert not pattern.match("HEADLINE: GREEN")
