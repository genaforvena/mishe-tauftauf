import json

from mishe_tauftauf import plant_view_boundary as view


def _lines(*events):
    return "\n".join(json.dumps(e) for e in events)


def test_codex_stream_reads_like_an_omp_stream(tmp_path):
    stream = tmp_path / 'wake-x.prompt.out.jsonl'
    text = _lines(
        {"type": "thread.started", "thread_id": "t"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"id": "0", "type": "agent_message", "text": "Checking the tree."}},
        {"type": "item.started", "item": {"id": "1", "type": "command_execution", "command": "git status", "status": "in_progress"}},
        {"type": "item.completed", "item": {"id": "1", "type": "command_execution", "command": "git status", "aggregated_output": "clean\n", "exit_code": 0}},
        {"type": "item.completed", "item": {"id": "2", "type": "command_execution", "command": "false", "aggregated_output": "", "exit_code": 1}},
        {"type": "item.completed", "item": {"id": "3", "type": "file_change", "changes": [{"path": "walls/genome.md", "kind": "update"}]}},
        {"type": "item.completed", "item": {"id": "4", "type": "reasoning", "text": "hidden"}},
        {"type": "item.completed", "item": {"id": "5", "type": "agent_message", "text": "Done: nothing to repair."}},
        {"type": "turn.completed", "usage": {"input_tokens": 5}},
    )
    out = view.stream_text(text)
    assert "UNKNOWN" not in out and "hidden" not in out
    lines = out.splitlines()
    assert lines[0] == "[assistant] Checking the tree."
    assert "[tool bash] git status" in lines and "[tool output] clean" in lines
    assert "[tool error] [no output]" in lines
    assert "[tool file_change] update walls/genome.md" in lines
    assert lines[-1] == "[agent_end]"
    answer = view.final_answer(out)
    assert answer.startswith("(2 tool calls)") and answer.rstrip().endswith("Done: nothing to repair.")
    stream.write_text(text)
    progress = view.progress_text(stream)
    assert "says: Done: nothing to repair." in progress and "last call: [tool file_change]" in progress


def test_codex_failure_and_unknown_item_stay_visible():
    out = view.stream_text(_lines(
        {"type": "turn.failed", "error": {"message": "usage limit reached"}},
        {"type": "item.completed", "item": {"id": "9", "type": "brand_new_item"}},
    ))
    assert "[provider error] usage limit reached" in out
    assert "UNKNOWN stream record" in out and "brand_new_item" in out
