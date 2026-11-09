"""Literal boundary for one OMP wake's per-message usage records.

An OMP wake writes its engine stream to ``wake-<key>.prompt.out.jsonl``. Each
assistant turn ends with a ``message_end`` record that carries the provider,
the model and the usage of that turn. The budget collector folds those turns
into per-account token totals, so a turn it cannot read makes the whole
account's usage UNKNOWN.

One shape ends a turn without a response id: the turn was aborted before the
provider answered. Its usage counters are all zero. That is countable zero —
the account spent nothing on that turn — but it is not uncountable usage. The
old reader raised on it, so one aborted turn made every non-Claude account
UNKNOWN (``countable()`` in the budget view) until the collector's 35-day
retention window aged the file out. That is a false UNKNOWN, and it held the
budget view's account verdicts at ``UNKNOWN turns``.

This module decides one thing: whether a record is countable, and with what
values. It never judges a quota, a window or a verdict; those belong to the
budget view and its forecast rule. A record that is genuinely unreadable
raises :class:`Uncountable`, so the caller keeps showing UNKNOWN instead of
inventing a zero.
"""
from __future__ import annotations

import json

#: The usage counters an OMP turn reports.
USAGE_FIELDS = ("input", "output", "cacheRead", "cacheWrite")

#: A turn that ended before the provider answered reports every counter as zero.
_ZERO_USAGE = (0, 0, 0, 0)

#: A failed call with zero usage: no tokens spent, not a usage record.
_ERROR_STOP = "error"

#: A turn aborted before the provider answered: zero usage, no tokens spent.
_ABORTED_STOP = "aborted"


class Uncountable(Exception):
    """A usage-bearing record whose values could not be read.

    The caller must keep the account UNKNOWN: an unreadable record is not a
    zero.
    """


def _number(value):
    """Return a finite number, or None when the value is not one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


def _all_zero(usage):
    """True when every usage counter is present and zero."""
    for field in USAGE_FIELDS:
        value = _number(usage.get(field))
        if value is None or value != 0:
            return False
    return True


def _cost_total(usage):
    """The cost total of a usage block, or None when it is not a number."""
    cost = usage.get("cost")
    if not isinstance(cost, dict):
        return 0.0
    return _number(cost.get("total"))


def usage_record(event):
    """Decode one ``message_end`` event into (timestamp, name, key, values).

    ``event`` is the decoded event. The result is ``(ts, "provider|model",
    key, [turns, output, input, cache_read, cache_write, cost])``, the shape
    the budget collector buckets.

    Returns None when the event is not a usage-bearing assistant turn: a tool
    result, a user message, or a record without a usage block. An aborted turn
    with no provider answer returns countable zero.

    Raises :class:`Uncountable` when the record claims usage but its values
    cannot be read. The caller keeps that account UNKNOWN.
    """
    if not isinstance(event, dict) or event.get("type") != "message_end":
        return None
    message = event.get("message")
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None

    stop = message.get("stopReason")
    response_id = message.get("responseId")
    has_id = isinstance(response_id, str) and bool(response_id)

    if stop == _ERROR_STOP and _all_zero(usage):
        # A failed call with zero usage: no tokens spent and no response id.
        # It is not a usage record at all, so it is not counted and it is not
        # uncountable either.
        return None

    if stop == _ABORTED_STOP and _all_zero(usage) and not has_id:
        # The turn was aborted before the provider answered. It spent nothing,
        # and the provider issued no response id, so the record's own identity
        # is the key: a re-read replaces rather than double-counts. Counting
        # it as zero is what the collector's caller needs; raising here is
        # what made every non-Claude account UNKNOWN.
        completed = _number(message.get("completedAt"))
        timestamp = _number(message.get("timestamp"))
        if completed is None:
            completed = timestamp
        if completed is None:
            raise Uncountable("aborted turn without a timestamp")
        provider = message.get("provider")
        model = message.get("model")
        if not isinstance(provider, str) or not isinstance(model, str):
            raise Uncountable("aborted turn without a provider and model")
        error_id = message.get("errorId")
        marker = error_id if error_id is not None else completed
        key = f"aborted:{provider}|{model}:{marker}"
        return completed / 1000.0, f"{provider}|{model}", key, [1] + list(_ZERO_USAGE) + [0.0]

    output = _number(usage.get("output"))
    if not has_id or output is None:
        raise Uncountable("usage without a response id or a numeric output")
    completed = _number(message.get("completedAt"))
    if completed is None:
        completed = _number(message.get("timestamp"))
    if completed is None:
        raise Uncountable("usage without a completion time")
    values = [1, output]
    for field in ("input", "cacheRead", "cacheWrite"):
        number = _number(usage.get(field))
        if number is None:
            raise Uncountable(f"usage without a numeric {field}")
        values.append(number)
    cost = _cost_total(usage)
    if cost is None:
        raise Uncountable("usage without a numeric cost total")
    provider = message.get("provider")
    model = message.get("model")
    if not isinstance(provider, str) or not isinstance(model, str):
        raise Uncountable("usage without a provider and model")
    return completed / 1000.0, f"{provider}|{model}", response_id, values + [cost]


def decode_line(line):
    """Decode one JSON-lines record into a usage tuple, or None.

    A line that is not JSON is not a record and is skipped. A record that
    decodes but cannot be read raises :class:`Uncountable`.
    """
    try:
        event = json.loads(line)
    except ValueError:
        return None
    return usage_record(event)
