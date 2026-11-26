# Code health

Three duties watch the source: `code-health-lint` (ruff's pyflakes and syntax rules),
`code-health-types` (mypy over `src/`) and `code-health-coverage` (line coverage of `src/`,
measured weekly by running the suite under coverage.py). Each is a ratchet against
`code-health-baseline.json`: a count above its baseline is RED, below it AMBER until
someone lowers the baseline, and a missing tool or stale report is UNKNOWN, never green.
The steward resolves them. CI runs the same script on one interpreter (Python 3.12).

To fix a finding, run `python checks/code-health.py --repo . --only lint` and repair it, then
record the gain with `--write-baseline`, which only ever lowers a count. The tools live in
`SITE/code-health-venv`:

```sh
python -m venv SITE/code-health-venv
SITE/code-health-venv/bin/pip install ruff mypy coverage pytest pytest-xdist
```
