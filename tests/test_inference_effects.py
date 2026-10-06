"""Durable action/effect boundary for the inference loop.
Tests the programme's Deliverable 1 requirements: allocate before dispatch,
persist intent before acting, persist outcome after, and reconcile an ambiguous
dispatch from a fresh process instead of repeating it. Interruption is exercised
with real subprocess kills, not mocked returns.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import textwrap
from pathlib import Path

import pytest


from mishe_tauftauf.inference_effects import (
    AlreadyExecuted,
    ChangedContentReuse,
    ConcurrentClaim,
    DispatchClaim,
    EffectBoundary,
    EffectIntent,
    EffectLedger,
    JournalCorrupt,
    StoreUnavailable,
    canonical_request,
    effect_outcome_for,
    request_digest,
    snapshot_json,
)

STORE = Path(__file__).resolve().parent.parent / "src"


def ledger(tmp_path, writer_id="loop-1") -> EffectLedger:
    return EffectLedger(tmp_path / "effects", writer_id=writer_id)


def allocate(led, operation_id="op-1", capability="record_marker",
             arguments=None, request=None, authority="marker_root:append-one-line",
             native_call=None):
    return led.allocate(capability, "v1", arguments if arguments is not None else {"text": "a"},
                        authority=authority,
                        request=request if request is not None else {"obligation": "ob-1"},
                        operation_id=operation_id,
                        native_call=native_call if native_call is not None else
                        {"type": "toolCall", "id": "call-7", "name": "record_marker",
                         "arguments": {"text": "a"}})


def test_allocate_persists_intent_before_any_effect(tmp_path):
    led = ledger(tmp_path)
    intent = allocate(led)
    assert led.status("op-1").status == "unknown"
    assert intent.operation_id == "op-1"
    # The intent carries the original provider call so the loop can return its id.
    assert intent.native_call["id"] == "call-7"
    # An intent exists, no effect has happened: a fresh process reads unknown.
    fresh = ledger(tmp_path)
    assert fresh.status("op-1").status == "unknown"
    assert fresh.status("op-1").failure == "unreconciled-intent"
    assert fresh.status("op-1").native_call["id"] == "call-7"


def test_allocate_is_idempotent_on_same_content_and_rejects_changed_content(tmp_path):
    led = ledger(tmp_path)
    first = allocate(led)
    again = allocate(led)
    assert again == first
    with pytest.raises(ChangedContentReuse):
        allocate(led, request={"obligation": "other"})
    with pytest.raises(ChangedContentReuse):
        allocate(led, arguments={"text": "b"})
    with pytest.raises(ChangedContentReuse):
        allocate(led, authority="marker_root:unrestricted")
    with pytest.raises(ChangedContentReuse):
        allocate(led, native_call={"type": "toolCall", "id": "call-8",
                                  "name": "record_marker", "arguments": {"text": "a"}})
    # A changed binding never wrote anything.
    assert len(led.open_intents()) == 1


def test_allocate_rejects_an_id_that_already_finished(tmp_path):
    led = ledger(tmp_path)
    intent = allocate(led)
    led.dispatch(intent, lambda call: {"ok": True})
    with pytest.raises(AlreadyExecuted):
        allocate(led)


def test_dispatch_persists_outcome_and_correlates_operation_id(tmp_path):
    led = ledger(tmp_path)
    intent = allocate(led)
    seen = []

    def executor(call):
        seen.append(call)
        return {"recorded": True}

    outcome = led.dispatch(intent, executor)
    assert (outcome.operation_id, outcome.status, outcome.result, outcome.failure) == (
        "op-1", "completed", {"recorded": True}, None)
    # Executor receives the native call shape plus the durable operation id.
    assert seen[0]["capability"] == "record_marker"
    assert seen[0]["operation_id"] == "op-1"
    assert seen[0]["id"] == "call-7"
    assert seen[0]["type"] == "toolCall"
    assert seen[0]["name"] == "record_marker"
    assert seen[0]["arguments"] == {"text": "a"}
    # Repeat dispatch returns the recorded outcome, it does not execute again.
    again = led.dispatch(intent, executor)
    assert again.status == "completed"
    assert again.result == {"recorded": True}
    assert len(seen) == 1
    assert ledger(tmp_path).dispatch(intent, executor).result == {"recorded": True}
    assert len(seen) == 1


def test_dispatch_refuses_a_started_operation_without_an_outcome(tmp_path):
    """A started-but-unrecorded outcome must not be re-executed: its effect is unknown."""
    led = ledger(tmp_path)
    intent = allocate(led)
    calls = []

    def executor(call):
        calls.append(call["operation_id"])
        raise RuntimeError("lost the receipt after the effect")

    outcome = led.dispatch(intent, executor)
    assert outcome.status == "unknown"
    assert outcome.failure == "capability-failed"
    # The successor sees a started operation with a non-completing outcome.
    fresh = ledger(tmp_path)
    again = fresh.dispatch(intent, executor)
    assert again.status == "unknown"
    assert again.failure == "started-without-outcome"
    assert len(calls) == 1, "the lost-receipt dispatch must not be repeated"
    assert fresh.reconcile("op-1").status == "unknown"
    assert fresh.open_intents() != []


def test_store_lock_rejects_overlapping_writers_in_one_process(tmp_path):
    """Distinct ledger instances in one process cannot both act."""
    first = ledger(tmp_path, writer_id="loop-1")
    second = ledger(tmp_path, writer_id="loop-2")
    with first._locked():
        with pytest.raises(StoreUnavailable):
            with second._locked():
                pass
    with second._locked():
        with pytest.raises(StoreUnavailable):
            with first._locked():
                pass
    # Releasing the lock lets the first writer take it again.
    with first._locked():
        with pytest.raises(StoreUnavailable):
            with second._locked():
                pass


def test_start_record_is_flushed_before_the_capability_runs(tmp_path):
    """The start record is durable before any effect, so a crash leaves a witness."""
    led = ledger(tmp_path)
    intent = allocate(led)

    def executor(call):
        raise RuntimeError("crash inside the capability")

    try:
        led.dispatch(intent, executor)
    except Exception:  # pragma: no cover - the ledger records, it does not raise
        pytest.fail("an executor exception is recorded, not raised")
    starts = [json.loads(line) for line in
              (led.store_dir / "starts.jsonl").read_text().splitlines() if line.strip()]
    assert starts[0]["record_type"] == "start"
    assert starts[0]["operation_id"] == "op-1"


def test_reconcile_returns_existing_outcome_without_executing(tmp_path):
    led = ledger(tmp_path)
    intent = allocate(led)
    led.dispatch(intent, lambda call: {"recorded": True})
    calls = []
    reconciled = led.reconcile("op-1")
    assert reconciled.status == "completed"
    assert reconciled.result == {"recorded": True}
    assert calls == [], "reconcile never executes"
    # Reconcile on an unknown operation reports no-intent honestly.
    assert led.reconcile("never-dispatched").status == "unknown"
    assert led.reconcile("never-dispatched").failure == "no-intent"


def test_reconcile_names_the_intent_for_an_ambiguous_dispatch(tmp_path):
    led = ledger(tmp_path)
    intent = allocate(led)
    fresh = ledger(tmp_path, writer_id="successor-1")
    result = fresh.reconcile("op-1")
    assert result.status == "unknown"
    assert result.failure == "unreconciled-intent"
    assert intent.operation_id in (result.reconciliation_ref or "")
    # The successor cannot replay the ambiguous operation.
    again = fresh.dispatch(intent, lambda call: {"recorded": True})
    assert again.status == "unknown"
    assert again.failure == "concurrent-claim"


def test_concurrent_claim_from_a_second_writer_is_reported_not_executed(tmp_path):
    led = ledger(tmp_path, writer_id="loop-1")
    intent = allocate(led)
    calls = []
    other = ledger(tmp_path, writer_id="loop-2")
    outcome = other.dispatch(intent, lambda call: calls.append(call) or {"ok": True})
    assert outcome.status == "unknown"
    assert outcome.failure == "concurrent-claim"
    assert calls == [], "a second writer must not execute another writer's operation"
    assert led.status("op-1").status == "unknown"


def test_store_lock_rejects_overlapping_writers_in_one_process(tmp_path):
    """Distinct ledger instances in one process cannot both act."""
    first = ledger(tmp_path, writer_id="loop-1")
    second = ledger(tmp_path, writer_id="loop-2")
    with first._locked():
        with pytest.raises(StoreUnavailable):
            with second._locked():
                pass
    with second._locked():
        with pytest.raises(StoreUnavailable):
            with first._locked():
                pass


def test_status_unknown_for_unallocated_operation(tmp_path):
    assert ledger(tmp_path).status("never-allocated") is None


def test_request_digest_binds_content_authority_and_call_identity(tmp_path):
    led = ledger(tmp_path)
    base = {"obligation": "ob-1"}
    a = allocate(led, operation_id="op-1", request=base)
    b = allocate(led, operation_id="op-2", request={"obligation": "ob-2"})
    c = allocate(led, operation_id="op-3", request=base, native_call={
        "type": "toolCall", "id": "call-8", "name": "record_marker",
        "arguments": {"text": "a"}})
    assert a.request_digest != b.request_digest
    assert a.request_digest != c.request_digest
    same = allocate(led, operation_id="op-4", request=base)
    assert same.request_digest == a.request_digest

def test_request_digest_binds_the_capability_version(tmp_path):
    led = ledger(tmp_path)
    base = {"obligation": "ob-1"}
    a = allocate(led, operation_id="op-1", request=base)
    same = allocate(led, operation_id="op-2", request=base)
    assert same.request_digest == a.request_digest
    # A same-id rebind under a different capability version is different content,
    # not the same operation replayed at another version.
    other = led.allocate("record_marker", "v2", {"text": "a"},
                         authority="marker_root:append-one-line",
                         request=base, operation_id="op-3")
    assert other.request_digest != a.request_digest
    # An empty version is bound like any other value, so the digest never
    # silently treats an unset version as interchangeable with a set one.
    explicit = request_digest(base, "marker_root:append-one-line",
                              capability="record_marker", capability_version="v1")
    defaulted = request_digest(base, "marker_root:append-one-line",
                               capability="record_marker")
    assert explicit != defaulted


def test_arguments_are_snapshotted_so_mutation_cannot_change_the_binding(tmp_path):
    led = ledger(tmp_path)
    args = {"text": "a", "nested": {"deep": [1, 2]}}
    native = {"type": "toolCall", "id": "call-7", "name": "record_marker",
              "arguments": {"text": "a", "nested": {"deep": [1, 2]}}}
    intent = allocate(led, arguments=args, native_call=native)
    args["text"] = "mutated"
    args["nested"]["deep"].append(3)
    assert intent.arguments == {"nested": {"deep": [1, 2]}, "text": "a"}
    again = allocate(led, operation_id="op-2",
                     arguments={"text": "a", "nested": {"deep": [1, 2]}},
                     native_call=native)
    assert again.request_digest == intent.request_digest


def test_noncanonical_values_are_rejected(tmp_path):
    led = ledger(tmp_path)
    with pytest.raises(ValueError):
        allocate(led, request={"bad": float("inf")})
    with pytest.raises(ValueError):
        allocate(led, request={"bad": float("nan")})
    with pytest.raises(ValueError):
        allocate(led, request={"bad": object()})


def test_records_are_append_only_jsonl_with_one_writer(tmp_path):
    led = ledger(tmp_path)
    intent = allocate(led)
    led.dispatch(intent, lambda call: {"ok": True})
    intents = [json.loads(line) for line in
               (tmp_path / "effects" / "intents.jsonl").read_text().splitlines() if line.strip()]
    assert intents[0]["record_type"] == "intent"
    assert intents[0]["writer_id"] == "loop-1"
    assert intents[0]["native_call"]["id"] == "call-7"
    outcomes = [json.loads(line) for line in
                (tmp_path / "effects" / "outcomes.jsonl").read_text().splitlines()
                if line.strip()]
    assert outcomes[0]["record_type"] == "outcome"
    assert outcomes[0]["status"] == "completed"
    assert outcomes[0]["result"] == {"ok": True}


def test_dispatch_carries_the_native_call_shape_to_the_executor(tmp_path):
    """The provider's type/id/name/arguments reach the executor, plus our identity."""
    led = ledger(tmp_path)
    native = {"type": "toolCall", "id": "call-9", "name": "record_marker",
              "arguments": {"text": "a"}}
    intent = allocate(led, operation_id="op-native", native_call=native)
    seen = []
    led.dispatch(intent, lambda call: seen.append(call) or {"ok": True})
    assert seen[0]["type"] == "toolCall"
    assert seen[0]["native_call"]["type"] == "toolCall"
    assert seen[0]["native_call"]["id"] == "call-9"
    assert seen[0]["native_call"]["name"] == "record_marker"
    assert seen[0]["native_call"]["arguments"] == {"text": "a"}
    assert seen[0]["operation_id"] == "op-native"
    assert seen[0]["capability_version"] == "v1"
    assert seen[0]["arguments"] == {"text": "a"}
def test_a_writer_id_is_required(tmp_path):
    with pytest.raises(ValueError):
        EffectLedger(tmp_path / "effects", writer_id="")


def test_interruption_between_start_and_outcome_leaves_unknown_and_no_rerun(tmp_path):
    """A real process is killed after the start record; a successor never re-executes.

    The child allocates, takes the store lock, writes the start record and then
    blocks inside the capability until it is killed. No outcome can exist. A
    fresh process must read started-without-outcome and refuse a second effect.
    """
    store = tmp_path / "effects"
    script = tmp_path / "child.py"
    script.write_text(textwrap.dedent(
        """
        import json, sys, time
        from pathlib import Path
        sys.path.insert(0, %r)
        from mishe_tauftauf.inference_effects import (DispatchClaim, EffectIntent,
                                                     EffectLedger, request_digest)

        store = Path(%r)
        led = EffectLedger(store, writer_id="child")
        # `_locked` is exclusive and not reentrant, so the intent is built by
        # hand inside the held lock; the kill lands between start and outcome.
        with led._locked():
            intent = EffectIntent(
                operation_id="op-kill", capability="record_marker",
                capability_version="v1", arguments={"text": "a"},
                request_digest=request_digest(
                    {"obligation": "ob-1"}, "marker_root:append-one-line",
                    arguments={"text": "a"}, capability="record_marker"),
                authority="marker_root:append-one-line", writer_id="child",
                budget=None, native_call=None)
            led._append(led.intents_path, intent.to_record())
            led._append(led.starts_path, DispatchClaim(
                operation_id="op-kill", writer_id="child",
                started_at=time.time()).to_record())
            print(json.dumps({"started": True}), flush=True)
            while True:
                time.sleep(0.05)
        """
        % (str(STORE), str(store))))
    child = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    try:
        assert json.loads(child.stdout.readline())["started"] is True
    finally:
        child.kill()
        child.wait(timeout=10)

    # The lock died with the process, so a successor can take the store again.
    successor = EffectLedger(store, writer_id="successor")
    assert (store / "starts.jsonl").read_text().strip(), "the start record survived"
    assert (store / "intents.jsonl").read_text().strip(), "the intent survived"
    result = successor.reconcile("op-kill")
    assert result.status == "unknown"
    assert result.failure == "started-without-outcome"
    # Re-dispatch is refused: the lost receipt is unknown, not permission to
    # retry. The child's intent is already durable, so the successor dispatches
    # the record it read rather than allocating a second binding.
    recovered = successor._read_intents()["op-kill"]
    again = successor.dispatch(recovered, lambda call: {"recorded": True})
    assert again.status == "unknown"
    assert again.failure == "concurrent-claim"
    assert successor.open_intents() != []

def test_concurrent_writer_in_another_process_cannot_act(tmp_path):
    """One process holds the store while another's dispatch fails without touching it."""
    store = tmp_path / "effects"
    script = tmp_path / "holder.py"
    script.write_text(textwrap.dedent(
        """
        import json, sys, time
        from pathlib import Path
        sys.path.insert(0, %r)
        from mishe_tauftauf.inference_effects import EffectLedger

        store = Path(%r)
        led = EffectLedger(store, writer_id="holder")
        # Hold the exclusive lock first: the peer cannot even read the store.
        with led._locked():
            print(json.dumps({"holding": True}), flush=True)
            while True:
                time.sleep(0.05)
        """
        % (str(STORE), str(store))))
    holder = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        assert json.loads(holder.stdout.readline())["holding"]
        other = EffectLedger(store, writer_id="other")
        # The child prints only after taking the lock, but the flock is a
        # separate syscall; retry until the store is genuinely unavailable.
        deadline = time.time() + 5
        while True:
            try:
                other.allocate("record_marker", "v1", {"text": "a"},
                               authority="marker_root:append-one-line",
                               request={"obligation": "ob-1"}, operation_id="op-other")
            except StoreUnavailable:
                break
            assert time.time() < deadline, "the holder never took the store lock"
            time.sleep(0.02)
        with pytest.raises(StoreUnavailable):
            other.allocate("record_marker", "v1", {"text": "a"},
                           authority="marker_root:append-one-line",
                           request={"obligation": "ob-1"}, operation_id="op-other")
        # Nothing was written by the losing writer.
        outcomes = (store / "outcomes.jsonl").read_text().strip()
        assert outcomes == ""
    finally:
        os.killpg(holder.pid, 9)
        holder.wait(timeout=10)


def test_a_truncated_or_malformed_journal_fails_closed(tmp_path):
    """Damage is preserved and reported; no dispatch is based on a half-readable store."""
    led = ledger(tmp_path)
    intent = allocate(led)
    led.dispatch(intent, lambda call: {"ok": True})
    outcomes = led.store_dir / "outcomes.jsonl"
    outcomes.write_text('{"record_type": "outcome", "operation_id": "op-2",\n'
                        '"status": "completed", "result": null, "failure": null,\n'
                        '"executions": 1, "reconciliation_ref": null, "native_call": null}\n'
                        '{"record_type": "outcome", "operat')
    broken = ledger(tmp_path)
    with pytest.raises(JournalCorrupt):
        broken.status("op-1")
    with pytest.raises(JournalCorrupt):
        broken.dispatch(intent, lambda call: {"recorded": True})
    with pytest.raises(JournalCorrupt):
        broken.reconcile("op-1")
    with pytest.raises(JournalCorrupt):
        broken.open_intents()
    # The bytes remain as evidence.
    assert "operat" in outcomes.read_text()


def test_a_torn_line_is_reported_by_its_real_partial_record(tmp_path):
    """A torn write is named by the bytes that lost their terminator.

    A canonical record may contain the JSON escape `\\n` inside a string value,
    so the diagnostic must split on the real newline byte. Searching for the
    escape instead reports every record since an escaped newline as one
    fragment, hiding the partial record it exists to name. The operation is
    reserved, not completed, so its status is read from the intents journal
    the tear is written into rather than short-circuited by an outcome.
    """
    led = ledger(tmp_path)
    led.reserve("record_marker", "v1", {"text": "first\\nsecond"},
                authority="marker_root:append-one-line",
                operation_id="op-1")
    intents = led.store_dir / "intents.jsonl"
    # The first record is complete and terminated; the second lost its tail.
    torn = intents.read_bytes() + b'{"record_type": "intent", "operation_id": "op-2"'
    intents.write_bytes(torn)
    with pytest.raises(JournalCorrupt) as excinfo:
        ledger(tmp_path).status("op-1")
    message = str(excinfo.value)
    assert message.endswith(repr(b'{"record_type": "intent", "operation_id": "op-2"'))
    # The intact record is not dragged into the diagnostic.
    assert "first\\nsecond" not in message
    # The evidence is preserved untouched.
    assert torn == intents.read_bytes()


def test_an_escaped_newline_in_a_value_is_not_a_torn_line(tmp_path):
    """A canonical record containing `\\n` reads back cleanly."""
    led = ledger(tmp_path)
    allocate(led, arguments={"text": "a\\nb"})
    fresh = ledger(tmp_path)
    assert fresh.status("op-1").status == "unknown"
    assert fresh._read_intents()["op-1"].arguments == {"text": "a\\nb"}


def test_a_duplicated_record_fails_closed(tmp_path):
    led = ledger(tmp_path)
    allocate(led)
    intents = led.store_dir / "intents.jsonl"
    intents.write_text(intents.read_text() + intents.read_text())
    with pytest.raises(JournalCorrupt):
        ledger(tmp_path).status("op-1")


def test_an_unexpected_record_type_fails_closed(tmp_path):
    led = ledger(tmp_path)
    intents = led.store_dir / "intents.jsonl"
    intents.write_text('{"record_type": "something-else", "operation_id": "op-1"}\n')
    with pytest.raises(JournalCorrupt):
        ledger(tmp_path).status("op-1")


def test_dispatch_rejects_an_intent_from_another_store(tmp_path):
    led = ledger(tmp_path)
    other = ledger(tmp_path / "elsewhere")
    intent = allocate(other)
    with pytest.raises(ValueError):
        led.dispatch(intent, lambda call: {"ok": True})


def test_completed_operation_survives_restart_and_is_returned_as_recorded(tmp_path):
    led = ledger(tmp_path)
    intent = allocate(led)
    led.dispatch(intent, lambda call: {"recorded": True, "seq": 12})
    fresh = ledger(tmp_path)
    assert fresh.status("op-1").result == {"recorded": True, "seq": 12}
    assert fresh.reconcile("op-1").result == {"recorded": True, "seq": 12}
    assert fresh.open_intents() == []


# -- loop-boundary surface -------------------------------------------------

NATIVE_CALL = {"type": "toolCall", "id": "call_native|fc_native", "name": "inspect",
               "arguments": {"path": "owned-evidence"}}


def boundary(tmp_path, execute, **kwargs):
    options = dict(writer_id="loop-1", obligation={"source": "owned-event"},
                   authority="marker_root:read-owned-evidence")
    options.update(kwargs)
    return EffectBoundary(tmp_path / "effects", execute=execute, **options)


def test_boundary_replies_in_the_shape_drive_native_accepts(tmp_path):
    """The loop requires exactly one of four statuses, or it raises."""
    seen = []
    reply = boundary(tmp_path, lambda call: seen.append(call) or {"ok": True})(NATIVE_CALL)
    assert set(reply) == {"status", "result", "operation_id", "failure",
                          "reconciliation_ref", "executions"}
    assert reply["status"] == "completed"
    assert reply["result"] == {"ok": True}
    assert reply["operation_id"] == "effect-call_native-fc_native-d3129ff9ce3f"
    assert reply["failure"] is None
    assert reply["executions"] == 1
    assert seen, "the capability actually ran"
    assert seen[0]["type"] == "toolCall"
    assert seen[0]["id"] == "call_native|fc_native"
    assert seen[0]["name"] == "inspect"
    assert seen[0]["native_call"] == NATIVE_CALL
    assert seen[0]["capability"] == "inspect"
    assert seen[0]["arguments"] == {"path": "owned-evidence"}
    assert seen[0]["authority"] == "marker_root:read-owned-evidence"
    assert seen[0]["operation_id"] == "effect-call_native-fc_native-d3129ff9ce3f"


def test_boundary_and_drive_native_end_to_end(tmp_path):
    """The boundary is drive_native's dispatch callback: exactly one execution."""
    from mishe_tauftauf.inference_loop import NativeJournal, drive_native
    from mishe_tauftauf.inference_worker import WorkerTurn

    calls = []
    context = {"messages": [{"role": "user", "content": "Inspect evidence"}],
               "tools": [{"name": "inspect"}]}
    first = {"type": "toolCall", "id": "call_native|fc_native", "name": "inspect",
             "arguments": {"path": "owned-evidence"}}
    second = {"type": "toolCall", "id": "call_two|fc_native", "name": "inspect",
              "arguments": {"path": "owned-evidence"}}
    emitted = []

    class ToolSession:
        def __init__(self):
            self.inputs = []

        def turn(self, context):
            self.inputs.append(context)
            # A distinct second call id reaches turn_budget at the loop bound
            # rather than completing, so no third model turn is needed.
            return WorkerTurn("done", {"role": "assistant",
                                       "content": [first] if len(self.inputs) < 2
                                       else [second],
                                       "stopReason": "toolUse",
                                       "providerPayload": {"opaque": "retained"}},
                              None)

    def record(event):
        emitted.append(event["kind"])
        journal(event)

    with NativeJournal(tmp_path / "caller.jsonl") as journal:
        result = drive_native(ToolSession(), context, obligation={"source":
                               "owned-event"}, max_turns=2, max_calls=2,
                              dispatch=boundary(tmp_path,
                                                lambda call: calls.append(call)
                                                or {"lines": 1}),
                              record=record, phase=lambda call: None,
                              cancelled=lambda: False,
                              authorized=lambda call: True,
                              complete=lambda context: True)
    assert result["status"] == "turn_budget"
    assert result["calls"] == 2
    assert len(calls) == 2, "each distinct provider call ran exactly once"
    assert emitted[:1] == ["checkpoint"]
    assert emitted.count("proposal") == 2
    assert emitted.count("tool_result") == 2
    assert [c["operation_id"] for c in calls] == [
        "effect-call_native-fc_native-d3129ff9ce3f",
        "effect-call_two-fc_native-342800ae9ac1"]
    assert calls[-1]["native_call"]["id"] == second["id"]
    # The durable store holds both effects, and neither is open.
    fresh = boundary(tmp_path, lambda call: pytest.fail("unexpected execution"),
                     writer_id="loop-2")
    assert fresh.status("effect-call_native-fc_native-d3129ff9ce3f")["status"] == (
        "completed")
    assert fresh.status("effect-call_two-fc_native-342800ae9ac1")["status"] == (
        "completed")
    assert fresh.open_intents() == []


def test_boundary_never_executes_a_completed_operation_again(tmp_path):
    """A repeated dispatch is refused, never re-executed."""
    runs = []
    dispatch = boundary(tmp_path, lambda call: runs.append(1) or {"ok": True})
    first = dispatch(NATIVE_CALL)
    assert first["status"] == "completed"
    with pytest.raises(AlreadyExecuted):
        dispatch(NATIVE_CALL)
    assert len(runs) == 1
    # A fresh process reads the one recorded outcome.
    fresh = boundary(tmp_path, lambda call: pytest.fail("unexpected execution"),
                     writer_id="loop-2")
    assert fresh.recover(NATIVE_CALL) == first


def test_boundary_changed_arguments_are_rejected_before_any_effect(tmp_path):
    runs = []
    dispatch = boundary(tmp_path, lambda call: runs.append(1) or {"ok": True})
    first = dispatch(NATIVE_CALL)
    assert first["status"] == "completed"
    # The same id with different arguments would execute a different operation
    # under one durable name; the store refuses it before any new effect.
    changed = {**NATIVE_CALL, "arguments": {"path": "other-evidence"}}
    with pytest.raises(AlreadyExecuted):
        dispatch(changed)
    assert len(runs) == 1


def test_boundary_changed_authority_is_refused_after_completion(tmp_path):
    """Authority is part of the digest, so a completed binding is not rebindable."""
    one = boundary(tmp_path, lambda call: {"where": "one"})
    named = {**NATIVE_CALL, "operation_id": "inspect-evidence-v1"}
    assert one(named)["status"] == "completed"
    two = boundary(tmp_path, lambda call: {"where": "two"},
                   authority="marker_root:read-anywhere")
    # An outcome already exists for this id, so the store refuses any reuse,
    # whatever the new authority: the completed effect is not rebindable.
    with pytest.raises(AlreadyExecuted):
        two(named)
    assert one.status("inspect-evidence-v1")["result"] == {"where": "one"}


def test_boundary_binds_arguments_at_call_time_not_use_time(tmp_path):
    dispatch = boundary(tmp_path, lambda call: {"ok": True})
    mutable = {"path": "owned-evidence"}
    call = {"type": "toolCall", "id": "call-mut", "name": "inspect",
            "arguments": mutable}
    first = dispatch(call)
    assert (tmp_path / "effects" / "intents.jsonl").read_text()
    # The recorded digest cannot be altered by mutating the caller's dict.
    second = boundary(tmp_path, lambda call: {"ok": True}, writer_id="loop-2")
    again = second.recover(call)
    assert again["operation_id"] == first["operation_id"]
    assert again["status"] == "completed"
    assert again["result"] == {"ok": True}


def test_boundary_noncanonical_result_raises_before_the_outcome_is_written(tmp_path):
    store = tmp_path / "effects"
    dispatch = boundary(tmp_path, lambda call: {"bad": object()})
    with pytest.raises(StoreUnavailable):
        dispatch(NATIVE_CALL)
    assert (store / "outcomes.jsonl").read_text().strip() == ""
    assert (store / "starts.jsonl").read_text().strip() != ""
    assert dispatch.recover(NATIVE_CALL)["status"] == "unknown"


def test_boundary_start_without_outcome_is_unknown_to_a_successor(tmp_path):
    """A real process is killed mid-execution; no successor reruns it."""
    store = tmp_path / "effects"
    script = tmp_path / "child.py"
    script.write_text(textwrap.dedent(
        """
        import json, sys, time
        from pathlib import Path
        sys.path.insert(0, %r)
        from mishe_tauftauf.inference_effects import EffectBoundary
        NATIVE_CALL = {"type": "toolCall", "id": "call_native|fc_native",
                       "name": "inspect", "arguments": {"path": "owned-evidence"}}
        started = []
        boundary = EffectBoundary(Path(%r), writer_id="loop-1",
                                  execute=lambda call: (
                                      print(json.dumps({"started": True}), flush=True),
                                      time.sleep(600))[1],
                                  obligation={"source": "owned-event"},
                                  authority="marker_root:read-owned-evidence")
        started.append(boundary(NATIVE_CALL))
        print(json.dumps({"outcome": started[0]["status"]}), flush=True)
        time.sleep(600)
        """
        % (str(STORE), str(store))))
    child = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
    try:
        line = child.stdout.readline()
        if not line:
            raise AssertionError(
                f"child produced no receipt; stderr:\n{child.stderr.read()}")
        assert json.loads(line) == {"started": True}
    finally:
        child.kill()
        child.wait(timeout=10)
    successor = boundary(tmp_path, lambda call: pytest.fail("redispatched"),
                         writer_id="loop-2")
    state = successor.recover(NATIVE_CALL)
    assert state["status"] == "unknown"
    assert state["failure"] == "started-without-outcome"
    assert (store / "starts.jsonl").read_text().strip() != ""
    assert (store / "outcomes.jsonl").read_text().strip() == ""


def test_boundary_recover_reports_a_call_that_never_arrived(tmp_path):
    dispatch = boundary(tmp_path, lambda call: {"ok": True})
    assert dispatch.recover(NATIVE_CALL) is None
    assert dispatch.status("effect-never-issued") is None
    assert dispatch.reconcile("effect-never-issued")["status"] == "unknown"



def test_boundary_reserve_binds_an_identity_before_any_effect(tmp_path):
    """A reserved operation is durable with no start record and no execution."""
    store = tmp_path / "effects"
    dispatch = boundary(tmp_path, lambda call: {"ok": True})
    reserved = dispatch.reserve("report-37233-v1", capability="publish-owned-report",
                                arguments={"target": "owned-evidence"})
    assert reserved.operation_id == "report-37233-v1"
    assert reserved.capability == "publish-owned-report"
    assert reserved.arguments == {"target": "owned-evidence"}
    # A reservation wrote an intent and nothing else.
    assert (store / "intents.jsonl").read_text().strip()
    assert (store / "starts.jsonl").read_text().strip() == ""
    assert (store / "outcomes.jsonl").read_text().strip() == ""
    # No capability has run: this state is unknown, not completed.
    state = dispatch.status("report-37233-v1")
    assert state["status"] == "unknown"
    assert state["failure"] == "unreconciled-intent"
    assert dispatch.recover({"id": "none"}) is None


def test_boundary_reserve_then_dispatch_executes_once(tmp_path):
    """A reserved id dispatches through the native call and runs at most once."""
    seen = []
    dispatch = boundary(tmp_path, lambda call: seen.append(call) or {"ok": True})
    dispatch.reserve("report-37233-v1", capability="publish-owned-report",
                     arguments={"target": "owned-evidence"})
    call = {"type": "toolCall", "id": "call-11", "name": "publish-owned-report",
            "arguments": {"target": "owned-evidence"},
            "operation_id": "report-37233-v1"}
    reply = dispatch(call)
    assert reply["status"] == "completed"
    assert reply["operation_id"] == "report-37233-v1"
    assert len(seen) == 1
    assert seen[0]["operation_id"] == "report-37233-v1"
    # A same-id repeat after completion is refused, not re-executed.
    with pytest.raises(AlreadyExecuted):
        dispatch(call)
    assert len(seen) == 1


def test_boundary_reserve_rejects_changed_content_before_any_effect(tmp_path):
    dispatch = boundary(tmp_path, lambda call: {"ok": True})
    dispatch.reserve("report-37233-v1", capability="publish-owned-report",
                     arguments={"target": "owned-evidence"})
    with pytest.raises(ChangedContentReuse):
        dispatch.reserve("report-37233-v1", capability="publish-owned-report",
                         arguments={"target": "other-evidence"})
    with pytest.raises(ChangedContentReuse):
        dispatch.reserve("report-37233-v1", capability="inspect",
                         arguments={"target": "owned-evidence"})
    assert dispatch.open_intents()[0].operation_id == "report-37233-v1"

def test_boundary_a_reserved_id_is_not_free_for_a_changed_capability_version(tmp_path):
    """A reserved id dispatched at another version never starts an effect.

    The version is bound in the digest, so a dispatch that arrives with a
    different `capability_version` is changed content: it is refused before any
    effect rather than silently upgrading the reservation to the new version.
    """
    seen = []
    dispatch = boundary(tmp_path, lambda call: seen.append(call) or {"ok": True})
    dispatch.reserve("report-37233-v1", capability="publish-owned-report",
                     arguments={"target": "owned-evidence"})
    call = {"type": "toolCall", "id": "call-12", "name": "publish-owned-report",
            "arguments": {"target": "owned-evidence"},
            "operation_id": "report-37233-v1"}
    with pytest.raises(ChangedContentReuse):
        boundary(tmp_path, lambda call: seen.append(call) or {"ok": True},
                 capability_version="v2")(call)
    assert seen == []
    # The reservation is untouched, still reconcilable at its own version.
    intent = dispatch.open_intents()[0]
    assert (intent.capability_version, intent.reservation) == ("v1", True)


def test_boundary_a_dispatched_id_is_not_free_for_a_changed_capability_version(tmp_path):
    """A bound id rebound at another version is refused, not replayed.

    Covers the post-binding path: once the id is bound, a second dispatch under
    a different version must fail the digest comparison instead of executing at
    the recorded version. Uses a capability that failed, so a start record
    exists with no outcome and the digest comparison in `bind_dispatch` is the
    one that fires. Nothing is replayed, and the one start record stands.
    """
    seen = []

    def failing(call):
        seen.append(call)
        raise RuntimeError("the capability failed")

    dispatch = boundary(tmp_path, failing)
    state = dispatch(NATIVE_CALL)
    assert (state["status"], state["failure"]) == ("unknown", "capability-failed")
    assert len(seen) == 1
    with pytest.raises(ChangedContentReuse):
        boundary(tmp_path, failing, capability_version="v2")(NATIVE_CALL)
    assert len(seen) == 1
    store = tmp_path / "effects"
    starts = [json.loads(line) for line in store.joinpath("starts.jsonl").read_text().splitlines()
              if line.strip()]
    assert len(starts) == 1
    assert dispatch.status(state["operation_id"])["failure"] == "started-without-outcome"


def test_boundary_reserve_then_dispatch_binds_the_same_version(tmp_path):
    """A reservation and a matching dispatch at one version upgrade cleanly."""
    seen = []
    dispatch = boundary(tmp_path, lambda call: seen.append(call) or {"ok": True})
    dispatch.reserve("report-37233-v1", capability="publish-owned-report",
                     arguments={"target": "owned-evidence"})
    call = {"type": "toolCall", "id": "call-13", "name": "publish-owned-report",
            "arguments": {"target": "owned-evidence"},
            "operation_id": "report-37233-v1"}
    reply = dispatch(call)
    assert reply["status"] == "completed"
    assert seen[0]["capability_version"] == "v1"
    assert dispatch.open_intents() == []


def test_boundary_reserve_is_durable_across_a_process_restart(tmp_path):
    """A fresh process reads a reservation as unknown and can then dispatch it."""
    store = tmp_path / "effects"
    dispatch = boundary(tmp_path, lambda call: {"ok": True})
    dispatch.reserve("report-37233-v1", capability="publish-owned-report",
                     arguments={"target": "owned-evidence"})
    fresh = boundary(tmp_path, lambda call: {"ok": True}, writer_id="loop-2")
    state = fresh.status("report-37233-v1")
    assert (state["status"], state["failure"]) == ("unknown", "unreconciled-intent")
    assert fresh.open_intents()[0].native_call is None
    # The reservation survives, so the id is not free for changed content.
    with pytest.raises(ChangedContentReuse):
        fresh.reserve("report-37233-v1", capability="publish-owned-report",
                      arguments={"target": "other-evidence"})
    assert fresh.open_intents()[0].writer_id == "loop-1"


def test_boundary_a_reserved_id_is_not_free_for_a_changed_native_call(tmp_path):
    """Dispatching a reserved id with other arguments never starts an effect."""
    seen = []
    dispatch = boundary(tmp_path, lambda call: seen.append(call) or {"ok": True})
    dispatch.reserve("report-37233-v1", capability="publish-owned-report",
                     arguments={"target": "owned-evidence"})
    foreign = {"type": "toolCall", "id": "call-9", "name": "publish-owned-report",
               "arguments": {"target": "other-evidence"},
               "operation_id": "report-37233-v1"}
    with pytest.raises(ChangedContentReuse):
        dispatch(foreign)
    assert seen == []
    assert dispatch.status("report-37233-v1")["failure"] == "unreconciled-intent"


def test_boundary_claim_for_runs_a_reserved_id_from_a_provider_call(tmp_path):
    """A provider call can claim a caller-reserved id without carrying it.

    `drive_native` forwards the provider's call untouched, so the only route by
    which a caller-named identity reaches the loop is an explicit claim. The
    reserved id is upgraded and runs once under the caller's name; a derived id
    is never created, so nothing is left unreconciled.
    """
    seen = []
    dispatch = boundary(tmp_path, lambda call: seen.append(call) or {"ok": True})
    dispatch.reserve("report-37233-v1", capability="publish-owned-report",
                     arguments={"target": "owned-evidence"})
    call = {"type": "toolCall", "id": "call-provider", "name": "publish-owned-report",
            "arguments": {"target": "owned-evidence"}}
    reply = dispatch(call, claim_for="report-37233-v1")
    assert (reply["status"], reply["operation_id"]) == ("completed", "report-37233-v1")
    assert len(seen) == 1
    assert seen[0]["operation_id"] == "report-37233-v1"
    assert seen[0]["native_call"]["id"] == "call-provider"
    # No second, derived operation was left behind.
    assert dispatch.open_intents() == []
    assert dispatch.status("report-37233-v1")["status"] == "completed"
    # The claimed id is now subject to the same at-most-once rule.
    with pytest.raises(AlreadyExecuted):
        dispatch(call, claim_for="report-37233-v1")
    assert len(seen) == 1
    fresh = boundary(tmp_path, lambda call: pytest.fail("unexpected execution"),
                     writer_id="loop-2")
    # A fresh process reconciles by the caller's id, which is what was claimed.
    assert fresh.status("report-37233-v1")["status"] == "completed"


def test_boundary_claim_for_refuses_changed_content_before_any_effect(tmp_path):
    """A claim binds the reservation's content, so a mismatch never executes."""
    seen = []
    dispatch = boundary(tmp_path, lambda call: seen.append(call) or {"ok": True})
    dispatch.reserve("report-37233-v1", capability="publish-owned-report",
                     arguments={"target": "owned-evidence"})
    wrong_args = {"type": "toolCall", "id": "call-1", "name": "publish-owned-report",
                  "arguments": {"target": "other-evidence"}}
    with pytest.raises(ChangedContentReuse):
        dispatch(wrong_args, claim_for="report-37233-v1")
    wrong_capability = {"type": "toolCall", "id": "call-2", "name": "inspect",
                        "arguments": {"target": "owned-evidence"}}
    with pytest.raises(ChangedContentReuse):
        dispatch(wrong_capability, claim_for="report-37233-v1")
    assert seen == []
    assert dispatch.status("report-37233-v1")["failure"] == "unreconciled-intent"


def test_boundary_claim_for_a_changed_capability_version_is_refused(tmp_path):
    """A claim at another version never starts an effect.

    The version is bound in the digest, so a boundary pinned to `v2` claiming a
    `v1` reservation is changed content, refused before any effect. The
    reservation is left untouched at its own version.
    """
    seen = []
    dispatch = boundary(tmp_path, lambda call: seen.append(call) or {"ok": True})
    dispatch.reserve("report-37233-v1", capability="publish-owned-report",
                     arguments={"target": "owned-evidence"})
    call = {"type": "toolCall", "id": "call-3", "name": "publish-owned-report",
            "arguments": {"target": "owned-evidence"}}
    with pytest.raises(ChangedContentReuse):
        boundary(tmp_path, lambda call: seen.append(call) or {"ok": True},
                 capability_version="v2")(call, claim_for="report-37233-v1")
    assert seen == []
    intent = dispatch.open_intents()[0]
    assert (intent.capability_version, intent.reservation) == ("v1", True)


def test_boundary_claim_for_requires_a_real_reservation(tmp_path):
    """A claim on an absent or already-dispatched id is refused before any effect."""
    seen = []
    dispatch = boundary(tmp_path, lambda call: seen.append(call) or {"ok": True})
    call = {"type": "toolCall", "id": "call-4", "name": "inspect",
            "arguments": {"path": "owned-evidence"}}
    # Nothing was ever reserved under this id.
    with pytest.raises(ValueError):
        dispatch(call, claim_for="report-never-reserved-v1")
    # Claiming an id that already completed is a duplicate dispatch: refused
    # as `AlreadyExecuted`, never a second execution.
    first = dispatch(call)
    assert first["status"] == "completed"
    with pytest.raises(AlreadyExecuted):
        dispatch(call, claim_for=first["operation_id"])
    with pytest.raises(ValueError):
        dispatch(call, claim_for="bad id!")
    assert len(seen) == 1
    assert dispatch.open_intents() == []


def test_boundary_claim_for_survives_a_process_restart(tmp_path):
    """A reservation is claimable by a fresh process that never saw the reserve."""
    store = tmp_path / "effects"
    dispatch = boundary(tmp_path, lambda call: {"ok": True})
    dispatch.reserve("report-37233-v1", capability="publish-owned-report",
                     arguments={"target": "owned-evidence"})
    call = {"type": "toolCall", "id": "call-5", "name": "publish-owned-report",
            "arguments": {"target": "owned-evidence"}}
    fresh = boundary(tmp_path, lambda call: {"ok": True}, writer_id="loop-2")
    reply = fresh(call, claim_for="report-37233-v1")
    assert reply["status"] == "completed"
    assert (store / "starts.jsonl").read_text().strip()
    assert fresh.open_intents() == []


def test_boundary_drop_reserved_retires_a_never_dispatched_reservation(tmp_path):
    store = tmp_path / "effects"
    dispatch = boundary(tmp_path, lambda call: {"ok": True})
    dispatch.reserve("report-37233-v1", capability="publish-owned-report",
                     arguments={"target": "owned-evidence"})
    assert dispatch.drop_reserved("report-37233-v1") is True
    assert dispatch.status("report-37233-v1") is None
    # The retired id is free again.
    again = dispatch.reserve("report-37233-v1", capability="publish-owned-report",
                             arguments={"target": "reused-evidence"})
    assert again.arguments == {"target": "reused-evidence"}
    # A reservation that was never dispatched drops again the same way.
    assert dispatch.drop_reserved("report-37233-v1") is True
    assert dispatch.status("report-37233-v1") is None
    assert (store / "starts.jsonl").read_text().strip() == ""
    assert (store / "outcomes.jsonl").read_text().strip() == ""


def test_boundary_drop_reserved_refuses_a_started_or_completed_operation(tmp_path):
    dispatch = boundary(tmp_path, lambda call: {"ok": True})
    reply = dispatch(NATIVE_CALL)
    # A completed operation is not a reservation and cannot be retired.
    assert dispatch.drop_reserved(reply["operation_id"]) is False
    assert dispatch.status(reply["operation_id"])["status"] == "completed"
    # An unknown id is simply not reserved.
    assert dispatch.drop_reserved("report-37233-v1") is False
    assert dispatch.status("report-37233-v1") is None


def test_boundary_an_operation_id_can_be_caller_named(tmp_path):
    seen = []
    call = {"type": "toolCall", "id": "call-77", "name": "inspect",
            "arguments": {"path": "owned-evidence"},
            "operation_id": "report-37233-v1"}
    reply = boundary(tmp_path, lambda call: seen.append(call) or {"ok": True})(call)
    assert reply["operation_id"] == "report-37233-v1"
    assert seen[0]["operation_id"] == "report-37233-v1"
    assert (tmp_path / "effects" / "intents.jsonl").read_text().strip()


def test_boundary_rejects_a_call_without_an_identity(tmp_path):
    dispatch = boundary(tmp_path, lambda call: {"ok": True})
    with pytest.raises(ValueError):
        dispatch({"type": "toolCall", "name": "inspect", "arguments": {}})
    with pytest.raises(ValueError):
        dispatch({"type": "toolCall", "id": "no|name", "arguments": {}})
    with pytest.raises(ValueError):
        dispatch({"type": "toolCall", "id": "no-args", "name": "inspect"})


def test_boundary_requires_authority(tmp_path):
    with pytest.raises(ValueError):
        EffectBoundary(tmp_path / "effects", execute=lambda call: None,
                       writer_id="loop-1", obligation={"source": "owned-event"},
                       authority="")


def test_boundary_provider_ids_outside_the_journal_alphabet_are_stable(tmp_path):
    seen = []
    odd = {"type": "toolCall", "id": "a b/c?", "name": "inspect",
           "arguments": {"path": "owned-evidence"}}
    dispatch = boundary(tmp_path, lambda call: seen.append(call) or {"ok": True})
    reply = dispatch(odd)
    assert reply["operation_id"] == "effect-a-b-c-817d7883382b"
    # The derived id is collision-free and the original id is retained verbatim.
    fresh = boundary(tmp_path, lambda call: pytest.fail("unexpected execution"),
                     writer_id="loop-2")
    again = fresh.recover(odd)
    assert again["operation_id"] == "effect-a-b-c-817d7883382b"
    assert again["status"] == "completed"
    assert len(seen) == 1
    assert seen[0]["native_call"]["id"] == "a b/c?"


def test_effect_outcome_for_keeps_the_durable_states(tmp_path):
    led = ledger(tmp_path)
    intent = allocate(led)
    led.dispatch(intent, lambda call: {"recorded": True, "seq": 12})
    reply = effect_outcome_for(led.reconcile("op-1"))
    assert reply == {"status": "completed", "result": {"recorded": True, "seq": 12},
                     "operation_id": "op-1", "failure": None,
                     "reconciliation_ref": None, "executions": 1}
    assert effect_outcome_for(
        led.reconcile("never")) == {"status": "unknown", "result": None,
                                    "operation_id": "never", "failure": "no-intent",
                                    "reconciliation_ref": None, "executions": 0}


def test_snapshot_json_refuses_noncanonical_values():
    assert snapshot_json({"a": [1, 2]}) == {"a": [1, 2]}
    with pytest.raises(ValueError):
        snapshot_json({"bad": object()})
    with pytest.raises(ValueError):
        snapshot_json({"inf": float("inf")})


def test_a_capability_that_dies_in_the_dispatch_window_is_attributable(tmp_path):
    """The crash window `drive_native` crosses is already attributable.

    `drive_native` records its `proposal` and then calls dispatch, so a
    capability that dies in that gap is the case the boundary must survive. It
    does not propagate and does not leave an anonymous pending call: the intent
    is durable under the id derived from the provider call, no outcome is
    written and the capability never ran. A successor reaches the operation by
    name, reads unknown, and refuses to run it again.
    """
    store = tmp_path / "effects"
    died = boundary(tmp_path, lambda call: (_ for _ in ()).throw(
        InterruptedError("crash before the effect persisted")))
    reply = died(NATIVE_CALL)
    assert reply["status"] == "unknown"
    assert reply["executions"] == 0, "a dead capability cannot have executed"
    assert reply["operation_id"] and (store / "intents.jsonl").read_text().strip(), \
        "the crash left no durable intent to reconcile"

    fresh = boundary(tmp_path, lambda call: {"ok": True})
    open_ids = [intent.operation_id for intent in fresh.open_intents()]
    assert open_ids == [reply["operation_id"]]
    # recover derives the id from the call alone, so it needs no dispatch-time
    # state and is available before the proposal is even written.
    assert fresh.recover(NATIVE_CALL)["operation_id"] == reply["operation_id"]
    again = fresh(NATIVE_CALL)
    assert again["status"] == "unknown"
    assert again["executions"] == 0, "unknown is not permission to retry"


def test_the_boundary_has_no_status_branch_it_cannot_reach(tmp_path):
    """Every status the boundary accepts is one it can actually reach.

    `partial` parses and `not-started` is a loop status, but neither is a branch
    of this boundary: an effect is written once as `completed`, or left with no
    outcome at all, which every reader reports as `unknown`. `partial` stays in
    the alphabet because the journal replays outcomes a caller wrote, not only
    this boundary's own. A regression that invents a producer for it would be a
    new state the loop and `read_native_journal` have not agreed to handle.
    """
    from mishe_tauftauf.inference_effects import _EFFECT_STATUSES

    store = tmp_path / "effects"
    fresh = lambda execute: boundary(tmp_path, execute)
    # Each step uses its own id: one execution per id is the boundary's contract,
    # so a second dispatch of a completed id is `AlreadyExecuted`, not a new state.
    crashed_call = {**NATIVE_CALL, "id": "call_native|crash_window"}
    completed = fresh(lambda call: {"ok": True})(NATIVE_CALL)
    assert completed["status"] == "completed"
    crashed = fresh(lambda call: (_ for _ in ()).throw(OSError("died")))(crashed_call)
    assert crashed["status"] == "unknown"
    reserved = fresh(lambda call: {"ok": True}).reserve(
        "op-reserved", capability="inspect", arguments={"path": "owned-evidence"})
    assert reserved.reservation is True
    assert fresh(lambda call: {"ok": True}).status("op-reserved")["status"] == "unknown"
    assert fresh(lambda call: {"ok": True}).recover(NATIVE_CALL)["status"] == "completed"
    assert fresh(lambda call: {"ok": True}).recover(crashed_call)["status"] == "unknown"

    statuses = sorted({json.loads(line)["status"] for line in
                       (store / "outcomes.jsonl").read_text().splitlines()})
    assert statuses == ["completed"], statuses
    # The reader of a caller-written outcome still has to parse it.
    assert "partial" in _EFFECT_STATUSES
    # `not-started` is the caller's accounting, not an outcome this store holds.
    assert "not-started" not in _EFFECT_STATUSES
