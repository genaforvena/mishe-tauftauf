from mishe_tauftauf import live_signal as ls
from mishe_tauftauf import memory_stat_reading as msr

BODY = "anon 2147483648\nfile 9663676416\nshmem 1048576\nfile_mapped 100\n"


def test_parse_reads_anon_file_shmem():
    assert msr.parse_memory_stat(BODY) == {"anon": 2147483648, "file": 9663676416, "shmem": 1048576}


def test_missing_key_is_a_parse_failure():
    try:
        msr.parse_memory_stat("anon 1\nfile 2\n")
    except ValueError as exc:
        assert "shmem" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_negative_counter_is_a_parse_failure():
    try:
        msr.parse_memory_stat("anon -1\nfile 2\nshmem 3\n")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_swap_max_text_max_and_numbers():
    assert msr.parse_bytes_or_max("max\n") is None
    assert msr.parse_bytes_or_max("268435456\n") == 268435456


def test_reader_missing_directory_is_unknown_never_zero(tmp_path):
    out = msr.read_memory_stat(tmp_path / "absent", at=10.0)
    assert all(s.state == ls.UNKNOWN and s.value is None for s in out.values())


def test_reader_on_fake_cgroup_is_ok(tmp_path):
    (tmp_path / "memory.stat").write_text(BODY)
    (tmp_path / "memory.swap.current").write_text("1000\n")
    (tmp_path / "memory.swap.max").write_text("max\n")
    out = msr.read_memory_stat(tmp_path, at=10.0)
    assert out["anon"].state == ls.OK and out["anon"].value == 2147483648
    assert out["swap_current"].value == 1000
    assert out["swap_max"].state == ls.OK and out["swap_max"].value == "unlimited"


def test_reader_partial_failure_is_per_part(tmp_path):
    (tmp_path / "memory.stat").write_text("anon 5\nfile 6\n")  # shmem missing
    (tmp_path / "memory.swap.current").write_text("0\n")
    (tmp_path / "memory.swap.max").write_text("268435456\n")
    out = msr.read_memory_stat(tmp_path, at=10.0)
    assert out["anon"].state == ls.UNKNOWN          # whole memory.stat parse failed, so no part is OK
    assert out["swap_current"].state == ls.OK and out["swap_max"].value == 268435456
