"""System Zero event-filter adapter.

The implementation lives beside observation rendering so both paths share the exact
unfiltered frame contract. This module is the stable import surface for embedders.
"""

from .observations import FilterResult, run_filter

__all__ = ["FilterResult", "run_filter"]
