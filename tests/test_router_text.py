import json

from mishe_tauftauf.router_text import stream_events


def test_stream_events_yields_decoded_objects_in_order(tmp_path):
    stream = tmp_path / "wake.out.jsonl"
    stream.write_text('\n'.join([
        json.dumps({"type": "agent_start"}),
        json.dumps({"type": "message_end", "message": {"role": "assistant"}}),
    ]) + '\n')
    assert [event["type"] for event in stream_events(stream)] == ["agent_start", "message_end"]


def test_stream_events_skips_undecodable_and_non_object_lines(tmp_path):
    stream = tmp_path / "wake.out.jsonl"
    stream.write_text('\n'.join([
        '{"type":"agent_start"}',
        'not json at all',
        '',
        '["a list, not an event"]',
        '"a string, not an event"',
        '{"type":"agent_end"}',
    ]) + '\n')
    assert [event["type"] for event in stream_events(stream)] == ["agent_start", "agent_end"]


def test_stream_events_of_missing_file_is_empty(tmp_path):
    assert list(stream_events(tmp_path / "missing.out.jsonl")) == []


def test_stream_events_does_not_read_the_whole_file_before_the_first_event(tmp_path):
    # An engine stream runs to tens of megabytes: a caller that stops at the
    # first tool call must not pay for the rest of the file.
    stream = tmp_path / "wake.out.jsonl"
    with stream.open("w", encoding="utf-8") as lines:
        lines.write(json.dumps({"type": "agent_start"}) + '\n')
        lines.write(json.dumps({"type": "tool_execution_start"}) + '\n')
        for _ in range(20000):
            lines.write(json.dumps({"type": "message_update", "padding": "x" * 200}) + '\n')
    events = stream_events(stream)
    assert next(events)["type"] == "agent_start"
    assert next(events)["type"] == "tool_execution_start"
