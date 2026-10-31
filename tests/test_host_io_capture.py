"""Post-latch io writer capture must be read-only, bounded and robust to a fake /proc tree."""
from mishe_tauftauf import host_containment as guard


def disk(name, writes, write_ms=0, in_flight=0, io_ms=0, major=8):
    """One /proc/diskstats row: name at field 3, writes completed at field 7."""
    stats = [0, 0, 0, 0, writes, 0, 0, write_ms, in_flight, io_ms, 0]
    return f"{major:4d}       0 {name} " + " ".join(map(str, stats)) + "\n"


def make_proc(root, pids, *, disks="", meminfo="Dirty: 2048 kB\nWriteback: 512 kB\n", sys_block=()):
    root.mkdir(parents=True, exist_ok=True)
    for pid, (comm, state, wchar, write_bytes, cpu) in pids.items():
        d = root / str(pid)
        d.mkdir()
        (d / "io").write_text(f"rchar: 1\nwchar: {wchar}\nsyscr: 1\nsyscw: 1\nread_bytes: 0\n"
                              f"write_bytes: {write_bytes}\ncancelled_write_bytes: 0\n")
        # Fields after ')' are: state, ppid, pgrp, session, tty, tpgid, flags, minflt,
        # cminflt, majflt, cmajflt, utime, stime ...
        (d / "stat").write_text(f"{pid} ({comm}) {state} 1 1 1 0 -1 0 0 0 0 0 {cpu} 0\n")
    (root / "diskstats").write_text(disks)
    (root / "meminfo").write_text(meminfo)
    for name in sys_block:
        (root.parent / "sys_block" / name).mkdir(parents=True, exist_ok=True)
    return root


def test_snapshot_reads_writers_disks_and_writeback_without_writing(tmp_path):
    proc = make_proc(tmp_path / "proc", {
        101: ("jbd2/sda1-8", "S", 10, 4096, 3),
        202: ("writer", "D", 900, 8192, 7),
    }, disks=disk("sda", 100, 40, 2, 900) + disk("loop0", 1, major=7),
        sys_block=("sda",))
    before = sorted(p.name for p in proc.rglob("*"))
    snap = guard.io_snapshot(proc, tmp_path / "sys_block")
    assert sorted(p.name for p in proc.rglob("*")) == before  # read-only
    assert snap["pids"]["202"]["state"] == "D"
    assert snap["pids"]["101"]["write_bytes"] == 4096
    assert set(snap["disks"]) == {"sda"}  # loop devices excluded
    assert snap["memory"] == {"Dirty": 2048 * 1024, "Writeback": 512 * 1024}
    assert snap["unreadable_pids"] == 0


def test_unreadable_and_non_numeric_entries_are_counted_not_fatal(tmp_path):
    proc = make_proc(tmp_path / "proc", {101: ("ok", "S", 1, 1, 1)})
    (proc / "303").mkdir()  # no io/stat: unreadable, not a crash
    (proc / "self").mkdir()  # not a pid
    snap = guard.io_snapshot(proc, tmp_path / "sys_block")
    assert set(snap["pids"]) == {"101"}
    assert snap["unreadable_pids"] == 1


def test_missing_proc_root_yields_empty_census(tmp_path):
    snap = guard.io_snapshot(tmp_path / "absent", tmp_path / "sys_block")
    assert snap == dict(pids={}, unreadable_pids=0, disks={}, memory={})


def test_delta_ranks_storage_and_syscall_writers_and_flags_d_state(tmp_path):
    a = make_proc(tmp_path / "a", {
        1: ("big-storage", "S", 100, 0, 0),
        2: ("big-syscall", "R", 0, 0, 0),
        3: ("stuck", "D", 0, 0, 0),
    }, disks=disk("sda", 100), sys_block=("sda",))
    b = make_proc(tmp_path / "b", {
        1: ("big-storage", "S", 150, 9000, 0),
        2: ("big-syscall", "R", 5000, 10, 0),
        3: ("stuck", "D", 0, 4096, 0),
    }, disks=disk("sda", 130), sys_block=("sda",))
    d = guard.io_deltas(guard.io_snapshot(a, tmp_path / "sys_block"),
                        guard.io_snapshot(b, tmp_path / "sys_block"), 2.0)
    assert [w["comm"] for w in d["top_storage_writers"][:2]] == ["big-storage", "stuck"]
    assert d["top_storage_writers"][0]["write_bytes_delta"] == 9000
    assert d["top_storage_writers"][1]["write_bytes_delta"] == 4096
    assert d["top_syscall_writers"][0]["comm"] == "big-syscall"
    assert d["d_state"] == [dict(pid=3, comm="stuck")]
    assert d["devices"]["sda"]["writes_completed"] == 30
    assert d["memory_end"]["Dirty"] == 2048 * 1024


def test_recycled_pid_with_a_different_comm_is_not_compared(tmp_path):
    a = make_proc(tmp_path / "a", {9: ("old", "S", 0, 0, 0)})
    b = make_proc(tmp_path / "b", {9: ("new", "S", 999, 999, 0)})
    d = guard.io_deltas(guard.io_snapshot(a, tmp_path / "sys_block"),
                        guard.io_snapshot(b, tmp_path / "sys_block"), 1.0)
    assert d["top_storage_writers"] == [] and d["top_syscall_writers"] == []


def test_missing_diskstats_and_meminfo_degrade_to_empty_fields(tmp_path):
    proc = tmp_path / "proc"
    (proc / "5").mkdir(parents=True)
    (proc / "5" / "io").write_text("wchar: 1\nwrite_bytes: 1\n")
    (proc / "5" / "stat").write_text("5 (x) S 1 1 1 0 -1 0 0 0 0 0 1 1\n")
    snap = guard.io_snapshot(proc, tmp_path / "sys_block")
    assert snap["disks"] == {} and snap["memory"] == {}
    assert snap["pids"]["5"]["write_bytes"] == 1
