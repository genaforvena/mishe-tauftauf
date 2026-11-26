# Code health

Three duties watch the source: `code-health-lint` (ruff's pyflakes and syntax rules),
`code-health-types` (mypy over `src/`) and `code-health-coverage` (line coverage of `src/`,
measured weekly by running the suite under coverage.py). Each is a ratchet against
`code-health-baseline.json`: a count above its baseline is RED, below it AMBER until
someone lowers the baseline, and a missing tool or stale report is UNKNOWN, never green.
The steward resolves them. CI runs the same script on one interpreter (Python 3.12).
The coverage checker reports UNKNOWN if the report is unreadable or its total
does not give a finite percentage from 0 to 100.

Set `site` to the absolute path of your site home. From the repository root, create
the tool environment with the same Python version and tool versions as CI.

```sh
python3.12 -m venv "$site/code-health-venv"
"$site/code-health-venv/bin/pip" install ruff==0.17.6 mypy==2.4.1 coverage==7.17.5 pytest pytest-xdist
python3 checks/code-health.py --home "$site" --repo . --only lint
```

After fixing a finding, add `--write-baseline` to record the lower count.
