from __future__ import annotations

import sys

_MODEL = None
_LOAD_ERROR = None


def load_model():
    global _MODEL, _LOAD_ERROR
    if _MODEL is not None or _LOAD_ERROR is not None:
        return _MODEL
    try:
        import laya
        _MODEL = laya.load("convaiinnovations/laya", subfolder="typed-decisions")
    except Exception as exc:  # optional adapter must fail visibly
        _LOAD_ERROR = str(exc)
    return _MODEL


def _token_count(model, text: str) -> tuple[int, int | None]:
    tokenizer = getattr(model, "tokenizer", None)
    if tokenizer is None:
        return len(text.split()), getattr(model, "cfg", {}).get("max_len", 1024)
    encoded = tokenizer(text, add_special_tokens=True)
    ids = encoded.get("input_ids", encoded) if isinstance(encoded, dict) else encoded
    if ids and isinstance(ids[0], list):
        ids = ids[0]
    count = len(ids)
    limit = getattr(model, "cfg", {}).get("max_len")
    if not isinstance(limit, int):
        limit = getattr(tokenizer, "model_max_length", None)
    if not isinstance(limit, int) or limit > 1_000_000:
        limit = 1024
    return count, limit


def judge(text: str) -> tuple[float | None, str]:
    model = load_model()
    if model is None:
        return None, f"laya unavailable: {_LOAD_ERROR}"
    count, limit = _token_count(model, text)
    if limit is not None and count > limit:
        return None, f"input has {count} tokens, checkpoint budget is {limit}; no truncation"
    try:
        result = model.predict(
            text,
            {
                "decision": {
                    "type": "noul",
                    "instructions": "Answer the QUESTION in the supplied state according to its INSTRUCTIONS and EVIDENCE.",
                }
            },
        )
        value = result["answers"]["decision"]["noul"]
        if hasattr(value, "item"):
            value = value.item()
        return float(value), ""
    except Exception as exc:
        return None, f"laya inference failed: {exc}"


def main() -> int:
    text = sys.stdin.read()
    probability, reason = judge(text)
    if probability is None:
        print("unknown " + reason.replace("\n", " "))
        return 0
    print(f"probability {probability:.17g}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
