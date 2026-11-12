from pathlib import Path

from mishe_tauftauf import server_placement as sp

PLANTS = "0::/user.slice/user-1000.slice/user@1000.service/mishe.slice/mishe-plants.slice/run-rX.scope\n"
OTHER = "0::/user.slice/user-1000.slice/session-2.scope\n"


def test_placed_server_is_verified():
    v = sp.placement_verdict("s", 12335, PLANTS, "mishe-plants.slice")
    assert v["state"] == "verified" and v["pid"] == 12335


def test_server_outside_slice_is_drift_not_verified():
    v = sp.placement_verdict("s", 12335, OTHER, "mishe-plants.slice")
    assert v["state"] == "drift"


def test_slice_name_must_be_a_whole_component():
    v = sp.placement_verdict("s", 1, "0::/x/mishe-plants.slice.old/run.scope\n", "mishe-plants.slice")
    assert v["state"] == "drift"


def test_absent_session_or_unreadable_cgroup_is_unknown_not_calm():
    assert sp.placement_verdict("s", None, None, "mishe-plants.slice")["state"] == "unknown"
    assert sp.placement_verdict("s", 5, None, "mishe-plants.slice")["state"] == "unknown"
    assert sp.placement_verdict("s", 5, "1:name=systemd:/x\n", "mishe-plants.slice")["state"] == "unknown"


def _proc(root: Path, pid: int, text: str) -> None:
    (root / str(pid)).mkdir(parents=True, exist_ok=True)
    (root / str(pid) / "cgroup").write_text(text, encoding="utf-8")


def _socket_dir(tmp_path: Path, uid: int = 1000) -> str:
    sock = tmp_path / f"tmp-{uid}" / "site" / f"tmux-{uid}" / "default"
    sock.parent.mkdir(parents=True)
    sock.write_text("", encoding="utf-8")
    return str(sock)


def test_reader_finds_live_server_by_socket_and_reads_its_cgroup(tmp_path, monkeypatch):
    sock = _socket_dir(tmp_path)
    monkeypatch.setattr(sp, "_sockets", lambda uid: [sock])
    proc = tmp_path / "proc"
    _proc(proc, 12335, PLANTS)

    def runner(argv):
        if "display-message" in argv:
            return "12335\n"
        if "has-session" in argv:
            return ""
        return None

    rows = sp.read_server_placement(uid=1000, proc_root=proc, runner=runner)
    assert rows[0]["id"] == sp.SENSE_ID
    assert rows[0]["state"] == "verified"
    assert "mishe-self-development-current@12335=mishe-plants.slice" in rows[0]["sample"]


def test_reader_with_no_socket_is_unknown_not_verified(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "_sockets", lambda uid: [])
    rows = sp.read_server_placement(uid=1000, proc_root=tmp_path, runner=lambda argv: None)
    assert rows[0]["state"] == "unknown"
    assert "no live tmux server" in rows[0]["sample"]


def test_reader_reports_drift_when_server_is_outside_slice(tmp_path, monkeypatch):
    sock = _socket_dir(tmp_path)
    monkeypatch.setattr(sp, "_sockets", lambda uid: [sock])
    proc = tmp_path / "proc"
    _proc(proc, 77, OTHER)

    def runner(argv):
        if "has-session" in argv:
            return ""
        if "display-message" in argv:
            return "77\n"
        return None

    rows = sp.read_server_placement(uid=1000, proc_root=proc, runner=runner)
    assert rows[0]["state"] == "drift"
