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
    EffectIntent,
    EffectLedger,
    JournalCorrupt,
    StoreUnavailable,
    canonical_request,
    request_digest,
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
