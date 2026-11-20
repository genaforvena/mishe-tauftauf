"""Model policy enforced at engine construction: a banned model never reaches a provider.

Ilya directed Mishe to stop using glm-5.3 on 2026-11-20. Engines
are constructed on every path (routed via engine_router._build and legacy via engines.for_role),
so refusing here is fail-closed for both. Matching is substring-based and case-insensitive so
aliases such as ``routerai/z-ai/glm-5.3-flash`` are refused too.
"""
from __future__ import annotations

BANNED_MODEL_MARKERS: tuple[str, ...] = ("glm-5.3",)


def refuse_banned_model(model: str | None) -> None:
    if not model:
        return
    lowered = str(model).lower()
    for marker in BANNED_MODEL_MARKERS:
        if marker in lowered:
            raise ValueError(f"model {model!r} is banned by policy ({marker})")
