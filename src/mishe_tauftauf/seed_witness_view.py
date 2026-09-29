"""Cheap, source-backed chat and channel view for a planted witness pane."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from .feed import Feed
from .seed_board import open_tasks, repeated_no_change, work_receipts


def render(home: Path) -> str:
    lines = ["DESIRED STATE: planted channels stay live and coordination gaps stay visible"]
    session = os.environ.get("MISHE_SEED_SESSION", "")
    required = {p.stem for p in (home / "charters").glob("*.md")}
    if not session:
        verdict = "UNKNOWN witness session unset"
        lines.append("WINDOWS: UNKNOWN — session unset")
    else:
        try:
            result = subprocess.run(["tmux", "list-windows", "-t", session, "-F", "#{window_name}"],
                                    capture_output=True, text=True, timeout=2)
            if result.returncode:
                verdict = "UNKNOWN witness tmux unavailable"
                lines.append("WINDOWS: UNKNOWN — tmux session unavailable")
            else:
                actual = set(result.stdout.splitlines())
                missing = sorted(required - actual)
                verdict = ("FAIL witness missing=" + ",".join(missing) if missing else
                           "PASS witness windows=" + ",".join(sorted(actual)))
                lines.append("WINDOWS: " + ("RED" if missing else "GREEN") +
                             " · windows=" + ",".join(sorted(actual)) +
                             (" · missing=" + ",".join(missing) if missing else ""))
        except (OSError, subprocess.TimeoutExpired):
            verdict = "UNKNOWN witness tmux check unavailable"
            lines.append("WINDOWS: UNKNOWN — tmux check unavailable")
    lines.append(f"CHAT.LOG: {home / 'chat.log'}")
    try:
        entries = Feed(home).entries()
    except (OSError, ValueError) as exc:
        verdict = f"UNKNOWN witness chat.log unreadable: {exc}"
        lines.append(verdict)
        entries = []
    visible: list[str] = []
    tasks = open_tasks(entries)
    lines.append(f"OPEN TASKS: {len(tasks)}")
    for task in tasks[-20:]:
        lines.append(f"{task.identity} owner={task.owner} state={task.status} at={task.sequence}")
    recent = work_receipts(entries, "genome")[-3:]
    lines.append("GENOME WORK: " + (", ".join(f"{work.wake}:{work.result}@{work.observation}" for work in recent) or "none"))
    repeated = repeated_no_change(entries, "genome")
    if repeated:
        lines.append("LOOP: RED — three genome turns without a change on the same observation")
        verdict = "FAIL witness repeated genome no-change turns"
    for entry in entries:
        if entry.source in {"witness", "seed"}:
            continue
        visible.extend(f"{entry.sequence} {entry.source}: {entry.body}".splitlines())
    lines.append("LATEST CHAT.LOG LINES (own bookkeeping omitted):")
    lines.extend(visible[-20:] or ["(none)"])
    lines.append("GOAL: keep the plant's shared work coherent until each need has an owner, checked result, and next step")
    lines.append("PURSUIT: notice stale panes, forgotten tasks, duplicate claims, and unverified fixes; follow them through the shared text tape")
    lines.append("NEXT: inspect the newest unresolved issue; repair witness-owned gaps or route one precise task to genome")
    lines.append("STATE: " + ("GREEN" if verdict.startswith("PASS") else "RED" if verdict.startswith("FAIL") else "UNKNOWN"))
    report = home / "observations" / "witness"
    report.parent.mkdir(exist_ok=True)
    report.write_text(verdict + "\n", encoding="utf-8")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    args = parser.parse_args()
    print(render(args.home), end="")


if __name__ == "__main__":
    main()
