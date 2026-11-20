#!/usr/bin/env python3
"""Detector: the signal of signals — every registered signal has a live producer, a
subscriber and a response (Ilya DM #303234: a signal without a listener is silent).

Registry is generated, not curated: ``signals/registry.json`` (what the producer
machinery itself registered: max_age, subscribers, registered_at per signal) plus the
duties manifest for accounts coverage. One worst case per signal, judged only from
bytes on disk:

- unmeasured: registered, never observed (no reading and no .seen) past the store's
  own grace deadline ``registered_at + max_age`` — a missing or dead producer.
- stale: the freshness stamp (.seen) is older than max_age — a producer that missed
  its cadence (the store's expire sweep ages the reading to UNKNOWN; this is the
  coverage view of the same failure).
- unattended-red: a RED reading (failed-* unit signals included: their RED value is
  the reading) older than 15 min with no owner-role wake active since it turned
  (Ilya DM #303254: doctor RED stood 19:28-20:27 with no mind awake). Attendance is
  a wake-record fact, not a chat scan: an outcome is recorded by a role inside its
  wake, so any owner wake active past the turn covers both. Unreadable wake
  evidence is a reported note, never an accusation. The cause names the signal and
  its owner so the subscriber this verdict wakes (the resolver, and health through
  the duty's subscribers) can route it, and a RED a wake has seen since it turned
  is attended: it parks on the ordinary ignored bound instead.
- ignored: a RED or UNKNOWN reading unchanged for longer than ``max(2 * max_age,
  1800)`` — no wake, outcome or state change inside the bound.
- unheard: a signal file on disk with no registry entry — nobody subscribes. Reported
  in the detail, never a RED cause by itself (legacy names age out as files).
- failing-producer: a failed ``mishe-producer-*`` user unit (systemctl --user
  evidence; an unreadable systemctl is reported, never assumed empty).
- self: the previous signal-coverage observation stamp older than its own max_age —
  this producer missed a cadence too, and the check names itself.

Exit 0 GREEN, 2 RED, 3 UNKNOWN (registry or manifest unreadable — coverage cannot be
judged, and a coverage check must not guess). Alerts only on RED or UNKNOWN: the duty
registers ``wake_on: "state"``, so GREEN coverage never wakes a subscriber.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

GREEN, RED, UNKNOWN = 0, 2, 3
SELF = "signal-coverage"
RESPONSE_MULT = 2     # a RED/UNKNOWN reading unanswered past 2 * max_age is ignored
RESPONSE_MIN = 1800   # ... but never judged quicker than half an hour
UNATTENDED_S = 900    # a RED older than this with no owner wake since it turned is unattended
UNIT_PREFIX = "mishe-producer-"
RESERVED_FILES = ("registry.json",)   # non-signal json in signals/ besides *.memory.json

# provider_budget.SIGNALS names the signal for each evaluated account; codex-a's
# signal is budget-codex, not a mechanical "budget-" prefix of the account id.
# Keep in step with that map; an unmapped account falls back to the prefix so a
# newly evaluated provider is still named as a dark lane until it is registered.
PROVIDER_SIGNALS = {"claude": "budget-claude", "codex-a": "budget-codex"}



def _read_json(path: Path):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _seen_at(sigdir: Path, name: str) -> float | None:
    try:
        return (sigdir / f"{name}.seen").stat().st_mtime
    except OSError:
        return None


def _ran_at(sigdir: Path, name: str) -> float | None:
    """Cadence heartbeat: the producer's last completed run (written by producers.run)."""
    try:
        return (sigdir / f"{name}.ran").stat().st_mtime
    except OSError:
        return None


def _failing_units(now: float) -> tuple[list[str], str | None]:
    """Failed mishe-producer-* user units, or (names, note) when systemctl cannot answer."""
    binary = os.environ.get("MISHE_SYSTEMCTL_BIN", "systemctl")
    try:
        proc = subprocess.run([binary, "--user", "--failed", "--plain", "--no-legend",
                                "--no-pager"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return [], f"unit state unmeasured: {exc}"
    if proc.returncode != 0:
        return [], "unit state unmeasured: systemctl exit " + str(proc.returncode)
    names = [line.split()[0] for line in proc.stdout.splitlines()
             if line.strip().startswith(UNIT_PREFIX)]
    return names, None


def _accounts_unmeasured(home: Path, repo: Path) -> tuple[list[str], str | None]:
    """Budget accounts with no duty that names them (covers/name text scan of the
    manifest — the only deterministic mapping there is; absent files are a note, not a
    verdict, because accounts coverage rides on files another duty owns)."""
    budget = _read_json(home / "engine" / "budget.json")
    manifest = (repo / "duties.json")
    try:
        text = manifest.read_text(encoding="utf-8")
    except OSError as exc:
        return [], f"duties.json unreadable: {exc}"
    if not isinstance(budget, dict) or not isinstance(budget.get("accounts"), dict):
        return [], "engine/budget.json unmeasured (owner: budget duties)"
    return sorted(a for a in budget["accounts"] if a not in text), None

def _dark_provider_verdicts(home: Path, registry: dict, now: float, repo: Path):
    """RED provider forecasts with no registered signal to carry them (codex-a, 2026-11-20).

    provider_budget evaluates every account in its SIGNALS map but observes only
    budget-claude. A second account's RED therefore lives in provider-budget-latest.json
    and the household view while no signal exists to wake a subscriber or name the
    cause here — the dark lane Ilya asked to see. The publisher rewrites that record
    every budget tick; an old record is a note, not a fresh finding.
    An active disposition is named in the cause so a waking mind sees the owner and
    recheck already recorded instead of a stale "disposition it" instruction; this
    only adds information — the verdict stays RED and the detector is not narrowed.
    """
    path = home / "operator" / "autonomy-repair-20261106" / "provider-budget-latest.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        age = now - path.stat().st_mtime
    except (OSError, ValueError):
        return [], "provider verdicts unmeasured (publisher record absent or unreadable)"
    if age > 3600:
        return [], f"provider verdicts unmeasured (publisher record {age / 3600:.1f} h old)"
    accounts = record.get("accounts") if isinstance(record, dict) else None
    if not isinstance(accounts, dict):
        return [], "provider verdicts unmeasured (publisher record has no accounts)"
    causes, summary = [], []
    for account, result in sorted(accounts.items()):
        name = PROVIDER_SIGNALS.get(account, f"budget-{account}")
        state = result.get("state") if isinstance(result, dict) else None
        if name in registry:
            summary.append(f"{account}={state} (signal {name})")
            continue
        summary.append(f"{account}={state} (NO signal {name})")
        if state == "RED":
            reason = result.get("reason") if isinstance(result, dict) else ""
            cause = (f"dark-provider-verdict: {account} RED — {reason}; "
                     f"no registered signal {name} to wake a subscriber")
            answer = _dispositioned(home, repo, name, now)
            if answer is not None:
                due = datetime.fromtimestamp(answer["recheck_at"], timezone.utc)
                cause += (f"; dispositioned {answer.get('kind')} by {answer.get('owner')} "
                          f"until {due:%d %b %H:%MZ} — RED is intended visibility")
            else:
                cause += " (register it with a reoffer and an owner, or disposition the known forecast)"
            causes.append(cause)
    return causes, "provider-verdicts: " + "; ".join(summary)

def _wake_intervals(wakes: Path, owners) -> dict[str, list[tuple[float, float]]] | None:
    """``[start, end]`` per owner-role wake in ``wakes/``, or None when unreadable.

    The wake key in a file name carries the start (``wake-<role>-<ns>.``); the end is
    the yield time, falling back to the last output of a wake still running or cut
    without a yield. A wake silent for the whole window is indistinguishable from a
    dead one from disk bytes, and the next observation clears the difference once
    the live wake writes again.
    """
    if not wakes.is_dir():
        return None
    try:
        entries = os.scandir(wakes)
    except OSError:
        return None
    owners = set(owners)
    out: dict[str, list[tuple[float, float]]] = {}
    with entries:
        for entry in entries:
            name = entry.name
            if not (name.startswith("wake-") and name.endswith(".prompt.md")):
                continue
            stem = name[len("wake-"):-len(".prompt.md")]
            role, sep, digits = stem.rpartition("-")
            if not sep or role not in owners or not digits.isdigit():
                continue
            start = float(digits)
            if start > 4e10:
                start /= 1e9
            end = start
            for suffix in (".yield.json", ".prompt.out.jsonl", ".prompt.out.json"):
                try:
                    end = (wakes / f"wake-{role}-{digits}{suffix}").stat().st_mtime
                    break
                except OSError:
                    continue
            out.setdefault(role, []).append((start, end))
    return out


def _attended(intervals: dict[str, list[tuple[float, float]]] | None,
              owners, turn: float) -> bool | None:
    """True when an owner wake was active at or after ``turn``; None when unmeasured.

    A wake whose end reached the turn either started inside the window or ran across
    its start, so one comparison decides it. Outcomes need no separate evidence: a role
    records an outcome inside its wake, so wake activity covers both clauses at once.
    """
    if intervals is None:
        return None
    return any(end >= turn for role in owners for _, end in intervals.get(role, ()))


def _dispositioned(home: Path, repo: Path, name: str, now: float):
    """The unexpired disposition record for signal ``name``, else None. An unreadable
    store is None (the finding stays visible), never an implicit mute."""
    try:
        import sys
        src = str(Path(repo) / "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        from mishe_tauftauf import dispositions
        return dispositions.active(Path(home), name, now=now)
    except Exception:  # noqa: BLE001 - a broken store must not hide a finding
        return None


def _lapsed_known_disposition(home: Path, repo: Path, name: str, now: float):
    """The expired known disposition for ``name``, if any.

    ``active()`` deliberately returns None once the recheck is due, so the ignored
    cause cannot say who owed the answer or when it lapsed. Reading the record back
    from the listing keeps the finding actionable: a resolver and a deadline, not
    just an age. A broken store still returns None; it never mutes the finding.
    """
    try:
        src = str(Path(repo) / "src")
        if src not in sys.path:
            sys.path.insert(0, src)
        from mishe_tauftauf import dispositions
        for row in dispositions.listing(Path(home), now=now):
            if (row.get("key") == name and row.get("kind") == "known"
                    and row.get("expired") is True):
                return row
    except Exception:  # noqa: BLE001 - a broken store must not hide a finding
        pass
    return None


def _recheck_servable(entry) -> bool:
    """Can ``yielding.settle`` arm this signal's known-disposition recheck wake?

    settle arms ``disposition-<name>`` only for a signal registered with a reoffer and
    at least one subscriber role (yielding.py:220). Without either, a valid disposition's
    ``recheck_at`` never becomes an intention: the acceptance lapses unserved while this
    check still reports the finding as answered. Observed on budget-claude 2026-11-20.
    """
    if not isinstance(entry, dict) or "reoffer" not in entry:
        return False
    return any(isinstance(role, str) and role for role in entry.get("subscribers", ()))


def run(home: Path, now: float, *, repo: Path | None = None,
        sigdir: Path | None = None) -> tuple[int, dict]:
    repo = repo or Path(__file__).resolve().parent.parent
    sigdir = sigdir or Path(home) / "signals"
    registry = _read_json(sigdir / "registry.json")
    if not isinstance(registry, dict) or not registry:
        return UNKNOWN, {"verdict": "UNKNOWN", "cause":
                         "signals/registry.json unreadable: coverage cannot be judged"}
    causes: list[str] = []
    counts = {"registered": len(registry), "ok": 0, "unmeasured": 0, "stale": 0,
              "ignored": 0, "unattended": 0, "unheard": 0, "unserveable": 0,
              "failing_units": [], "notes": []}
    detail_lines: list[str] = []
    wake_cache: dict[frozenset, dict | None] = {}

    for name, entry in sorted(registry.items()):
        if not isinstance(entry, dict):
            continue
        max_age = entry.get("max_age")
        if not isinstance(max_age, (int, float)) or max_age <= 0:
            counts["notes"].append(f"{name}: registry entry without max_age")
            continue
        reading = _read_json(sigdir / f"{name}.json")
        seen = _seen_at(sigdir, name)
        if reading is None and seen is None:
            registered_at = entry.get("registered_at")
            if isinstance(registered_at, (int, float)) and now > registered_at + max_age:
                counts["unmeasured"] += 1
                causes.append(f"unmeasured: {name} registered but never observed "
                              f"({(now - registered_at) / 3600:.1f} h since registration)")
            else:
                counts["notes"].append(f"{name}: never observed, inside grace")
            continue
        # Producer cadence: the last completed run or the last observation, whichever is later.
        # An UNKNOWN-declared run is a producer that ran on time; its input is judged below.
        beat = max((t for t in (seen, _ran_at(sigdir, name)) if t is not None), default=None)
        if beat is not None and now - beat > max_age:
            counts["stale"] += 1
            causes.append(f"stale: {name} freshness stamp {(now - beat) / 60:.0f} min old "
                          f"(max_age {max_age / 60:.0f} min) — producer missed cadence")
            continue
        if name == SELF:
            # This run recomputes its own verdict; its previous RED is judged by the
            # self stamp below, never answered as an ignored reading (that locked RED).
            counts["ok"] += 1
            continue
        value = reading.get("value") if isinstance(reading, dict) else None
        state = reading.get("state") if isinstance(reading, dict) else None
        observed_at = reading.get("observed_at") if isinstance(reading, dict) else None
        if (value in ("RED", "UNKNOWN") or state not in (None, "OK")) and \
                isinstance(observed_at, (int, float)):
            # Only a state word (RED/UNKNOWN) or a failed observation is owed a
            # response; a drifting reading (footprint, VERIFIED, samples=…) is
            # healthy data, not an alert, whatever its text says.
            bound = max(RESPONSE_MULT * max_age, RESPONSE_MIN)
            owners = [o for o in entry.get("subscribers", []) if isinstance(o, str)]
            if value == "RED" and now - observed_at > UNATTENDED_S:
                # Ilya DM #303254: a RED nobody's wake has crossed since it turned is
                # unattended, whatever its age — the parked/ignored bound below is for
                # REDs a subscriber has already had the chance to see.
                key = frozenset(owners)
                if key not in wake_cache:
                    wake_cache[key] = _wake_intervals(Path(home) / "wakes", owners)
                attended = _attended(wake_cache[key], owners, observed_at)
                if attended is None:
                    counts["notes"].append(f"{name}: wake evidence unmeasured, "
                                           "attendance unknown")
                elif not attended:
                    counts["unattended"] += 1
                    named = ", ".join(owners) or "health"
                    causes.append(f"unattended-red: {name} RED "
                                  f"{(now - observed_at) / 60:.0f} min with no wake of "
                                  f"owner {named} since it turned — wake {named} now")
                    continue
            if now - observed_at > bound:
                answer = _dispositioned(home, repo, name, now)
                if answer is not None:
                    # The resolver's unexpired answer (SITE/dispositions) is the read-back
                    # the disposition store exists for; an expired one returns the finding.
                    counts["notes"].append(f"{name}: {value or state} dispositioned "
                                           f"{answer.get('kind')} by {answer.get('owner')}")
                    if answer.get("kind") == "known" and not _recheck_servable(entry):
                        counts["unserveable"] += 1
                        causes.append(f"unserveable-disposition: {name} known disposition "
                                      f"by {answer.get('owner')} has recheck_at "
                                      f"{answer['recheck_at']:.0f} but no recheck wake — "
                                      f"registration needs a reoffer and a subscriber "
                                      f"(yielding.py:220)")
                    continue
                counts["ignored"] += 1
                cause = (f"ignored: {name} reading {value or state} unchanged "
                         f"{(now - observed_at) / 3600:.1f} h > bound {bound / 3600:.1f} h")
                lapsed = _lapsed_known_disposition(home, repo, name, now)
                if lapsed is not None and isinstance(lapsed.get("recheck_at"), (int, float)):
                    due = datetime.fromtimestamp(lapsed["recheck_at"], timezone.utc)
                    cause += (f"; known disposition by {lapsed.get('owner')} lapsed "
                              f"{due:%H:%M:%SZ} — recheck that resolver")
                causes.append(cause)
                continue
            counts["notes"].append(f"{name}: {value or state} fresh, response pending")
            continue
        counts["ok"] += 1

    # Signals on disk nobody subscribed to (no registry entry). *.memory.json files are
    # unit memory, not readings; registry.json is not a signal.
    on_disk = {p.name[:-5] for p in sigdir.glob("*.json")
               if p.name != "registry.json" and not p.name.endswith(".memory.json")}
    orphans = sorted(on_disk - set(registry))
    counts["unheard"] = len(orphans)
    if orphans:
        detail_lines.append("unheard (no subscriber): " + ", ".join(orphans[:12])
                            + (" …" if len(orphans) > 12 else ""))

    units, unit_note = _failing_units(now)
    counts["failing_units"] = units
    if unit_note:
        counts["notes"].append(unit_note)
    elif units:
        causes.append("failing-producer units: " + ", ".join(units))

    # Self: the previous own observation past its max_age is this producer failing.
    self_entry = registry.get(SELF)
    self_line = f"self not registered yet (first run)"
    if isinstance(self_entry, dict):
        self_seen = _seen_at(sigdir, SELF)
        self_max = self_entry.get("max_age", 0)
        if self_seen is None:
            self_line = f"self never observed (duty age unknown)"
        else:
            age = now - self_seen
            self_line = f"self stamp {age / 60:.0f} min old (max_age {self_max / 60:.0f} min)"
            if age > self_max:
                causes.append(f"self stale: signal-coverage stamp {age / 60:.0f} min old — "
                              "this producer missed its cadence")

    unmeasured_accounts, account_note = _accounts_unmeasured(Path(home), repo)
    if account_note:
        counts["notes"].append(account_note)
    # First covered case (Ilya DM #303234): budget-window-care.
    budget_case = []
    for probe in ("budget-opencode-go", "budget-claude"):
        entry = registry.get(probe)
        if isinstance(entry, dict):
            reading = _read_json(sigdir / f"{probe}.json") or {}
            subs = ",".join(entry.get("subscribers", [])) or "none"
            budget_case.append(f"{probe}={reading.get('value', 'unmeasured')}"
                               f" (subs {subs}, max_age {entry.get('max_age', 0) / 60:.0f}m)")
    detail_lines.append("budget-window-care: " + ("; ".join(budget_case) or "no budget signal registered"))
    dark_causes, dark_note = _dark_provider_verdicts(Path(home), registry, now, repo)
    counts["dark_providers"] = len(dark_causes)
    causes.extend(dark_causes)
    detail_lines.append(dark_note)
    if dark_note.startswith("provider verdicts unmeasured"):
        counts["notes"].append(dark_note)

    verdict = RED if causes else GREEN
    report = {"verdict": {GREEN: "GREEN", RED: "RED", UNKNOWN: "UNKNOWN"}[verdict],
              "counts": counts, "causes": causes,
              "unmeasured_accounts": unmeasured_accounts, "self": self_line,
              "detail": "\n".join([f"{k}: {v}" for k, v in counts.items()
                                   if k not in ("failing_units", "notes") and v]
                                  + [f"unit {u} failed" for u in units]
                                  + detail_lines + counts["notes"][:6])}
    return verdict, report


def main(argv: list[str] | None = None) -> int:
    here = os.environ.get("MISHE_SEED_HOME") or os.environ.get("MISHE_TAUFTAUF_HOME")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", default=here, required=here is None, help="site home")
    parser.add_argument("--now", type=float, default=None, help="test override for the epoch clock")
    args = parser.parse_args(argv)
    now = args.now if args.now is not None else time.time()
    code, report = run(Path(args.home), now)
    print(f"signal-coverage: {report['verdict']}"
          + (f": {report['causes'][0]}" if report.get("causes") else ""))
    for cause in report.get("causes", [])[1:]:
        print(f"  cause: {cause}")
    print(report["detail"])
    return code


if __name__ == "__main__":
    sys.exit(main())
