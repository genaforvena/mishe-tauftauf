"""Model policy enforced at engine construction: a banned model never reaches a provider.

No model is banned now: the operator lifted the Sonnet ban on 2026-11-19 (any available engine may
be routed, subject to budget). The mechanism stays so a future ban is one marker. Both engines
are constructed on every path (routed via engine_router._build and legacy via engines.for_role),
so refusing here is fail-closed for both. Matching is substring-based and case-insensitive so
aliases such as ``~anthropic/claude-sonnet-latest`` are refused too.
"""
from __future__ import annotations

BANNED_MODEL_MARKERS: tuple[str, ...] = ()


def refuse_banned_model(model: str | None) -> None:
    if not model:
        return
    lowered = str(model).lower()
    for marker in BANNED_MODEL_MARKERS:
        if marker in lowered:
            raise ValueError(f"model {model!r} is banned by policy ({marker})")
