"""_producer() reads the checkout commit through the reflog_boundary adapter."""

import subprocess

from mishe_tauftauf import discovery

SHA = "216890302f9500fa53d857c0758181bf043d7300"


def _fake_run(stdout: str, returncode: int = 0):
    def run(*args, **kwargs):
        return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")
    return run


def test_producer_takes_full_sha_with_trailing_newline(monkeypatch):
    monkeypatch.setattr(discovery.subprocess, "run", _fake_run(SHA + "\n"))
    assert discovery._producer().endswith("@" + SHA)


def test_producer_is_unknown_when_output_is_not_a_full_sha(monkeypatch):
    monkeypatch.setattr(discovery.subprocess, "run", _fake_run("fatal: not a git repository\n"))
    assert discovery._producer().endswith("@unknown")


def test_producer_is_unknown_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(discovery.subprocess, "run", _fake_run(SHA + "\n", returncode=128))
    assert discovery._producer().endswith("@unknown")
