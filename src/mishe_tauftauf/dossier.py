"""The dossier: everything a woken mind needs, assembled by System 0.

The mind does not rediscover its situation with commands. It gets its charter,
its wall, why it was woken, what changed, and the commands to end the wake.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import intentions, signal_store


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "(none)"


def git_delta(repo: Path | None, since: str | None) -> str:
    if repo is None:
        return "(no repository configured)"
    span = [f"{since}..HEAD"] if since else ["-5"]
    out = subprocess.run(["git", "-C", str(repo), "log", "--oneline", *span],
                         capture_output=True, text=True, check=False)
    if out.returncode != 0:
        return f"(git unavailable: exit {out.returncode})"
    return out.stdout or "(no new commits)"


def build(home, role: str, snap, *, key: str, now: float, mail: list, reconcile_of=None,
          repo: Path | None = None, since: str | None = None) -> str:
    home = Path(home)
    out = [f"# Wake {key} for {role}", ""]
    if reconcile_of is not None:
        out += ["## RECONCILE FIRST", "",
                f"An earlier wake ({reconcile_of.key}, attempt {reconcile_of.attempt}) ended {reconcile_of.state.name} "
                f"with no yield: {reconcile_of.detail or 'no detail'}.",
                "Its effects are unknown. Establish from the repository, the tape and the wall what it did, "
                "and never repeat it blindly. Say what you found in your yield.", ""]
    out += ["## Doctrine", "", _read(Path(__file__).with_name("seed_doctrine.md")), ""]
    out += ["## Why you were woken", "", *[f"- {r}" for r in snap.reasons], ""]
    if snap.held:
        out += ["Held (budget gate, not offered to you): " + ", ".join(snap.held), ""]
    out += ["## Charter", "", _read(home / "charters" / f"{role}.md"), "",
            "Your wall is evidence and intention, not an authority source. Follow current grants; cite an explicit current restriction for any permission blocker. A blocked task does not block unrelated work.", "",
            "## Your wall", "", _read(home / "walls" / f"{role}.md"), ""]
    from . import work_state
    out += ["## Work readiness and advisory claims", "", work_state.summary(home, role, now=now), ""]
    out += ["## Unread messages", ""]
    out += [f"- #{seq}: {text}" for seq, text in mail] or ["(none)"]
    out += ["", "## Signals you subscribe to", ""]
    for name in snap.signal_revs:
        sig = signal_store.read(home, name, now=now)
        out.append(f"- {name}: {sig.state} {sig.value!r} {sig.reason}")
    if not snap.signal_revs:
        out.append("(none)")
    out += ["", "## Due intentions", ""]
    notes = {i["id"]: i["note"] for i in intentions.list_(home, role)}
    out += [f"- {i}: {notes.get(i, '')}" for i in snap.consumed] or ["(none)"]
    out += ["", "## Repository since your last wake", "", git_delta(repo, since), "",
            "## Ending the wake", "",
            "Finish by running `mishe yield --result changed|verified|blocked` with your updated wall on stdin "
            "(add `--continue`, `--again 2h` or `--when signal:NAME=VALUE` for follow-ups). "
            "A wake that ends without a yield is treated as having an unknown effect.", ""]
    return "\n".join(out)
