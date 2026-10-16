"""Boot mkdir must preserve the SSH tmux socket root permissions."""
import subprocess
import pytest
from mishe_tauftauf import plant


def _mkdir_line(text: str) -> str:
    line = next(x for x in text.splitlines() if x.startswith("ExecStartPre=/usr/bin/mkdir -p "))
    return line.split("=", 1)[1]

@pytest.mark.parametrize("slug", ["health", "silence"])
def test_boot_mkdir_creates_private_parent(tmp_path, monkeypatch, slug):
    site = tmp_path / "tmux-1000" / "site"
    monkeypatch.setattr(plant, "_site_tmux_tmpdir", lambda home: site)
    text = (plant.silence_unit_text(tmp_path, "owned", "/usr/bin/python3")
            if slug == "silence" else plant.unit_text(tmp_path, "owned", slug, "/usr/bin/python3"))
    mask = next((line.split("=", 1)[1] for line in text.splitlines() if line.startswith("UMask=")), "0022")
    mkdir = _mkdir_line(text)
    assert not (site.parent.exists() or site.exists())
    subprocess.run(["sh", "-c", 'umask "$1"; $2', "sh", mask, mkdir], check=True)
    assert site.parent.stat().st_mode & 0o777 == 0o700
    assert site.stat().st_mode & 0o777 == 0o700
