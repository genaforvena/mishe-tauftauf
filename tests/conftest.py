"""Every integration test uses a disposable tmux socket, never the live plant."""
import os
import subprocess
from pathlib import Path

import pytest

LIFELINE_SLICE = "mishe-lifeline.slice"


def lifeline_refusal(cgroup_text):
    """Return a refusal message when a process cgroup sits in the lifeline slice.

    A pytest run inherits its cgroup, and so does every tmux server and pain-watch
    worker it forks. A run started from life-support placed ~12 workers and a test
    tmux server inside mishe-lifeline.slice (memory.max 3 GiB) and tripped the
    guard by cgroup OOM on 2026-11-02. The session therefore refuses to start there
    before any fixture forks, so the test tree can only land in a non-lifeline slice.
    Returns None when the cgroup text names no lifeline slice.
    """
    for line in str(cgroup_text).splitlines():
        if not line.startswith("0::"):
            continue
        if LIFELINE_SLICE in line[3:].split("/"):
            return ("UNKNOWN test session started inside " + LIFELINE_SLICE
                    + "; refusing before forking tmux or workers. Run pytest from a"
                    " non-lifeline shell.")
    return None


def refuse_lifeline_run():
    """Stop the session before any fixture forks tmux or a worker."""
    try:
        cgroup_text = Path("/proc/self/cgroup").read_text()
    except OSError:
        return
    message = lifeline_refusal(cgroup_text)
    if message is not None:
        pytest.exit(message, returncode=3)


@pytest.fixture(autouse=True)
def restore_site_binding_env():
    """Undo MISHE_SEED_HOME writes made by in-process CLI calls.

    ``cli.main`` binds ``MISHE_SEED_HOME`` with ``os.environ.setdefault`` when a
    test runs it in-process. Nothing restored that, so a later case in the same
    session was refused by ``observations.validate_home_binding`` (CI core 3.10,
    run 41259521270: 591 failed, 1620 "does not match" refusals). Each case
    starts and ends with the binding as the session fixture left it.
    """
    saved = os.environ.get("MISHE_SEED_HOME")
    yield
    if saved is None:
        os.environ.pop("MISHE_SEED_HOME", None)
    else:
        os.environ["MISHE_SEED_HOME"] = saved




@pytest.fixture(scope="session", autouse=True)
def private_tmux_server(tmp_path_factory):
    refuse_lifeline_run()
    root = tmp_path_factory.mktemp("mishe-test-tmux")
    patch = pytest.MonkeyPatch()
    patch.delenv("TMUX", raising=False)
    patch.setenv("TMUX_TMPDIR", str(root))
    # Live systemd keepers belong to the plant; ordinary tests need none.
    patch.setenv("MISHE_SESSION_RUNNER", "")
    # A mind shell exports MISHE_SEED_HOME, and the CLI and the validated library
    # writers refuse an explicit --home naming a different site
    # (observations.validate_home_binding). Cases that build disposable tmp_path
    # sites therefore failed 426 of 2367 whenever the suite ran inside a mind,
    # while CI — which has no ambient binding — stayed green. An in-mind run has
    # to describe the tree under test exactly as CI does; the binding itself is
    # covered where it lives, in tests/test_site_home.py.
    patch.delenv("MISHE_SEED_HOME", raising=False)
    try:
        yield root
    finally:
        # Explicit socket even if a test failed to restore its environment.
        socket = root / ("tmux-" + str(os.getuid())) / "default"
        subprocess.run(["tmux", "-S", str(socket), "kill-server"],
                       capture_output=True, timeout=5)
        patch.undo()
