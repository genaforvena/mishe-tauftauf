from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .feed import Feed, FeedEntry

CHECK_RE = re.compile(r"^Check at: (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)$", re.MULTILINE)
PLAN_RE = re.compile(r"^Plan: ([^\r\n]+)$", re.MULTILINE)
STATUS_RE = re.compile(r"^prediction (\d+): (accepted|needs-reasoning|met|missed|insufficient-evidence)$", re.MULTILINE)
REPLACE_RE = re.compile(r"^prediction (\d+) replaced by (\d+)$", re.MULTILINE)
DESIRED_RE = re.compile(r"^desired state for prediction (\d+): (observed|not-established)$", re.MULTILINE)


class PredictionError(ValueError):
    pass


@dataclass(frozen=True)
class Prediction:
    sequence: int
    slug: str
    body: str
    check_at: datetime
    plan: Path | None
    accepted: bool
    terminal: bool
    desired_observed: bool
    replaced_by: int | None


def parse_check_at(body: str) -> datetime:
    matches = CHECK_RE.findall(body)
    if len(matches) != 1:
        raise PredictionError("prediction requires exactly one whole 'Check at: YYYY-MM-DDTHH:MM:SSZ' line")
    return datetime.strptime(matches[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def resolve_plan(home: Path, body: str) -> Path | None:
    matches = PLAN_RE.findall(body)
    if len(matches) > 1:
        raise PredictionError("prediction permits at most one Plan line")
    if not matches:
        return None
    raw = matches[0]
    candidate = (home / raw).resolve()
    root = home.resolve()
    if candidate == root or root not in candidate.parents:
        raise PredictionError("Plan path must remain inside home")
    if not candidate.is_file():
        raise PredictionError(f"Plan path is missing or unreadable: {raw}")
    candidate.read_text(encoding="utf-8")
    return candidate


def validate_submission(home: Path, slug: str, body: str, now: datetime | None = None) -> tuple[datetime, Path | None]:
    if not body:
        raise PredictionError("prediction text must be non-empty")
    check_at = parse_check_at(body)
    current = now or datetime.now(timezone.utc)
    if check_at <= current:
        raise PredictionError("Check at must be in the future")
    return check_at, resolve_plan(home, body)


def replay_predictions(home: Path, entries: list[FeedEntry]) -> dict[int, Prediction]:
    raw: dict[int, tuple[str, str, datetime, Path | None]] = {}
    statuses: dict[int, set[str]] = {}
    replacements: dict[int, int] = {}
    desired: set[int] = set()
    for entry in entries:
        if entry.source.startswith("prediction/"):
            slug = entry.source.split("/", 1)[1]
            try:
                raw[entry.sequence] = (slug, entry.body, parse_check_at(entry.body), resolve_plan(home, entry.body))
            except PredictionError:
                continue
        if entry.source == "mishe-tauftauf":
            for seq, status in STATUS_RE.findall(entry.body):
                statuses.setdefault(int(seq), set()).add(status)
            for old, new in REPLACE_RE.findall(entry.body):
                replacements[int(old)] = int(new)
            for seq, state in DESIRED_RE.findall(entry.body):
                if state == "observed":
                    desired.add(int(seq))
    result: dict[int, Prediction] = {}
    for seq, (slug, body, check_at, plan) in raw.items():
        states = statuses.get(seq, set())
        terminal = bool(states & {"met", "missed", "insufficient-evidence"})
        result[seq] = Prediction(seq, slug, body, check_at, plan, "accepted" in states, terminal, seq in desired, replacements.get(seq))
    return result


def pending_predictions(home: Path, entries: list[FeedEntry], slug: str | None = None) -> list[Prediction]:
    predictions = replay_predictions(home, entries).values()
    return sorted(
        (p for p in predictions if p.accepted and not p.terminal and p.replaced_by is None and (slug is None or p.slug == slug)),
        key=lambda p: (p.check_at, p.sequence),
    )


def append_prediction(feed: Feed, slug: str, body: str, *, replaces: int | None = None) -> FeedEntry:
    entries = feed.entries()
    validate_submission(feed.home, slug, body)
    if replaces is not None:
        old = replay_predictions(feed.home, entries).get(replaces)
        if old is None or old.slug != slug or old.terminal or old.replaced_by is not None:
            raise PredictionError(f"prediction {replaces} is not an active prediction for {slug}")
    entry = feed.append_runtime(f"prediction/{slug}", body)
    if replaces is not None:
        feed.append_runtime("mishe-tauftauf", f"prediction {replaces} replaced by {entry.sequence}")
    return entry


def expectations_text(home: Path, entries: list[FeedEntry], slug: str) -> str:
    pending = pending_predictions(home, entries, slug)
    lines = ["EXPECTATIONS"]
    if not pending:
        lines.append("(none)")
    for item in pending:
        lines.append(f"prediction {item.sequence} · check {item.check_at.strftime('%Y-%m-%dT%H:%M:%SZ')}")
        lines.extend("  " + line for line in item.body.splitlines() if not line.startswith("Check at:"))
    return "\n".join(lines) + "\n"
