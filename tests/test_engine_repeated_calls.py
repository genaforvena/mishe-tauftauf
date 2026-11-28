"""A tool-call loop is not wake progress (docs wake wake-docs-1795832242184929454, 2026-11-28).

That wake alternated two bash calls for most of its run and its output kept growing, so the idle
gap never cut it. A full window of recent calls using few distinct calls is a loop; its output is
not progress until a new kind of call breaks the window.
"""
from mishe_tauftauf import engine_boundary as boundary
from mishe_tauftauf import engine_process as proc

WINDOW = boundary.REPEAT_WINDOW


def call(command: str, call_id: str) -> bytes:
    return (f'{{"type":"tool_execution_start","toolCallId":"{call_id}","toolName":"bash",'
            f'"args":{{"command":"{command}"}}}}\n').encode()


def result(call_id: str) -> bytes:
    return f'{{"type":"tool_execution_end","toolCallId":"{call_id}","toolName":"bash"}}\n'.encode()


def test_a_short_run_of_identical_calls_still_counts_as_progress():
    repeat: dict = {}
    data = b"".join(call("git status", f"c{i}") + result(f"c{i}") for i in range(WINDOW - 1))
    assert boundary.progress_bytes(data, repeat=repeat) == (len(data), len(data))


def test_a_loop_of_identical_calls_is_not_progress():
    repeat: dict = {}
    data = b"".join(call("grep -rn x docs/", f"c{i}") + result(f"c{i}") for i in range(WINDOW + 200))
    progress, consumed = boundary.progress_bytes(data, repeat=repeat)
    assert consumed == len(data)
    assert progress < (WINDOW + 2) * len(call("grep -rn x docs/", "c0") + result("c0"))


def test_an_alternating_loop_is_not_progress():
    # The failed docs wake alternated two commands: a per-call-identical check missed it.
    repeat: dict = {}
    data = b"".join(call("git status" if i % 2 else "grep x docs/", f"c{i}") + result(f"c{i}")
                    for i in range(WINDOW + 200))
    progress, consumed = boundary.progress_bytes(data, repeat=repeat)
    assert consumed == len(data)
    assert progress < len(data) // 2


def test_varied_work_is_progress_even_with_many_calls():
    repeat: dict = {}
    data = b"".join(call(f"step {i}", f"c{i}") + result(f"c{i}") for i in range(WINDOW * 3))
    assert boundary.progress_bytes(data, repeat=repeat) == (len(data), len(data))


def test_a_new_kind_of_call_breaks_the_loop():
    repeat: dict = {}
    loop = b"".join(call("git status", f"a{i}") for i in range(WINDOW + 10))
    assert boundary.progress_bytes(loop, repeat=repeat)[0] < len(loop)
    varied = b"".join(call(f"work {i}", f"z{i}") for i in range(WINDOW))
    # The old loop stays in the window until enough new calls push it out, so the first few count late.
    progress = boundary.progress_bytes(varied, repeat=repeat)[0]
    assert progress >= len(varied) - 10 * len(call("work 0", "z0"))


def test_the_reader_keeps_the_loop_state_across_reads(tmp_path):
    path = tmp_path / "stream"
    reader = proc._ProgressReader(path)
    path.write_bytes(b"".join(call("ls", f"a{i}") for i in range(WINDOW)))
    first = reader.poll()
    path.write_bytes(path.read_bytes() + b"".join(call("ls", f"b{i}") for i in range(100)))
    second = reader.poll()
    assert second == first, "a loop continued across reads still counted as progress"


def test_without_repeat_state_the_counting_is_unchanged():
    data = b"".join(call("git status", f"c{i}") for i in range(WINDOW + 100))
    assert boundary.progress_bytes(data) == (len(data), len(data))


def test_heartbeats_are_still_not_progress_inside_a_loop():
    repeat: dict = {}
    heartbeat = b'{"type":"tool_execution_update","partialResult":{}}\n'
    data = b"".join(call("ls", f"c{i}") for i in range(WINDOW + 5)) + heartbeat
    progress, _ = boundary.progress_bytes(data, repeat=repeat)
    assert progress < len(data) - len(heartbeat)
