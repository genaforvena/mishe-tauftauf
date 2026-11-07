"""The plant's detection duties: ``duties.json`` at the repository root.

Each duty is a producer (the only polling), a signal with ``max_age`` and one
resolver role. ``declared`` expands the manifest for a site into the producer
declarations ``producers.load`` turns into timers; ``resolvers`` maps each
producer unit to its resolver so a failed producer reaches the right role.
"""
from __future__ import annotations

import sys
from pathlib import Path

from .state_files import read_json


def load(repo) -> list:
    data = read_json(Path(repo) / "duties.json")
    if not isinstance(data, dict) or not isinstance(data.get("duties"), list):
        raise ValueError("duties.json is missing or has no duties list")
    return data["duties"]


def _expand(word: str, home, repo, source=None) -> str:
    from .work_boundary import duty_word
    return duty_word(word, home=home, repo=repo, source=source or repo, python=sys.executable)


def declared(repo, home, *, source=None) -> list:
    out = []
    for duty in load(source or repo):
        item = {"name": duty["name"], "every": duty["every"], "timeout": duty["timeout"],
                "signal": {"max_age": duty["max_age"], "subscribers": [duty["resolver"]]}}
        if duty.get("builtin"):
            item["builtin"] = True
        else:
            item["command"] = [_expand(w, home, repo, source) for w in duty["command"]]
            item["red_codes"] = duty.get("red_codes", [2])
            item["unknown_codes"] = duty.get("unknown_codes", [])
        out.append(item)
    return out


def resolvers(repo, prefix: str = "mishe") -> dict:
    return {f"{prefix}-producer-{d['name']}.service": d["resolver"] for d in load(repo)}
