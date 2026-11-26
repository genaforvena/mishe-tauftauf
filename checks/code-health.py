#!/usr/bin/env python3
"""Detector: code-health ratchets for lint, types and test coverage.

The repository had no linter, no type checker and no coverage measurement.  Cleaning every
existing finding in one change would be a risky rewrite, so each facet is a ratchet against the
committed ``code-health-baseline.json``:

- lint:  ``ruff check`` with the rules in pyproject.toml (pyflakes plus syntax errors: real
         defects such as undefined names, unused variables, redefined functions).  Counted per rule.
- types: ``mypy`` over src/.  Counted as a total of error lines.
- coverage: line coverage of src/ from a coverage.py JSON report (total percent).

Per facet, with ``--only lint|types|coverage`` selecting one:

- RED   (exit 2): a count rose above the baseline (any rule, or the type total), or coverage
                  fell more than the tolerance below the baseline.
- AMBER (exit 0): the code is better than the baseline; lower the baseline with
                  ``--write-baseline`` so the gain cannot silently be lost again.
- UNKNOWN (exit 3): the tool is missing, timed out, printed output that could not be decoded,
                  or (coverage) the report is invalid, missing or older than ``--max-age-days``.
                  A missing tool is never green.
- GREEN: equal to the baseline.

``--write-baseline`` only lowers: it refuses to record a higher count, so the baseline cannot be
widened to pass a change.  Tools run from ``--tools-python`` (default: ``$HOME_SITE/code-health-venv``
when it exists, else this interpreter); install them with
``python -m venv SITE/code-health-venv && SITE/code-health-venv/bin/pip install ruff mypy coverage``.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

BASELINE = "code-health-baseline.json"
LINT_PATHS = ["src", "tools", "checks", "tests", "conftest.py"]
TYPE_PATHS = ["src"]
COVERAGE_TOLERANCE = 0.5  # percentage points of noise before a drop is RED
TOOL_TIMEOUT = 120
FACETS = ("lint", "types", "coverage")


class Unknown(Exception):
    pass


def _run(cmd, cwd, timeout=TOOL_TIMEOUT):
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise Unknown(f"{cmd[0]} not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise Unknown(f"{' '.join(cmd[:3])} timed out after {timeout}s") from exc


def measure_lint(python, repo):
    cmd = [python, "-m", "ruff", "check", "--no-cache", "--output-format", "json", *LINT_PATHS]
    done = _run(cmd, repo)
    if "No module named" in done.stderr:
        raise Unknown("ruff is not installed for " + python)
    if done.returncode not in (0, 1):
        raise Unknown(f"ruff exited {done.returncode}: {done.stderr.strip()[:200]}")
    try:
        rows = json.loads(done.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise Unknown("ruff output is not JSON") from exc
    counts = {}
    for row in rows:
        code = row.get("code") or "syntax"
        counts[code] = counts.get(code, 0) + 1
    return counts


def measure_types(python, repo, cache_dir):
    cmd = [python, "-m", "mypy", "--ignore-missing-imports", "--no-error-summary", "--no-color-output",
           "--show-error-codes", "--cache-dir", str(cache_dir), *TYPE_PATHS]
    done = _run(cmd, repo)
    if "No module named" in done.stderr:
        raise Unknown("mypy is not installed for " + python)
    if done.returncode not in (0, 1):
        raise Unknown(f"mypy exited {done.returncode}: {(done.stderr or done.stdout).strip()[:200]}")
    return {"errors": sum(1 for line in done.stdout.splitlines() if ": error:" in line)}


def read_coverage(path, now, max_age_days):
    path = Path(path)
    if not path.is_file():
        raise Unknown(f"no coverage report at {path}")
    age = now - path.stat().st_mtime
    if age > max_age_days * 86400:
        raise Unknown(f"coverage report {path} is {age / 86400:.1f} days old (limit {max_age_days})")
    try:
        total = json.loads(path.read_text(encoding="utf-8"))["totals"]["percent_covered"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Unknown(f"coverage report {path} has no totals.percent_covered") from exc
    invalid_total = f"coverage report {path} has an invalid totals.percent_covered (expected 0 to 100)"
    try:
        percent = float(total)
    except (ValueError, TypeError, OverflowError) as exc:
        raise Unknown(invalid_total) from exc
    if isinstance(total, bool) or not 0 <= percent <= 100:
        raise Unknown(invalid_total)
    return {"percent": round(percent, 2)}


def measure_coverage(python, repo, out):
    """Run the suite under coverage.py and write the JSON report (slow: minutes)."""
    data = Path(out).with_suffix(".data")
    done = _run([python, "-m", "coverage", "run", "--source=src", f"--data-file={data}", "-m", "pytest", "-q"],
                repo, timeout=3000)
    if "No module named" in done.stderr:
        raise Unknown("coverage is not installed for " + python)
    if done.returncode != 0:
        raise Unknown(f"the suite failed under coverage (exit {done.returncode}); no report is written")
    done = _run([python, "-m", "coverage", "json", f"--data-file={data}", "-o", str(out)], repo)
    if done.returncode != 0:
        raise Unknown("coverage json failed: " + done.stderr.strip()[:200])


def judge_counts(name, current, base):
    """Counts may only fall.  Returns (level, text)."""
    worse = {k: (base.get(k, 0), v) for k, v in current.items() if v > base.get(k, 0)}
    if worse:
        return "RED", f"{name} rose: " + ", ".join(f"{k} {b}->{v}" for k, (b, v) in sorted(worse.items()))
    better = sum(base.values()) - sum(current.values())
    if better > 0 or any(k not in current for k in base if base[k]):
        return "AMBER", f"{name} improved by {max(better, 0)} below baseline; lower it with --write-baseline"
    return "GREEN", f"{name} {sum(current.values())} = baseline"


def judge_coverage(current, base):
    now_pct, base_pct = current["percent"], base.get("percent")
    if base_pct is None:
        return "UNKNOWN", f"coverage {now_pct}% has no baseline recorded yet"
    if now_pct < base_pct - COVERAGE_TOLERANCE:
        return "RED", f"coverage fell {base_pct}% -> {now_pct}%"
    if now_pct > base_pct + 1.0:
        return "AMBER", f"coverage {now_pct}% is above baseline {base_pct}%; raise it with --write-baseline"
    return "GREEN", f"coverage {now_pct}% (baseline {base_pct}%)"


def load_baseline(repo):
    try:
        return json.loads((Path(repo) / BASELINE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Unknown(f"{BASELINE} is missing or invalid") from exc


def write_baseline(repo, measured):
    path = Path(repo) / BASELINE
    try:
        base = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        base = {"doc": "Ratchet baselines for checks/code-health.py. Counts may only fall; never widen to pass a change."}
    for facet, current in measured.items():
        old = base.get(facet)
        if facet == "coverage":
            if old and old.get("percent") is not None and current["percent"] < old["percent"]:
                raise ValueError(f"refusing to lower the coverage baseline {old['percent']} -> {current['percent']}")
        elif old:
            raised = {k: (old.get(k, 0), v) for k, v in current.items() if v > old.get(k, 0)}
            if raised:
                raise ValueError(f"refusing to raise the {facet} baseline: {raised}")
        base[facet] = current
    path.write_text(json.dumps(base, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", type=Path, help="site home (holds code-health-venv, the type cache and the coverage report)")
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--only", choices=FACETS, action="append", help="check only this facet (repeatable)")
    ap.add_argument("--tools-python", help="interpreter that has ruff, mypy and coverage installed")
    ap.add_argument("--coverage-json", type=Path, help="coverage.py JSON report (default: SITE/code-health/coverage.json)")
    ap.add_argument("--measure-coverage", action="store_true", help="run the suite under coverage first (slow)")
    ap.add_argument("--max-age-days", type=float, default=9.0)
    ap.add_argument("--write-baseline", action="store_true", help="record the measured counts, lowering only")
    ap.add_argument("--now", type=float, default=None)
    a = ap.parse_args(argv)
    now = time.time() if a.now is None else a.now
    facets = a.only or list(FACETS)
    python = a.tools_python
    if python is None:
        venv = a.home / "code-health-venv" / "bin" / "python" if a.home else None
        python = str(venv) if venv and venv.exists() else sys.executable
    work = (a.home / "code-health") if a.home else Path("/var/tmp/code-health")
    cov_json = a.coverage_json or work / "coverage.json"
    work.mkdir(parents=True, exist_ok=True)
    results, measured = [], {}
    try:
        base = {} if a.write_baseline and not (a.repo / BASELINE).exists() else load_baseline(a.repo)
    except Unknown as exc:
        print(f"code-health: UNKNOWN {exc}", file=sys.stderr)
        return 3
    for facet in facets:
        try:
            if facet == "lint":
                measured[facet] = measure_lint(python, a.repo)
            elif facet == "types":
                measured[facet] = measure_types(python, a.repo, work / "mypy-cache")
            else:
                if a.measure_coverage:
                    measure_coverage(python, a.repo, cov_json)
                measured[facet] = read_coverage(cov_json, now, a.max_age_days)
        except Unknown as exc:
            results.append(("UNKNOWN", f"{facet}: {exc}"))
            continue
        if a.write_baseline:
            continue
        if facet == "coverage":
            results.append(judge_coverage(measured[facet], base.get(facet, {})))
        else:
            results.append(judge_counts(facet, measured[facet], base.get(facet, {})))
    if a.write_baseline:
        if results:
            print("code-health: UNKNOWN cannot write a baseline: " + "; ".join(t for _, t in results), file=sys.stderr)
            return 3
        try:
            write_baseline(a.repo, measured)
        except ValueError as exc:
            print(f"code-health: RED {exc}", file=sys.stderr)
            return 2
        print("code-health: baseline written for " + ", ".join(measured))
        return 0
    order = {"RED": 3, "UNKNOWN": 2, "AMBER": 1, "GREEN": 0}
    level = max((lv for lv, _ in results), key=order.get)
    text = "; ".join(t for _, t in results)
    if level == "RED":
        print(f"code-health: RED {text}", file=sys.stderr)
        return 2
    if level == "UNKNOWN":
        print(f"code-health: UNKNOWN {text}", file=sys.stderr)
        return 3
    print(f"code-health: {level} {text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
