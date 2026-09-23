from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from .judges import QUESTIONS


@dataclass(frozen=True)
class JudgmentPolicy:
    version: str = "1"
    question_versions: dict[str, str] | None = None
    questions: dict[str, str] | None = None
    low_threshold: float = 0.20
    high_threshold: float = 0.80
    judge_timeout: float = 30.0

    def question_version(self, question: str) -> str:
        return (self.question_versions or {}).get(question, "1")

    def question_text(self, question: str) -> str:
        return (self.questions or {}).get(question, QUESTIONS[question])

    def classify(self, question: str, probability: float | None) -> str:
        if probability is None:
            return "unknown"
        if question in {"publish", "relevance"}:
            return "no" if probability <= self.low_threshold else "yes"
        if probability >= self.high_threshold:
            return "yes"
        if probability <= self.low_threshold:
            return "no"
        return "uncertain"


def load_policy(path: Path | None) -> JudgmentPolicy:
    if path is None:
        return JudgmentPolicy()
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) - {"version", "question_versions", "questions", "low_threshold", "high_threshold", "judge_timeout"}:
        raise ValueError("invalid judgment policy fields")
    version = data.get("version", "1")
    revisions = data.get("question_versions", {})
    questions = data.get("questions", {})
    low, high, timeout = (data.get("low_threshold", 0.20), data.get("high_threshold", 0.80), data.get("judge_timeout", 30.0))
    if not isinstance(version, str) or not version or not isinstance(revisions, dict) or set(revisions) - set(QUESTIONS) or any(not isinstance(v, str) or not v for v in revisions.values()):
        raise ValueError("invalid judgment policy versions")
    if not isinstance(questions, dict) or set(questions) - set(QUESTIONS) or any(not isinstance(v, str) or not v.strip() or len(v.encode("utf-8")) > 4096 for v in questions.values()) or set(questions) - set(revisions):
        raise ValueError("question overrides require bounded text and explicit revisions")
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (low, high, timeout)) or not 0 <= low < high <= 1 or not 0 < timeout <= 300:
        raise ValueError("invalid judgment policy bounds")
    return JudgmentPolicy(version, revisions, questions, float(low), float(high), float(timeout))
