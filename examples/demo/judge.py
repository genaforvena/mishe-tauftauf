#!/usr/bin/env python3
"""Protocol fixture, not intelligence and not a calibrated model."""
import sys

text = sys.stdin.read()
question = text.splitlines()[0].removeprefix("QUESTION ") if text else ""
if "TOP PAIN control" in text:
    negatives = {
        "publish": "stable reading is unchanged",
        "relevance": "unrelated music fixture",
        "valid-attempt": "I think it is done",
        "continue-observing": "pane froze",
        "prediction-met": "old error",
        "desired-state-met": "Only an intermediate",
    }
    probability = 0.05 if negatives[question] in text else 0.95
elif question in {"publish", "relevance", "valid-attempt"}:
    probability = 0.95
elif "DESIRED STATE: unrelated is stable" in text or "DESIRED STATE: every Top Pain lease" in text:
    probability = 0.95
elif question == "continue-observing":
    probability = 0.95 if "UNKNOWN" not in text and "exit: 2" not in text else 0.05
elif question in {"prediction-met", "desired-state-met"}:
    probability = 0.95 if "stdout:\nfixture-reading=42" in text else 0.05
else:
    probability = 0.5
print(f"probability {probability}")
