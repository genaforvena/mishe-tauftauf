import subprocess
from mishe_tauftauf import tmux


def test_explicit_test_directory_overrides_inherited_live_tmux(monkeypatch):
    monkeypatch.setenv("TMUX", "/run/user/1000/live-socket,123,0")
    monkeypatch.setenv("TMUX_TMPDIR", "/tmp/owned-fixture")
    seen = {}
    monkeypatch.setattr(tmux.shutil, "which", lambda _: "/usr/bin/tmux")
    def run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, b"", b"")
    monkeypatch.setattr(tmux.subprocess, "run", run)
    tmux._tmux("kill-server", check=False)
    assert "env" in seen and "TMUX" not in seen["env"]
    assert seen["env"]["TMUX_TMPDIR"] == "/tmp/owned-fixture"
