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
from . import task_state
from .coordination_checks import anomalies


def _publication_lines(home):
    """Observe saved private verdicts; never invoke inference from a pane."""
    lines, uncertain = [], False
    timeout_seconds = 30
    config = home / 'publication-check.json'
    if not config.exists():
        lines.append('PUBLICATION GATE: UNTESTED — no semantic worker configured')
        uncertain = True
    else:
        try:
            data = json.loads(config.read_text(encoding='utf-8'))
            command = data.get('command')
            if not isinstance(command, list) or not command or not all(isinstance(value, str) and value for value in command):
                raise ValueError('command must be a nonempty argv list')
            timeout_seconds = data.get('timeout_seconds', 30)
            if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 0 < timeout_seconds <= 300:
                raise ValueError('timeout_seconds must be finite and in (0, 300]')
            lines.append('PUBLICATION GATE: CONFIGURED — availability depends on private worker results')
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            lines.append(f'PUBLICATION GATE: UNKNOWN — configuration unavailable: {exc}')
            uncertain = True
    try:
        paths = list((home / 'post-checks').glob('*.json'))
        if not paths:
            lines.append('PUBLICATION RESULT: UNTESTED — no saved private review')
            uncertain = True
        else:
            ordered = sorted(paths, key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)
            report = None
            for index, latest in enumerate(ordered):
                candidate = json.loads(latest.read_text(encoding='utf-8'))
                status = candidate.get('status')
                pending = (status is None and candidate.get('semantic_status') == 'untested'
                           and candidate.get('clear') is False
                           and isinstance(candidate.get('worker_input_bytes'), int)
                           and candidate['worker_input_bytes'] > 0)
                if pending:
                    if index == 0:
                        age = datetime.now(timezone.utc).timestamp() - latest.stat().st_mtime
                        abandoned = age > timeout_seconds + 5 or age < 0
                        lines.append('PUBLICATION ACTIVE: ' +
                                     ('UNKNOWN — unfinished review exceeded its deadline; reconcile the private report'
                                      if abandoned else 'PENDING — private review in progress; last completed result retained'))
                        lines.append(f'  Active evidence: private report={latest}')
                        uncertain = uncertain or abandoned
                    continue
                if status not in {'clear', 'suspicious', 'unknown'}:
                    raise ValueError('private review has an invalid status')
                report = candidate
                break
            if report is None:
                lines.append('PUBLICATION RESULT: UNTESTED — no completed private review')
                return lines, True
            semantic = report.get('semantic_status', 'untested')
            refused = status in {'suspicious', 'unknown'}
            scope = (f" source={report.get('source', 'unknown')} stage={report.get('stage', 'unknown')}" if refused else '')
            lines.append(f"PUBLICATION RESULT: {'REFUSED' if refused else 'CLEAR'}{scope} semantic={semantic}")
            lines.append(f'  Evidence: private report={latest}')
            flagged = [row.get('id', 'unknown') for row in report.get('results', []) if row.get('verdict') in {'suspicious', 'unknown'}]
            if flagged:
                lines.append('  Correction questions: ' + ','.join(flagged) + '; inspect the private report before resubmitting.')
            uncertain = uncertain or refused or semantic in {'unknown', 'untested'}
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        lines.append(f'PUBLICATION RESULT: UNKNOWN — private report unavailable: {exc}')
        uncertain = True
    return lines, uncertain


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
        feed_error = None
    except (OSError, ValueError) as exc:
        feed_error = str(exc)
        verdict = f"UNKNOWN witness chat.log unreadable: {exc}"
        lines.append(verdict)
        entries = []
    visible: list[str] = []
    try:
        tasks = open_tasks(entries)
    except ValueError as exc:
        verdict = f"UNKNOWN witness task state: {exc}"
        tasks = []
    lines.append(f"OPEN TASKS: {len(tasks)}")
    for task in tasks[-20:]:
        lines.append(f"{task.identity} owner={task.owner} state={task.status} at={task.sequence}")
    try:
        lines.extend(task_state.lines(entries))
    except ValueError as exc:
        verdict = f"UNKNOWN witness task state: {exc}"
        lines.append(verdict)
    findings = anomalies(entries)
    if feed_error:
        findings.append(dict(id='feed-unreadable', task=None, kind='invalid-context', severity='UNKNOWN',
                             message='Canonical feed unavailable: ' + feed_error, sequences=[], evidence=[str(Feed(home).path)]))
    severities = Counter(finding['severity'] for finding in findings)
    coordination = ('UNKNOWN' if severities['UNKNOWN'] else 'RED' if severities['RED'] else
                    'SUSPICIOUS' if severities['SUSPICIOUS'] else 'GREEN')
    lines.append(f"COORDINATION: {coordination} — {len(findings)} task findings across all roles")
    for finding in findings:
        lines.append(f"ANOMALY: {finding['severity']} task={finding['task'] or 'feed'} "
                     f"kind={finding['kind']} id={finding['id']} — {finding['message']}")
        lines.append("  Evidence: sequences=" + ','.join(map(str, finding['sequences'])) +
                     ("; " + '; '.join(str(value) for value in finding['evidence'] if value) if finding['evidence'] else ''))
    if severities['UNKNOWN']:
        verdict = 'UNKNOWN witness coordination evidence invalid'
    elif severities['RED']:
        verdict = 'FAIL witness coordination invariants need reconciliation'
    elif severities['SUSPICIOUS'] and verdict.startswith('PASS'):
        verdict = 'UNKNOWN witness coordination suspicion needs verification'
    gate_lines, gate_uncertain = _publication_lines(home)
    lines.extend(gate_lines)
    if gate_uncertain and verdict.startswith('PASS'):
        verdict = 'UNKNOWN witness publication gate needs verified evidence'
    try:
        recent = work_receipts(entries, "genome")[-3:]
        repeated = repeated_no_change(entries, "genome")
    except (ValueError, TypeError, KeyError) as exc:
        recent, repeated = [], []
        verdict = f'UNKNOWN witness receipt evidence: {exc}'
        lines.append(verdict)
    lines.append("GENOME WORK: " + (", ".join(f"{work.wake}:{work.result}@{work.observation}" for work in recent) or "none"))
    if repeated:
        identity = repeated[-1].task
        try:
            plan = task_state.states(entries).get(identity)
        except ValueError:
            plan = None
        if plan and plan.status == "waiting" and not task_state.eligible(plan, entries):
            if plan.retry_event or plan.retry_at or plan.retry_task:
                lines.append(f"LOOP: WAITING task={identity} observation={repeated[-1].observation} "
                             f"retry={plan.retry_event or plan.retry_at or plan.retry_task} — historical attempts; prerequisite unchanged")
            else:
                lines.append(f"LOOP: UNKNOWN task={identity} observation={repeated[-1].observation} "
                             "— checked next step or retry predicate missing")
                verdict = "UNKNOWN witness task next step missing"
        else:
            lines.append("LOOP: RED — three genome turns without a change on the same observation" +
                         (f" task={identity}" if identity else ""))
            verdict = "FAIL witness repeated genome no-change turns"
    now = datetime.now(timezone.utc)
    recent_entries = [entry for entry in entries if
                      0 <= (now - datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00"))).total_seconds() <= 120]
    by_source = Counter(entry.source for entry in recent_entries if entry.source != "seed")
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
        if entry.source == 'seed' and entry.body.startswith('seed observation '):
            continue
        paragraphs = entry.body.splitlines()
        if paragraphs:
            visible.append(f"{entry.sequence} {entry.source}: {paragraphs[0]}")
            visible.extend("  " + line for line in paragraphs[1:])
    lines.append("LATEST CHAT.LOG TEXT (all roles, including witness and seed work):")
    lines.extend(visible[-20:] or ["(none)"])
    lines.append("GOAL: keep the plant's shared work coherent until each need has an owner, checked result, and next step")
    lines.append("PURSUIT: notice stale panes, forgotten tasks, duplicate claims, and unverified fixes; follow them through the shared text tape")
    lines.append("NEXT: verify the strongest unresolved task finding; claim useful work and repair within owned scope")
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
