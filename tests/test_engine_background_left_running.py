"""A one-shot wake that ends its reply on a backgrounded job loses that job's output.

Operator wake 1794888664164281312 (2026-11-17 04:11Z) ran ``sleep 271`` through the bash tool,
which auto-backgrounded it with "Do NOT poll for it ... end your reply and wait to be woken". The
model did, omp wrote ``agent_end``, the wake process exited 86 s before the job finished and the
wake settled ``unknown (engine exit 0 without a yield)``. The settlement must name the job, so the
reconciling wake does not have to re-diagnose the stream.
"""
import json

from mishe_tauftauf import engine_boundary, owed, signal_store as store, wake
from mishe_tauftauf.core.interfaces import UNKNOWN

ROLES = ["genome", "health", "docs"]
NOW = 1000.0
NOTICE = ("Backgrounded as job bg_1; its output is injected into the conversation as a follow-up "
          "the moment it finishes. Do NOT poll for it.")


def event(kind, message=None):
    row = {"type": kind}
    if message is not None:
        row["message"] = message
    return json.dumps(row, separators=(",", ":"))


def message(role, text):
    return event("message_start", {"role": role, "content": [{"type": "text", "text": text}]})


def stream(tmp_path, *lines):
    path = tmp_path / "wake-docs-1.prompt.out.jsonl"
    path.write_text("\n".join(lines) + "\n")
    return path


def test_a_reply_that_ends_on_the_background_notice_reports_the_job(tmp_path):
    path = stream(
        tmp_path,
        message("user", "start"),
        message("toolResult", NOTICE),
        message("assistant", "Waiting for bg_1; standing by."),
        event("turn_end"),
        event("agent_end", {"messages": []}),
    )
    assert engine_boundary.background_left_running(path) == "bg_1"


def test_a_delivered_completion_clears_the_job(tmp_path):
    path = stream(
        tmp_path,
        message("toolResult", NOTICE),
        message("user", "Background job bg_1 completed:\nMARKER-OK"),
        message("assistant", "read it"),
        event("agent_end", {"messages": []}),
    )
    assert engine_boundary.background_left_running(path) is None


def test_a_turn_that_continues_past_the_backgrounding_call_clears_the_job(tmp_path):
    path = stream(
        tmp_path,
        message("toolResult", NOTICE),
        message("toolResult", "other output"),
        message("assistant", "done"),
    )
    assert engine_boundary.background_left_running(path) is None


def test_a_quoted_notice_in_assistant_text_is_not_a_job(tmp_path):
    path = stream(tmp_path, message("assistant", f"the tool said: {NOTICE}"), event("agent_end"))
    assert engine_boundary.background_left_running(path) is None


def test_a_missing_stream_reports_nothing(tmp_path):
    assert engine_boundary.background_left_running(tmp_path / "absent.jsonl") is None


class BackgroundingEngine:
    """Writes the background-notice stream, then ends without a yield, like omp did."""

    def run(self, prompt_file, *, env, cwd, timeout, on_start=None):
        sibling = prompt_file.with_suffix(".out.jsonl")
        sibling.write_text("\n".join([
            message("toolResult", NOTICE),
            message("assistant", "Waiting on bg_1, standing by."),
            event("agent_end", {"messages": []}),
        ]) + "\n")
        return 0


def test_the_settlement_names_the_background_job(tmp_path):
    (tmp_path / "walls").mkdir()
    (tmp_path / "walls" / "genome.md").write_text("MY WALL TEXT")
    store.register(tmp_path, "ci", max_age=60, subscribers=["genome"], now=NOW)
    owed.touch_inbox(tmp_path, "genome")
    result = wake.run_wake(tmp_path, "genome", roles=ROLES, engine=BackgroundingEngine(), now=NOW,
                           mail_reader=lambda *a: [], starter=[].append)
    assert result.outcome == UNKNOWN.name.lower()
    assert "background job bg_1 left running" in result.detail
