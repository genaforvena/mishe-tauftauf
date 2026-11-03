"""Cached follow-through surface and independent producer-freshness guard.

Rendering never scans Git or chat. Put this projection after the MIND WALL
boundary: changing diagnostic counts must not recursively create another wake.
The guard checks producer freshness, not whether the reported work is healthy.

Lawrence 2005 politics-of-4I (AMR 30:1, DOI 10.5465/amr.2005.15281431) —
Crossan et al. 1999 4I: intuiting → interpreting → integrating → institutionalizing;
Lawrence maps discipline→intuiting, influence→interpreting, force→integrating,
domination→institutionalizing (Fig.1/Table1 pp.182-183; Table1 p.185 in this
rendition); modes episodic (influence, force) vs systemic (domination,
discipline). ROUTING_POWER below is a plant analogy, not a Lawrence mapping:
the 2024 6I paper (BMJ Open 14(5) e083830) restates only the process→form mapping
and classifies no routing stage. Vocabulary is a detector, not a causal claim;
any form may appear in any process (p.184). Props. 1-4 are "will be best
facilitated by" hypotheses (pp.186-189) — helpers below are heuristic detectors,
not a classifier.

Clegg 2006 (JMI 15(4):426-430, DOI 10.1177/1056492606294640) — total institutions as
concentrated systemic power (Goffman/Bauman/Foucault pp.427-429; checklist p.429:
strip identity, routinize/dehumanize, division of labour, technicalization, enclosure,
misrepresentation) — warns systemic forms (domination, discipline) are not
normatively neutral infrastructure: when they totalize they enclose learning and
eliminate equivocality (pp.427,429). Lawrence p.188 notes domination can be
enabling/productive; this vocabulary keeps that qualifier but does not present
systemic power as value-free (see ROUTING_POWER note).

Jurado-Zambrano et al. 2024 (SIGNOS 16(2):182-203, DOI 10.15332/24631140.10083)
§§ Procesos de conocimiento / Relaciones / Conclusiones pairs Crossan 4I with the KM
cycle (Alavi & Leidner 2001; Wilson & Campbell 2016; Lin & Lee 2006 variant
acquisition/conversion/application/protection) enumerating creation, transfer,
acquisition, storage, retrieval, application (pp.189-190, via Gold et al. 2001).
Three enablers bridge both cycles — people as axis, process focus, organizational
culture as common facilitator (pp.185,209-211); ISO 9001:2015 §7.1.6 (p.9 quoted
p.188) frames the knowledge requirement. This module models 4I+Lawrence and currently
implements KM storage (refusal audit with REFUSAL_AUDIT_MAX_BYTES) and application
(routing/health); it has no named acquisition/transfer/retrieval/protection or
culture/people measure. Any future wiring of those processes would need per-role
attribution and a culture guard. Qualitative descriptive synthesis only (46 sources
via Google Scholar relevance); no measured effect; categories emergent not exclusive.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Final

from .post_check import _save


LEARNING_PROCESSES: Final[tuple[str, ...]] = (
    "intuiting",
    "interpreting",
    "integrating",
    "institutionalizing",
)
"""Crossan et al. 1999 4I processes (Lawrence 2005 p.181)."""

POWER_MODES: Final[tuple[str, ...]] = ("episodic", "systemic")
"""Lawrence 2005 p.182-183: episodic vs systemic power."""

POWER_FORMS: Final[tuple[str, ...]] = (
    "influence",
    "force",
    "domination",
    "discipline",
)
"""Four power forms (Hardy 1996 via Lawrence p.182-183)."""

PROCESS_POWER_MAP: Final[dict[str, str]] = {
    "interpreting": "influence",
    "integrating": "force",
    "institutionalizing": "domination",
    "intuiting": "discipline",
}
"""Most-effective mapping process→form (Fig.1/Table1 p.183); not exclusive (p.184)."""

ROUTING_POWER: Final[dict[str, str]] = {
    "initial": "influence",
    "changed": "influence",
    "escalation": "force",
    "reminder": "influence",
}
"""Plant analogy (not a Lawrence or 6I mapping): routing stage→power form; None for unknown.

A reminder re-presents a finding and restricts no response option, so under Lawrence it
is influence (episodic, affecting costs/benefits, p.185: moral suasion/persuasion), not
discipline (systemic, ongoing identity-forming via socialization/training/teamwork, p.188)
and not domination (restricting available actions via material/IS/physical layout, p.188;
domination can be enabling/productive, not merely repressive, p.188 — the domination case
here would be the outcome-writer refusal, not a reminder). That refusal is correctly typed
as domination but must not be read as benign: per Clegg JMI 15(4) p.429 (routinization,
division of labour, technicalization, misrepresentation) a domination guard that silently
blocks other roles' evidence paths would reproduce the concentrated-power failure mode
rather than surface it — so this projection's audit (REFUSAL_AUDIT_MAX_BYTES, last-24h
projection, health guard) exists to keep a denial visible, not to normalize the guard.
No caller reads this mapping yet (see follow_through_power_form).
"""


def follow_through_power_form(
    *,
    stage: str | None = None,
    process: str | None = None,
    kind: str | None = None,
) -> str | None:
    """Type the power form for a routing stage or learning process.

    Pure, deterministic, no I/O. Returns the form for a routing stage
    (initial/changed→influence, escalation→force, reminder→influence; plant analogy) or
    for a learning process (via PROCESS_POWER_MAP), else None for incomplete
    or unknown inputs. Lawrence Props. 1-4 are "will be best facilitated by" hypotheses
    (pp.186-189) and any form may appear in any process (p.184) — these helpers are
    heuristic detectors, not a classifier.
    """
    if stage is not None:
        return ROUTING_POWER.get(stage)
    # process is the primary name for a 4I process; kind is kept as alias
    # for callers that pass a finding kind that happens to name a process.
    if process is not None:
        return PROCESS_POWER_MAP.get(process)
    if kind is not None:
        return PROCESS_POWER_MAP.get(kind)
    return None


def follow_through_mode_for_form(form: str | None) -> str | None:
    """Map a power form to its mode: episodic vs systemic.

    Pure, deterministic, no I/O. influence/force→episodic, domination/
    discipline→systemic, else None. Mirrors Lawrence p.182-183.
    Lawrence modes are tied to Props. 1-4 as "most effective" (p.184), not exclusive.
    """
    if form in ("influence", "force"):
        return "episodic"
    if form in ("domination", "discipline"):
        return "systemic"
    return None


def lines(home: Path, *, now=None) -> list[str]:
    from .initiative import snapshot_lines as initiative_lines
    from .substrate_watch import snapshot_lines as substrate_lines
    from .initiative_watch import snapshot_lines as watch_lines

    return [*_outcome_refusal_lines(home, now=now),
            *substrate_lines(home, now=now, max_age=120),
            *initiative_lines(home, now=now, max_age=180),
            *watch_lines(home, now=now, max_age=180)]


REFUSAL_AUDIT_MAX_BYTES = 262144
"""Bound on the refusal audit tail the projection reads."""


def _outcome_refusal_lines(home: Path, *, now=None) -> list[str]:
    """Report refused outcome publications, so a denial is visible to the plant.

    The writer records every refusal it makes. Without that record a
    plant-wide denial of the outcome instrument is indistinguishable from a
    plant that produced nothing.
    """
    from .wall import OUTCOME_REFUSAL_AUDIT

    home = Path(home).resolve()
    now = now or datetime.now(timezone.utc)
    path = home / OUTCOME_REFUSAL_AUDIT
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return [f"OUTCOME WRITER REFUSALS: none recorded (no audit file yet at {OUTCOME_REFUSAL_AUDIT})"]
    except OSError as exc:
        return [f"OUTCOME WRITER REFUSALS: UNKNOWN — audit unreadable: {exc}"]
    if len(raw) > REFUSAL_AUDIT_MAX_BYTES:
        raw = raw[-REFUSAL_AUDIT_MAX_BYTES:]
    entries = []
    for line in raw.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("ts"), str):
            continue
        entries.append(entry)
    if not entries:
        return [f"OUTCOME WRITER REFUSALS: none recorded (audit {OUTCOME_REFUSAL_AUDIT} holds no readable entry)"]
    horizon = now - timedelta(hours=24)
    recent = []
    for entry in entries:
        try:
            stamp = datetime.fromisoformat(entry["ts"].replace("Z", "+00:00"))
        except (TypeError, ValueError):
            continue
        if stamp >= horizon:
            recent.append(entry)
    if not recent:
        return [f"OUTCOME WRITER REFUSALS: 0 in the last 24h "
                f"(audit {OUTCOME_REFUSAL_AUDIT}, {len(entries)} older entries retained)"]
    denials = [entry for entry in recent if entry.get("gaps")]
    last = recent[-1]
    gaps = last.get("gaps") or []
    refs = ",".join(f"chat.log#{row.get('sequence')}" for row in gaps
                    if isinstance(row, dict) and row.get("sequence") is not None)
    detail = f" blocked-by={refs}" if refs else ""
    reason = str(last.get("reason", "unspecified"))[:160]
    return [f"OUTCOME WRITER REFUSALS: {len(recent)} in the last 24h "
            f"({len(denials)} coverage denials); last={last.get('role')}/{last.get('kind')}: {reason}{detail}"]


def _load(path: Path, maximum: int) -> dict:
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError("snapshot exceeds size bound")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("snapshot is not an object")
    return value


def health(home: Path, *, now=None, require_initiative_watch=False) -> dict:
    """Route one alert per freshness-failure episode, retaining bad guard state."""
    from .wall import message
    from .initiative import read_snapshot
    from .substrate_watch import _read_state
    from .initiative_watch import read_snapshot as read_watch

    home = Path(home).resolve()
    now = now or datetime.now(timezone.utc)
    path = home / "checks/follow-through-health.json"
    save_path = path
    problems = []
    previous = {}
    try:
        previous = _load(path, 65536)
        if previous.get("version") != 1 or previous.get("state") not in {"GREEN", "UNKNOWN"}:
            raise ValueError("invalid guard state")
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError) as exc:
        problems.append(f"guard state unreadable: {type(exc).__name__}")
        try:
            raw = path.read_bytes()
            retained = home / "artifacts" / ("follow-through-guard-corrupt-" + hashlib.sha256(raw).hexdigest() + ".json")
            retained.parent.mkdir(parents=True, exist_ok=True)
            if not retained.exists():
                retained.write_bytes(raw)
        except OSError:
            problems.append("guard state retention failed")
            # Keep the sole damaged original when it could not be archived.
            save_path = home / "checks/follow-through-health-retention-failed.json"
            try:
                previous = _load(save_path, 65536)
            except (OSError, ValueError, TypeError):
                previous = {}
    producers = {}
    sources = [
        ("substrate-watch", "checked_at", "status", 120),
        ("initiative", "generated_at", "state", 180),
    ]
    if require_initiative_watch:
        sources.append(("initiative-watch", "generated_at", "state", 180))
    for name, stamp, state_key, maximum_age in sources:
        try:
            if name == "substrate-watch":
                report = _read_state(home / "checks/substrate-watch.json")
            elif name == "initiative-watch":
                report = read_watch(home, now=now, max_age=maximum_age)
            else:
                report = read_snapshot(home, now=now, max_age=maximum_age)
            at = datetime.fromisoformat(report[stamp].replace("Z", "+00:00"))
            age = (now - at).total_seconds()
            if not 0 <= age <= maximum_age:
                raise ValueError("stale or future producer timestamp")
            producers[name] = {"fresh": True, "reported_state": report[state_key], "age_seconds": age}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            producers[name] = {"fresh": False}
            problems.append(f"{name}: missing, stale, broken or invalid snapshot")
    state = "UNKNOWN" if problems else "GREEN"
    key = hashlib.sha256(json.dumps([problems, require_initiative_watch], sort_keys=True).encode()).hexdigest()
    row = {"version": 1, "checked_at": now.isoformat(), "state": state,
           "producers": producers, "problems": problems,
           "scope": "Producer snapshot freshness only; not successful repairs or runtime health."}
    if problems and previous.get("alert_key") != key:
        text = ("Follow-through observation UNKNOWN: " + "; ".join(problems) + ". "
                f"Evidence {save_path.relative_to(home)}. Resolver Health: inspect the owned "
                "source/initiative/assurance observers and retained snapshot errors within 120 seconds; "
                "restore observation, preserving original failure evidence. Genome and Witness "
                "take over if Health cannot act; no human polling or permission wait. "
                "Do not call missing monitoring success.")
        recipients = ("health", "genome", "witness") if require_initiative_watch else ("health",)
        row["alert_sequences"] = {
            recipient: message(home, "follow-through-watch", recipient, text).sequence
            for recipient in recipients
        }
        row["alert_sequence"] = row["alert_sequences"]["health"]
    if problems:
        row["alert_key"] = key
        if "alert_sequence" not in row and previous.get("alert_key") == key:
            row["alert_sequence"] = previous.get("alert_sequence")
            row["alert_sequences"] = previous.get("alert_sequences", {})
    _save(save_path, row)
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="Check producer freshness once")
    parser.add_argument("--watch", action="store_true", help="Independently guard producer freshness")
    parser.add_argument("--interval", type=float, default=30)
    parser.add_argument("--require-initiative-watch", action="store_true",
                        help="Also supervise initiative review/alert delivery; fail closed if unavailable")
    args = parser.parse_args()
    if not math.isfinite(args.interval) or args.interval <= 0:
        parser.error("interval must be finite and positive")
    if not (args.check or args.watch):
        print("\nMIND WALL — shared follow-through evidence; not an authored wall")
        print("\n".join(lines(args.home)))
        return 0
    from .session_watch import notify
    notify("READY=1")
    while True:
        row = health(args.home, require_initiative_watch=args.require_initiative_watch)
        print(json.dumps(row, sort_keys=True), flush=True)
        notify("WATCHDOG=1\nSTATUS=" + row["state"] + " producer freshness only")
        if not args.watch:
            return 0 if row["state"] == "GREEN" else 1
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
