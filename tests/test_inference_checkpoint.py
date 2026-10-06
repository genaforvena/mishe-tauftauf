import sys

import pytest

from mishe_tauftauf.inference_transport import NativeSession
from mishe_tauftauf.inference_worker import WorkerError


FRAME = {"terminal": "done", "assistant": {"role": "assistant",
         "stopReason": "toolUse", "content": [{"type": "toolCall",
         "id": "call_checkpoint", "name": "inspect", "arguments": {}}]}}


def command(tmp_path, *, turn):
    script = tmp_path / "checkpoint-worker.py"
    script.write_text("import json,sys\n"
        "def emit(value): print(json.dumps(value), flush=True)\n"
        "json.loads(sys.stdin.readline()); emit({'type':'ready'})\n"
        "for line in sys.stdin:\n"
        " request=json.loads(line)\n"
        " if request['type']=='turn':\n"
        f"  {turn}\n"
        " elif request['type']=='close':\n"
        "  emit({'type':'closed'}); sys.exit(0)\n")
    return [sys.executable, str(script)]


def assert_reaped(session):
    assert session.closed and session.process.poll() is not None
    assert all(stream.closed for stream in
               (session.process.stdin, session.process.stdout, session.process.stderr))
    with pytest.raises(WorkerError, match="closed"):
        session.turn({})


@pytest.mark.parametrize("checkpoint", [None, "opaque", []])
def test_completed_correlated_turn_requires_checkpoint(tmp_path, checkpoint):
    frame = {**FRAME, "checkpoint": checkpoint,
             "wire_terminal": {"event": "response.completed", "correlated": True}}
    session = NativeSession(command(tmp_path, turn=
        f"emit({{'type':'turn','id':request['id'],'frame':{frame!r}}})"),
        "fixture/model", "session", timeout=3)
    with pytest.raises(WorkerError, match="missing native logical checkpoint"):
        session.turn({})
    assert_reaped(session)


def test_error_frame_preserves_diagnostics_without_checkpoint(tmp_path):
    frame = {**FRAME, "terminal": "error",
             "assistant": {**FRAME["assistant"], "stopReason": "error"},
             "checkpoint": None,
             "wire_terminal": {"event": "response.failed", "correlated": True}}
    with NativeSession(command(tmp_path, turn=
        f"emit({{'type':'turn','id':request['id'],'frame':{frame!r}}})"),
        "fixture/model", "session", timeout=3) as session:
        turn = session.turn({})
        assert turn.terminal == "error" and turn.checkpoint is None
        assert turn.dispatch(lambda call: pytest.fail("failed stream dispatched"),
                             cancelled=lambda: False) == []
        assert turn.assistant == frame["assistant"]
    assert_reaped(session)
