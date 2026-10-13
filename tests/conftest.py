"""Every integration test uses a disposable tmux socket, never the live plant."""
import os
import subprocess

import pytest


@pytest.fixture(scope="session", autouse=True)
def private_tmux_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("mishe-test-tmux")
    patch = pytest.MonkeyPatch()
    patch.delenv("TMUX", raising=False)
    patch.setenv("TMUX_TMPDIR", str(root))
    # Live systemd keepers belong to the plant; ordinary tests need none.
    patch.setenv("MISHE_SESSION_RUNNER", "")
    try:
        yield root
    finally:
        # Explicit socket even if a test failed to restore its environment.
        socket = root / ("tmux-" + str(os.getuid())) / "default"
        subprocess.run(["tmux", "-S", str(socket), "kill-server"],
                       capture_output=True, timeout=5)
        patch.undo()
