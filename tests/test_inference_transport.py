import sys
import time

import pytest

from mishe_tauftauf.inference_transport import NativeSession
from mishe_tauftauf.inference_worker import WorkerError


FRAME = {"terminal": "done", "assistant": {"role": "assistant",
         "stopReason": "toolUse", "content": [{"type": "toolCall",
         "id": "call_native|fc_native", "name": "inspect", "arguments": {}}]},
         "wire_usage": None, "wire_terminal": {"correlated": True}}


def command(tmp_path, *, turn=None, close=None):
    script = tmp_path / "worker.py"
    script.write_text(
        "import json,sys,time\n"
        "def emit(value): print(json.dumps(value), flush=True)\n"
        "request=json.loads(sys.stdin.readline())\n"
        "emit({'type':'ready'})\n"
        "for line in sys.stdin:\n"
        " request=json.loads(line)\n"
        " if request['type']=='turn':\n"
        + "  " + (turn or f"emit({{'type':'turn','id':request['id'],'frame':{FRAME!r}}})") + "\n"
        " elif request['type']=='close':\n"
        + "  " + (close or "emit({'type':'closed'}); sys.exit(0)") + "\n"
    )
    return [sys.executable, str(script)]


def assert_reaped(session):
    assert session.closed and session.process.poll() is not None
    assert all(stream.closed for stream in
               (session.process.stdin, session.process.stdout, session.process.stderr))
    with pytest.raises(WorkerError, match="closed"):
        session.turn({})


def test_sequential_native_turns_and_clean_disposal(tmp_path):
    with NativeSession(command(tmp_path), "fixture/model", "session", timeout=3) as session:
        first = session.turn({"messages": []})
        second = session.turn({"messages": [first.assistant]})
        assert first.assistant == second.assistant == FRAME["assistant"]
        assert first.wire_usage is None
        assert first.wire_terminal == {"correlated": True}
        assert session.next_id == 3
    assert session.process.returncode == 0
    assert_reaped(session)


@pytest.mark.parametrize("close,match", [
    ("emit({'type':'closed'}); sys.exit(7)", "failed after disposal"),
    ("sys.exit(0)", "exited|EOF"),
    ("emit({'type':'error'})", "disposal receipt"),
    ("time.sleep(30)", "time limit"),
    ("emit({'type':'closed'}); time.sleep(30)", "failed after disposal"),
])
def test_disposal_failure_invalidates_successful_turn(tmp_path, close, match):
    session = NativeSession(command(tmp_path, close=close), "fixture/model", "session", timeout=.5)
    assert session.turn({}).assistant == FRAME["assistant"]
    with pytest.raises(WorkerError, match=match):
        session.close()
    assert_reaped(session)


def test_slow_exit_after_disposal_receipt_does_not_reclassify_as_timeout(tmp_path):
    # The lifetime deadline bounds the receipt exchange, not the post-receipt
    # exit wait. A worker that emits its disposal receipt and then lingers must
    # still be reported as "failed after disposal receipt", not as a timeout.
    session = NativeSession(command(tmp_path, close=(
        "emit({'type':'closed'})\n"
        "time.sleep(3)\n"
        "sys.exit(7)"
    )), "fixture/model", "session", timeout=.5)
    assert session.turn({}).assistant == FRAME["assistant"]
    with pytest.raises(WorkerError, match="failed after disposal"):
        session.close()
    assert_reaped(session)


def test_slow_clean_exit_after_disposal_receipt_is_not_a_disposal_failure(tmp_path):
    # A worker that still leaves with status 0 within the grace bound disposed
    # cleanly: the grace bound reaps, not reports, so close() must stay quiet.
    session = NativeSession(command(tmp_path, close=(
        "emit({'type':'closed'})\n"
        "time.sleep(.5)\n"
        "sys.exit(0)"
    )), "fixture/model", "session", timeout=5, close_grace=2)
    assert session.turn({}).assistant == FRAME["assistant"]
    session.close()
    assert_reaped(session)

@pytest.mark.parametrize("turn,match", [
    ("emit({'type':'turn','id':True,'frame':{}})", "mismatched"),
    ("emit({'type':'turn','id':99,'frame':{}})", "mismatched"),
    ("print('{',flush=True)", "malformed"),
    ("sys.stdout.close(); sys.exit(0)", "exited|EOF"),
    ("print('x'*4096,file=sys.stderr,flush=True)", "output limit"),
    ("time.sleep(30)", "time limit"),
])
def test_failed_exchange_reaps_and_refuses_reuse(tmp_path, turn, match):
    session = NativeSession(command(tmp_path, turn=turn), "fixture/model", "session",
                            timeout=.5, max_output=1024)
    with pytest.raises(WorkerError, match=match):
        session.turn({})
    assert_reaped(session)


def test_completion_written_before_a_nonzero_exit_is_kept_not_refused(tmp_path):
    # A worker that writes its completion and exits nonzero in the same tick can
    # have the line land during the post-EOF drain rather than before it. That
    # line is the turn's receipt, so it must be parsed and returned; raising the
    # bare exit first would discard a completion the caller needs to reconcile.
    session = NativeSession(command(tmp_path, turn=(
        "emit({'type':'turn','id':request['id'],'frame':" + repr(FRAME) + "}); __import__('os')._exit(7)"
    )), "fixture/model", "session", timeout=3)
    turn = session.turn({})
    # The receipt won, so the caller owns the assistant text. Whether the dead
    # worker is reaped before or after the receipt is read is scheduling
    # dependent; a reaped worker makes the turn transport-limited rather than
    # 'done', because the provider state it was recorded from is no longer
    # reachable.
    assert turn.assistant == FRAME["assistant"]
    assert turn.terminal in ("done", "transport-receipt-after-exit")
    assert session.next_id == 2
    assert not session.pending
    # The worker died before answering the disposal exchange, so close() refuses
    # by whichever path that takes: the reaped exit surfaces from the drain, or
    # the write of the close request fails on a pipe the dead worker abandoned.
    with pytest.raises(WorkerError, match="exited 7|input closed"):
        session.close()
    assert_reaped(session)


def test_worker_exiting_without_writing_anything_is_still_refused(tmp_path):
    # The drain keeps only a line that actually arrived. A worker that exits
    # having written nothing must still surface the bare exit, otherwise this
    # would wait forever for a receipt that never comes.
    session = NativeSession(command(tmp_path, turn="__import__('os')._exit(7)"),
                            "fixture/model", "session", timeout=3)
    with pytest.raises(WorkerError, match="exited 7"):
        session.turn({})
    assert_reaped(session)


def test_completion_received_after_the_worker_exits_still_completes_the_turn(tmp_path, monkeypatch):
    # The completion can be read from the pipe after the worker has already exited
    # rather than merely racing it: the parent is delayed, the worker writes its
    # receipt and dies, and only then does the read happen. turn() refused that
    # receipt as "exited during turn", discarding a turn the caller was charged
    # for. A receipt for THIS turn wins; the exit stays for the next call.
    original_pump = NativeSession._pump
    original_exchange = NativeSession._exchange
    in_turn = False

    def slow_read(self, data, timeout=None, *, check=True):
        nonlocal in_turn
        # Delay only the first read inside the turn exchange, so the worker has
        # written its receipt and exited before the parent reads the pipe.
        if in_turn:
            in_turn = False
            time.sleep(0.3)
        return original_pump(self, data, timeout=timeout, check=check)

    def slow_exchange(self, request):
        nonlocal in_turn
        in_turn = request.get('type') == 'turn'
        return original_exchange(self, request)

    monkeypatch.setattr(NativeSession, "_pump", slow_read)
    monkeypatch.setattr(NativeSession, "_exchange", slow_exchange)
    session = NativeSession(command(tmp_path, turn=(
        "emit({'type':'turn','id':request['id'],'frame':" + repr(FRAME) + "});"
        " __import__('os')._exit(7)"
    )), "fixture/model", "session", timeout=3)
    turn = session.turn({})
    assert turn.assistant == FRAME["assistant"]
    assert session.next_id == 2
    # The worker is dead, so this session cannot answer another turn: the next
    # turn fails, whether it trips the exit guard or the closed input pipe.
    with pytest.raises(WorkerError, match="exited during turn|input closed"):
        session.turn({})
    assert_reaped(session)


def test_a_receipt_naming_another_turn_is_still_refused(tmp_path):
    # The receipt is what names the outcome, but only when it names THIS turn:
    # one that does not is still refused rather than laundered into a result.
    session = NativeSession(command(tmp_path, turn=(
        "emit({'type':'turn','id':request['id']+1,'frame':" + repr(FRAME) + "});"
        " __import__('os')._exit(7)"
    )), "fixture/model", "session", timeout=3)
    with pytest.raises(WorkerError, match="mismatched native turn receipt"):
        session.turn({})
    assert_reaped(session)

def test_current_cancellation_reaps_before_next_turn(tmp_path):
    cancelled = False
    session = NativeSession(command(tmp_path), "fixture/model", "session",
                            timeout=3, cancelled=lambda: cancelled)
    session.turn({})
    cancelled = True
    with pytest.raises(WorkerError, match="cancelled"):
        session.turn({})
    assert_reaped(session)


def test_concurrent_call_refused_without_killing_owner(tmp_path):
    with NativeSession(command(tmp_path), "fixture/model", "session", timeout=3) as session:
        session.lock.acquire()
        try:
            with pytest.raises(WorkerError, match="concurrent"):
                session.turn({})
            with pytest.raises(WorkerError, match="concurrent"):
                session.close()
        finally:
            session.lock.release()
        assert session.turn({}).assistant == FRAME["assistant"]
    assert_reaped(session)


@pytest.mark.parametrize("mode", ["exit", "timeout", "cancelled", "truncated"])
def test_refusal_stderr_survives_stdout_eof_and_deadline(tmp_path, mode):
    turn = (
        "__import__('os').close(1); time.sleep(.05); "
        f"sys.stderr.write({'x' * 5000 if mode == 'truncated' else ''!r} + "
        "'UNKNOWN: refused checkpoint\\n'); sys.stderr.flush(); "
        + ("sys.exit(9)" if mode == "exit" else "time.sleep(30)")
    )
    holder = {}
    session = NativeSession(
        command(tmp_path, turn=turn), "fixture/model", "session", timeout=.5,
        cancelled=lambda: mode == "cancelled" and bool(
            holder.get("session") and holder["session"].errors),
    )
    holder["session"] = session
    deadline = session.deadline
    with pytest.raises(WorkerError) as caught:
        session.turn({})
    message = str(caught.value)
    assert "UNKNOWN: refused checkpoint" in message
    if mode == "exit":
        assert message.startswith("native session exited 9:")
    elif mode == "cancelled":
        assert message.startswith("cancelled during native session: stderr:")
    else:
        assert message.startswith("native session total time limit exceeded: stderr:")
    if mode == "truncated":
        assert "[truncated]" in message and len(message) < 4200
    assert session.deadline == deadline
    assert_reaped(session)
