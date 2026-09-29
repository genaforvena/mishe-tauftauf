"""Cheap, source-backed chat and channel view for a planted witness pane."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .feed import Feed
from .ci_watch import line as ci_line
from .seed_board import open_tasks, repeated_no_change, work_receipts


def render(home: Path) -> str:
    lines = ["DESIRED STATE: planted channels stay live and coordination gaps stay visible"]
    session = os.environ.get("MISHE_SEED_SESSION", "")
    try:
        required = set(json.loads((home / "health" / "windows.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
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
                extra = sorted(actual - required)
                verdict = ("FAIL witness window mismatch" if missing or extra else
                           "PASS witness windows=" + ",".join(sorted(actual)))
                lines.append("WINDOWS: " + ("RED" if missing or extra else "GREEN") +
                             " · windows=" + ",".join(sorted(actual)) +
                             (" · missing=" + ",".join(missing) if missing else "") +
                             (" · extra=" + ",".join(extra) if extra else ""))
        except (OSError, subprocess.TimeoutExpired):
            verdict = "UNKNOWN witness tmux check unavailable"
            lines.append("WINDOWS: UNKNOWN — tmux check unavailable")
    lines.append(f"CHAT.LOG: {home / 'chat.log'}")
    ci = ci_line(home)
    lines.append(ci)
    if ci.startswith("CI: FAIL") and verdict.startswith("PASS"):
        verdict = "FAIL witness CI failure needs genome follow-through"
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
    now = datetime.now(timezone.utc)
    recent_entries = [entry for entry in entries if
                      0 <= (now - datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00"))).total_seconds() <= 120]
    by_source = Counter(entry.source for entry in recent_entries if entry.source not in {"seed", "witness"})
    by_observation = Counter(entry.body.splitlines()[0].split(" sha256=")[0] for entry in recent_entries
                             if entry.source == "seed" and entry.body.startswith("seed observation "))
    floods = [f"{source}: {count} entries" for source, count in by_source.items() if count >= 8]
    floods.extend(f"{source}: {count} observations" for source, count in by_observation.items() if count >= 12)
    if floods:
        lines.append("CHAT RATE: RED — " + "; ".join(sorted(floods)) +
                     ". Trace the wake or sampling feedback loop and fix its cause.")
        verdict = "FAIL witness chat event flood"
    else:
        lines.append("CHAT RATE: GREEN — no repeated high-rate source in the last two minutes")
    for entry in entries:
        if entry.source in {"witness", "seed"}:
            continue
        paragraphs = entry.body.splitlines()
        if paragraphs:
            visible.append(f"{entry.sequence} {entry.source}: {paragraphs[0]}")
            visible.extend("  " + line for line in paragraphs[1:])
    lines.append("LATEST CHAT.LOG TEXT (own bookkeeping omitted):")
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
