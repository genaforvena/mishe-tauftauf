#!/bin/sh
set -eu

launcher=headless
if [ "${1:-}" = --launcher ]; then launcher="$2"; shift 2; fi
case "$launcher" in headless|tmux) ;; *) echo "usage: $0 --launcher headless|tmux" >&2; exit 2;; esac

root=$(mktemp -d)
home="$root/home"
session="mishe-tauftauf-demo-$$"
cleanup() {
  if [ "$launcher" = tmux ]; then mishe-tauftauf --home "$home" tmux stop --session "$session" >/dev/null 2>&1 || true; fi
  rm -rf "$root"
}
trap cleanup EXIT INT HUP TERM

mishe-tauftauf --home "$home" init >/dev/null
printf 'fixture-reading=42\n' > "$root/device"
cat > "$root/probe" <<EOF
#!/bin/sh
cat "$root/wrong-device"
EOF
chmod +x "$root/probe"
cat > "$home/top-pains/sensor" <<EOF
#!/bin/sh
printf '%s\n' 'DESIRED STATE: stdout contains fixture-reading=42' 'UNRESOLVED: probe must read the existing fixture device'
EOF
cat > "$home/top-pains/unrelated" <<'EOF'
#!/bin/sh
printf '%s\n' 'DESIRED STATE: unrelated is stable' 'unrelated=healthy'
EOF
chmod +x "$home/top-pains/sensor" "$home/top-pains/unrelated"

cat > "$home/minds/default" <<'EOF'
#!/bin/sh
set -eu
home=$MISHE_TAUFTAUF_HOME
slug=$MISHE_TAUFTAUF_SLUG
if [ "$slug" != sensor ]; then
  printf 'No intervention: healthy unrelated observation.\nNext: continue observing.\n' | mishe-tauftauf --home "$home" handoff "$slug" >/dev/null
  exit 0
fi
count_file="$home/mind-count"
count=0; [ ! -f "$count_file" ] || count=$(cat "$count_file")
count=$((count + 1)); printf '%s\n' "$count" > "$count_file"
mishe-tauftauf --home "$home" pain read sensor --launcher "${DEMO_LAUNCHER:-headless}" --session "${DEMO_SESSION:-mishe-tauftauf}" >/dev/null || true
if [ "$count" -eq 1 ]; then
  cat > "$home/plans/fix-probe.md" <<PLAN
# Correct the fixture probe

- [x] Reproduce the wrong-path failure — evidence: observations/sensor exit is nonzero and stderr names wrong-device.
- [ ] Correct the probe to read the existing fixture device — acceptance: source names the device path.
- [ ] Rerun the same check — acceptance: observations/sensor has exit 0 and stdout fixture-reading=42.

Intended observed end state: the live sensor Top Pain shows the real fixture reading.
PLAN
  at=$(date -u -d '2 seconds' +%Y-%m-%dT%H:%M:%SZ)
  cat <<NOTE | mishe-tauftauf --home "$home" predict sensor >/dev/null
Red evidence: the wired check reports the wrong device path.
Hypothesis: correcting the probe path will expose the fixture reading.
Attempt: deliberately inspect without correcting the path in this first experiment.
Expected: the existing failure remains, proving the deadline check is independent of frame change.
Desired state: stdout contains fixture-reading=42.
Plan: plans/fix-probe.md
Check at: $at
NOTE
  printf 'Observed red check and submitted a bounded deliberately insufficient prediction.\nUnresolved: probe path remains wrong.\nNext: due check must wake a fresh invocation.\nPaths: %s/observations/sensor %s/probe\n' "$home" "$MISHE_TAUFTAUF_WORKSPACE" | mishe-tauftauf --home "$home" handoff sensor >/dev/null
else
  probe="$MISHE_TAUFTAUF_WORKSPACE/probe"
  device="$MISHE_TAUFTAUF_WORKSPACE/device"
  cat > "$probe" <<FIX
#!/bin/sh
cat "$device"
FIX
  chmod +x "$probe"
  mishe-tauftauf --home "$home" check sensor -- "$probe" >/dev/null
  cat > "$home/plans/fix-probe.md" <<PLAN
# Correct the fixture probe

- [x] Reproduce the wrong-path failure — evidence: observations/sensor exit was nonzero and stderr named wrong-device.
- [x] Correct the probe to read the existing fixture device — evidence: $probe names $device.
- [x] Rerun the same check — evidence: observations/sensor has exit 0 and stdout fixture-reading=42.

Intended observed end state: the live sensor Top Pain shows the real fixture reading.
PLAN
  printf 'Corrected the probe path and reran the same wired check.\nEvidence: observations/sensor stdout is fixture-reading=42 with exit 0.\nUnresolved: none; coordinator must assess desired state from the live surface.\nNext: continue observation.\nPaths: %s/observations/sensor %s\n' "$home" "$probe" | mishe-tauftauf --home "$home" handoff sensor >/dev/null
fi
EOF
chmod +x "$home/minds/default"

mishe-tauftauf --home "$home" check sensor -- "$root/probe" >/dev/null
printf '%s\n' '=== INITIAL RED SYSTEM ZERO ==='
mishe-tauftauf --home "$home" pain render sensor || true

export DEMO_LAUNCHER="$launcher" DEMO_SESSION="$session"
if [ "$launcher" = tmux ]; then
  mishe-tauftauf --home "$home" tmux start --session "$session" --interval 0.2 >/dev/null
  sleep 1
fi

n=0
while [ "$n" -lt 12 ]; do
  mishe-tauftauf --home "$home" run --once --launcher "$launcher" --session "$session" --judge "$(dirname "$0")/judge.py"
  n=$((n + 1))
  sleep 0.5
done

printf '%s\n' '=== FINAL GREEN SYSTEM ZERO ==='
mishe-tauftauf --home "$home" pain render sensor
printf '%s\n' '=== PREDICTION AND INVOCATION TRACE ==='

python3 - "$home" <<'PY'
from pathlib import Path
import sys
from mishe_tauftauf.feed import Feed
home = Path(sys.argv[1])
entries = Feed(home).entries()
bodies = [entry.body for entry in entries]
for entry in entries:
    if (
        entry.source == "prediction/sensor"
        or entry.body.startswith("prediction ")
        or entry.body.startswith("desired state for prediction ")
        or entry.body.startswith("mind starting top-pain sensor")
        or entry.body.startswith("mind exited top-pain sensor")
    ):
        print(f"{entry.sequence:020d} {entry.source}\n{entry.body}")
starts = [body for body in bodies if body.startswith("mind starting top-pain sensor")]
assert len(starts) == 2, f"expected exactly two sensor Minds, got {len(starts)}"
assert any(": missed" in body for body in bodies if body.startswith("prediction ")), "missing failed deadline"
report = (home / "observations" / "sensor").read_text()
assert "exit: 0" in report and "fixture-reading=42" in report, "real fixture reading not observed green"
assert any("desired-state-met" in body and ": yes " in body for body in bodies), "desired state never established"
print("DEMO PASS: red -> missed prediction -> fresh Mind -> real green reading; unrelated surface stayed intervention-free")
PY
