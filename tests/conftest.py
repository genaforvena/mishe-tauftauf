"""Every integration test uses a disposable tmux socket, never the live plant."""
import os
import subprocess

import pytest


@pytest.fixture
def receipt_recovery_case():
    """Factory: a crashed caller journal whose effect store holds a receipt.

    Experiment 4f's case. The dispatch's executor dies inside the capability, so
    the store keeps an intent and a start record with no outcome and the caller
    journal lost its tool_result row; a successor then records a receipt from the
    effect-side record. Returns the crash-state journal bytes, the store, the
    evidence artifact the receipt bound, and the call that stayed pending.
    """

    def make(tmp_path, *, call=None):
        import hashlib
        import json

        from mishe_tauftauf.inference_effects import EffectBoundary
        from mishe_tauftauf.inference_loop import (NativeJournal, drive_native,
                                                   read_native_journal)
        from mishe_tauftauf.inference_worker import WorkerTurn

        selected = dict(call or {"type": "toolCall",
                                 "id": "call_native|fc_receipt",
                                 "name": "publish",
                                 "arguments": {"target": "owned-report"}})
        context = {"messages": [{"role": "user", "content": "Publish the report"}],
                   "tools": [{"name": selected["name"]}]}

        class Session:
            def turn(self, _context):
                return WorkerTurn("done", {"role": "assistant",
                                           "content": [selected],
                                           "stopReason": "toolUse", "timestamp": 7})

        store = tmp_path / "effects"
        world = tmp_path / "world"
        world.mkdir()
        artifact = world / "effect-side-record.json"
        written = {}

        def execute(ledger_call):
            written["operation_id"] = ledger_call.get("operation_id")
            artifact.write_text(json.dumps(
                {"operation_id": written["operation_id"],
                 "kind": "owned-effect"}) + "\n", encoding="utf-8")
            raise RuntimeError("caller killed mid-effect")

        dispatch = EffectBoundary(store, writer_id="loop-1",
                                  obligation={"source": "owned-event"},
                                  execute=execute, authority="test:authority")
        path = tmp_path / "caller.jsonl"
        with NativeJournal(path) as journal:
            def record(event):
                if event["kind"] == "tool_result":
                    raise InterruptedError("crash before tool_result persisted")
                journal(event)

            with pytest.raises(InterruptedError):
                drive_native(Session(), context,
                             obligation={"source": "owned-event"}, max_turns=3,
                             max_calls=2, dispatch=dispatch, record=record,
                             phase=lambda call: None, cancelled=lambda: False,
                             authorized=lambda call: True,
                             complete=lambda context: False)

        state = read_native_journal(path)
        assert state["pending_calls"] == [selected]
        successor = EffectBoundary(store, writer_id="loop-2",
                                   obligation={"source": "owned-event"},
                                   execute=lambda call: pytest.fail("executed again"),
                                   authority="test:authority")
        assert [intent.operation_id for intent in successor.open_intents()] \
            == [written["operation_id"]] != [None]
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        receipt = successor.record_receipt(
            written["operation_id"],
            {"inspection": "committed", "operation_id": written["operation_id"],
             "executions": 1,
             "evidence": {"artifact": artifact.name, "sha256": digest}},
            evidence_root=world)
        assert receipt["status"] == "completed"

        return {"call": selected, "journal": path.read_bytes(), "store": store,
                "artifact": artifact, "digest": digest, "world": world}

    return make


@pytest.fixture(scope="session", autouse=True)
def private_tmux_server(tmp_path_factory):
    root = tmp_path_factory.mktemp("mishe-test-tmux")
    patch = pytest.MonkeyPatch()
    patch.delenv("TMUX", raising=False)
    patch.setenv("TMUX_TMPDIR", str(root))
    # Live systemd keepers belong to the plant; ordinary tests need none.
    patch.setenv("MISHE_SESSION_RUNNER", "")
    try:
        yield root
    finally:
        # Explicit socket even if a test failed to restore its environment.
        socket = root / ("tmux-" + str(os.getuid())) / "default"
        subprocess.run(["tmux", "-S", str(socket), "kill-server"],
                       capture_output=True, timeout=5)
        patch.undo()
