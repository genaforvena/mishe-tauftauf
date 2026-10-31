"""Optional render admission using only the independent recovery snapshot."""
from __future__ import annotations

import argparse
import fcntl
import os
from pathlib import Path
import sys
import time

from .recovery_io import identity, read_json, run_probe, save, status


def _attempt_at(path):
    try:
        at = read_json(path).get("at")
    except (ValueError, AttributeError):
        return None
    return float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) else None


def render(home, role, command, *, now=None):
    now = time.time() if now is None else now
    identity(role)
    root = Path(home) / "recovery"
    root.mkdir(parents=True, exist_ok=True)
    state = status(home, now=now)
    limited = state.get("capacity", {}).get("limited", True)
    slots = state.get("render_slots", 1) if limited else 2
    cooldown = state.get("render_cooldown", 30) if limited else 0
    slot_root = root
    timeout = 8
    try:
        cfg = read_json(root / "config.json")
        host_root = cfg.get("host_root")
        if host_root:
            from . import host_capacity
            slot_root = Path(host_root)
            if not slot_root.is_absolute() or slot_root.is_symlink() or not slot_root.is_dir():
                raise ValueError("invalid shared admission root")
            state = host_capacity.status(slot_root, now=now)
            slots, cooldown, timeout = state["slots"], state["cooldown"], state["render_timeout"]
    except ValueError as exc:
        # A configured but broken host policy must never reopen local slots.
        if (root / "config.json").exists():
            state = {"state": "UNKNOWN", "reason": str(exc)}
            slots = 0
    cache_path = root / "render-cache" / f"{role}.json"
    try:
        cache = read_json(cache_path)
    except ValueError:
        cache = None

    def deferred(reason):
        save(root / "render-action.json", {"at": now, "role": role, "action": "defer-optional-render", "reason": reason})
        age = now - cache["at"] if cache else None
        original = cache["receipt"]["stdout"] if cache else "No cached renderer output.\n"
        message = f"UNKNOWN — render deferred: {reason}; controller={state['state']}; cached_at={cache['at'] if cache else 'none'} age={age}; retained content is not a fresh observation\n"
        return {"code": 1, "stdout": message + original, "stderr": message, "ok": None}

    attempt_path = root / "render-attempts" / f"{role}.json"
    last_attempt = _attempt_at(attempt_path)
    if last_attempt is not None and 0 <= now - last_attempt < cooldown:
        return deferred("pressure cooldown")
    for number in range(slots):
        with (slot_root / f"render-slot-{number}.lock").open("a+") as handle:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                continue
            # Every admitted probe starts the cooldown, success or failure, and is
            # recorded before it runs so a crash mid-probe still cools the next retry.
            save(attempt_path, {"at": now, "role": role})
            env = dict(os.environ)
            if "MISHE_RECOVERY_PREVIOUS_PYTHONPATH_SET" in env:
                was_set = env.pop("MISHE_RECOVERY_PREVIOUS_PYTHONPATH_SET")
                original = env.pop("MISHE_RECOVERY_PREVIOUS_PYTHONPATH", "")
                if was_set:
                    env["PYTHONPATH"] = original
                else:
                    env.pop("PYTHONPATH", None)
            # The timeout supervisor inherits the flock. Killing this wrapper
            # cannot admit another child while its previous helper survives.
            receipt = run_probe(command, timeout=timeout, env=env, output_limit=1024 * 1024,
                                pass_fds=(handle.fileno(),))
            if receipt["ok"]:
                save(cache_path, {"at": now, "receipt": receipt})
            else:
                save(root / "render-failures" / f"{role}-{time.time_ns()}.json", receipt, immutable=True)
                receipt["stdout"] = "UNKNOWN — original renderer failed; retained failure evidence under recovery/render-failures\n" + receipt["stdout"]
                receipt["stderr"] = receipt["stdout"][:1024] + receipt["stderr"]
                receipt["code"] = 1
            return receipt
    return deferred("all render slots occupied")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    from .observations import validate_home
    validate_home(args.home)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("original renderer command required")
    row = render(args.home.resolve(), args.role, command)
    sys.stdout.write(row["stdout"])
    sys.stderr.write(row["stderr"])
    return row["code"]


if __name__ == "__main__":
    raise SystemExit(main())
