import json
import sys
import time

import pytest

from mishe_tauftauf.inference_worker import WorkerError, run_worker


CALL = {"type": "toolCall", "id": "call_native|fc_native", "name": "record",
        "arguments": {"text": "exact intent"}}
FRAME = {"terminal": "done", "assistant": {"role": "assistant",
         "stopReason": "toolUse", "content": [CALL],
         "providerPayload": {"opaque": "keep"}}}


def worker(tmp_path, source):
    path = tmp_path / "worker.py"
    path.write_text(source)
    return [sys.executable, str(path)]


def run(tmp_path, payload, *, exit_code=0):
    command = worker(tmp_path, f"import sys\nsys.stdin.readline()\n"
                     f"sys.stdout.write({payload!r})\nsys.exit({exit_code})\n")
    return run_worker(command, {"messages": []}, cancelled=lambda: False, timeout=2)


def test_complete_dispatch_and_current_cancellation(tmp_path):
    turn = run(tmp_path, json.dumps(FRAME))
    observed = []
    assert turn.dispatch(lambda call: observed.append(call) or "committed",
                         cancelled=lambda: False) == ["committed"]
    assert observed == [CALL]
    assert turn.assistant == FRAME["assistant"]
    assert turn.wire_usage is None
    assert turn.dispatch(lambda call: pytest.fail("cancelled effect"),
                         cancelled=lambda: True) == []


@pytest.mark.parametrize("terminal,reason", [("error", "error"), ("error", "aborted"),
                                             ("done", "length"), ("done", "stop")])
def test_retained_proposals_are_not_authority(tmp_path, terminal, reason):
    frame = {**FRAME, "terminal": terminal,
             "assistant": {**FRAME["assistant"], "stopReason": reason}}
    turn = run(tmp_path, json.dumps(frame))
    assert turn.dispatch(lambda call: pytest.fail("ineligible effect"),
                         cancelled=lambda: False) == []
    assert turn.assistant["content"] == [CALL]


@pytest.mark.parametrize("payload", ["", "{", json.dumps(FRAME) * 2,
    json.dumps({**FRAME, "assistant": {**FRAME["assistant"], "content": [CALL, CALL]}}),
    json.dumps({**FRAME, "assistant": {**FRAME["assistant"], "content": [{**CALL, "arguments": "{}"}]}})])
def test_malformed_or_missing_completion(tmp_path, payload):
    with pytest.raises(WorkerError, match="invalid worker completion"):
        run(tmp_path, payload)


def test_nonzero_exit_cannot_credit_success(tmp_path):
    with pytest.raises(WorkerError, match="exited 7"):
        run(tmp_path, json.dumps(FRAME), exit_code=7)


def test_cancellation_between_calls_does_not_retract_first(tmp_path):
    frame = {**FRAME, "assistant": {**FRAME["assistant"],
             "content": [CALL, {**CALL, "id": "second"}]}}
    turn = run(tmp_path, json.dumps(frame))
    effects = []
    turn.dispatch(effects.append, cancelled=lambda: bool(effects))
    assert effects == [CALL]


@pytest.mark.parametrize("mode", ["timeout", "cancel", "output"])
def test_bounded_cleanup(tmp_path, mode):
    command = worker(tmp_path, "import sys,time\nsys.stdin.readline()\n"
                     + ("print('x'*4096, flush=True)\n" if mode == "output" else "")
                     + "time.sleep(30)\n")
    start = time.monotonic()
    with pytest.raises(WorkerError, match={"timeout": "time limit", "cancel": "cancelled",
                                          "output": "output limit"}[mode]):
        run_worker(command, {}, timeout=.2 if mode == "timeout" else 2,
                   max_output=100 if mode == "output" else 4096,
                   cancelled=lambda: mode == "cancel" and time.monotonic() - start > .1)
    assert time.monotonic() - start < 3


@pytest.mark.parametrize("timeout,limit", [(float("nan"), 1), (float("inf"), 1),
                                          (0, 1), (1, 0), (1, True), (1, 1.5)])
def test_invalid_limits_refuse_before_start(timeout, limit):
    with pytest.raises(ValueError, match="limits required"):
        run_worker(["must-not-start"], {}, cancelled=lambda: False,
                   timeout=timeout, max_output=limit)
