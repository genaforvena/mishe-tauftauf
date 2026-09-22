from __future__ import annotations

import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

QUESTIONS = {
    "publish": "Does a newly observed unknown, error, change, recovery, or evidence warrant entering the shared feed rather than remaining routine pane output?",
    "relevance": "Does this feed text concern this current Top Pain?",
    "valid-attempt": "Does the note describe concrete relevant follow-through with an observable condition and check time, supported by visible red evidence or a readable plan with unmet continuation?",
    "continue-observing": "Is the observation adequately covered by the pending experiment, with no early contradiction, lost observability, or unrelated problem requiring reasoning?",
    "prediction-met": "Does fresh System Zero evidence establish the predicted condition?",
    "desired-state-met": "Does fresh evidence establish the Top Pain's desired observable state rather than only an intermediate prediction or claim?",
}
RESULT_RE = re.compile(r"probability ([^\s]+)\Z")
UNKNOWN_RE = re.compile(r"unknown (.+)\Z")


@dataclass(frozen=True)
class Judgment:
    question: str
    probability: float | None
    outcome: str
    reason: str
    document: str


def document(question: str, slug: str, top_pain: str, evidence: str, prediction: str | None = None) -> str:
    if question not in QUESTIONS:
        raise ValueError(f"unknown judgment question: {question}")
    sections = [
        f"QUESTION {question}",
        "INSTRUCTIONS\n" + QUESTIONS[question],
        f"TOP PAIN {slug}\n{top_pain}",
        "EVIDENCE\n" + evidence,
    ]
    if prediction is not None:
        sections.append("PREDICTION\n" + prediction)
    return "\n\n".join(sections) + "\n"


def classify(question: str, probability: float | None) -> str:
    if probability is None:
        return "unknown"
    if question in {"publish", "relevance"}:
        return "no" if probability <= 0.20 else "yes"
    if probability >= 0.80:
        return "yes"
    if probability <= 0.20:
        return "no"
    return "uncertain"


def run_external(path: Path, question: str, slug: str, top_pain: str, evidence: str, prediction: str | None = None, timeout: float = 30.0) -> Judgment:
    request = document(question, slug, top_pain, evidence, prediction)
    try:
        result = subprocess.run([str(path)], input=request.encode("utf-8"), capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Judgment(question, None, "unknown", f"judge unavailable: {exc}", request)
    if result.returncode != 0:
        return Judgment(question, None, "unknown", f"judge exit {result.returncode}: {result.stderr.decode('utf-8', 'replace').strip()}", request)
    try:
        output = result.stdout.decode("utf-8").rstrip("\n")
    except UnicodeDecodeError as exc:
        return Judgment(question, None, "unknown", f"judge invalid UTF-8: {exc}", request)
    if "\n" in output:
        return Judgment(question, None, "unknown", "judge returned extra result lines", request)
    unknown = UNKNOWN_RE.fullmatch(output)
    if unknown:
        return Judgment(question, None, "unknown", unknown.group(1), request)
    match = RESULT_RE.fullmatch(output)
    if not match:
        return Judgment(question, None, "unknown", "judge result must be 'probability P' or 'unknown reason'", request)
    try:
        probability = float(match.group(1))
    except ValueError:
        probability = math.nan
    if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
        return Judgment(question, None, "unknown", "judge probability must be finite and in [0,1]", request)
    return Judgment(question, probability, classify(question, probability), "external judgment", request)


def conservative_unknown(question: str, slug: str, top_pain: str, evidence: str, prediction: str | None = None, reason: str = "no System One adapter selected") -> Judgment:
    request = document(question, slug, top_pain, evidence, prediction)
    return Judgment(question, None, classify(question, None), reason, request)


def controls() -> dict[str, tuple[str, str]]:
    return {
        "publish": ("A new probe reports UNKNOWN and exit 2.", "The stable reading is unchanged and routine."),
        "relevance": ("The temperature Top Pain's sensor failed.", "An unrelated music fixture changed."),
        "valid-attempt": ("Visible red check failed; fix the path; expect exit 0. Check at: 2099-01-01T00:00:00Z", "I think it is done."),
        "continue-observing": ("Expected intermediate value is present and the pane is fresh.", "The pane froze and evidence contradicts the prediction."),
        "prediction-met": ("Fresh check output equals the promised value.", "Fresh check still shows the old error."),
        "desired-state-met": ("Fresh live check establishes the desired reading.", "Only an intermediate change or model claim exists."),
    }
