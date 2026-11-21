"""Boundary adapter: reads the verdict word that leads a signal's value text.

A signal value reads ``"RED: detail"`` or ``"GREEN"``. This adapter is the only
place that splits that text, so callers ask for the word and never parse it.
"""
from __future__ import annotations


def verdict_word(value) -> str:
    """The upper-cased verdict word before the first colon, or ``""`` when absent."""
    head = str(value or "").split(":", 1)[0]
    return head.strip().upper()
