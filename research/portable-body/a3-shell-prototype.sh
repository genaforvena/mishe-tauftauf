#!/bin/bash
# a3-shell-prototype.sh — A3 portable body: existing machine (shell + coreutils)
#
# Wake 814 rebuild. Closes gap plan items 2-7 from the witness DM 485 review
# of the wake-405 prototype:
#   2. Duplicate op-ID policy: reject an already-taken ID before effect
#      dispatch; pending reservation (the intent record) plus retained-store
#      scan (grep the journal for the generated ID).
#   3. Journal corruption detection: malformed or torn records fail closed
#      as journal_corrupt; outcome status enumeration validated
#      (ok partial denied unsupported unavailable error unknown).
#   4. Discovery states: every capability reported as
#      available / absent / denied / unknown.
#   5. Reconcile classification: each intent-without-outcome is classified
#      complete (ok) / partial / unknown via effect verification.
#   6. Text boundary: every record carries v=1; records are bounded
#      (MAX_RECORD bytes); a torn final line (crash mid-append) is detected
#      and fails closed.
#   7. Absent-capability states: unsupported (absent from host set),
#      denied (host OS refuses), error (other host failure),
#      unavailable (required host resource absent).
# Item 8 (concurrency) intentionally NOT implemented: optional per
# host-contract.md "Optional Features" — the minimal body handles one
# operation at a time. Item 1 (host authority) closed by decision:
# host OS enforcement only, no call-time policy layer (host-contract.md M8).
#
# Contract: research/portable-body/host-contract.md M1-M8.
# Journal: append-only text, space-free key=value records, one per line.
# Statuses: ok partial denied unsupported unavailable error unknown.

set -u

JOURNAL="${A3_JOURNAL:-/tmp/a3-body.journal}"
COUNTER="${JOURNAL}.counter"
ERR_FILE="/tmp/a3-body.err.$$"
MAX_RECORD=512
VERSION=1

# ---------- journal primitives ----------

# jwrite: append one bounded record. Refuse records over MAX_RECORD bytes.
jwrite() {
    local record="$1"
    if [ "${#record}" -gt "$MAX_RECORD" ]; then
        echo "ERR record_too_large size=${#record} max=$MAX_RECORD" >&2
        return 1
    fi
    printf '%s\n' "$record" >> "$JOURNAL"
}

# jvalidate: fail closed on malformed or torn records.
#   - every line must match the v=1 record grammar
#   - the file must end with a newline; a torn final line means a crash
#     mid-append, i.e. an incomplete record -> journal_corrupt
jvalidate() {
    [ -f "$JOURNAL" ] || return 0
    if [ -s "$JOURNAL" ]; then
        local last
        last=$(tail -c 1 "$JOURNAL" | od -An -tx1 | tr -d ' \n')
        [ "$last" = "0a" ] || {
            echo "journal_corrupt: torn final line (no trailing newline)" >&2
            return 1
        }
    fi
    local bad
    bad=$(grep -nvE "^(intent op=[A-Za-z0-9._-]+ cap=[A-Za-z0-9._-]+ v=$VERSION|outcome op=[A-Za-z0-9._-]+ status=(ok|partial|denied|unsupported|unavailable|error|unknown) v=$VERSION)( [A-Za-z0-9._-]+=[^ ]*)*$" "$JOURNAL" 2>/dev/null | head -n 1)
    [ -z "$bad" ] || { echo "journal_corrupt: malformed record: $bad" >&2; return 1; }
    return 0
}

# ---------- operation identity (gap item 2) ----------

# Counter-based IDs. The counter file is local state; the journal is the
# retained store. allocate() scans the retained store before accepting an ID:
# if the journal already holds an intent for the generated ID, reject.
generate_op_id() {
    local n=1
    if [ -f "$COUNTER" ]; then
        n=$(cat "$COUNTER" 2>/dev/null)
        case "$n" in ''|*[!0-9]*) n=1 ;; esac
    fi
    echo "op-$(printf '%08d' "$n")"
    printf '%08d' "$((n + 1))" > "$COUNTER"
}

# ---------- journal records (gap items 3, 6) ----------

# outcome: write an outcome record; status must be in the contract
# enumeration (ok partial denied unsupported unavailable error unknown).
outcome() {
    local op_id="$1" status="$2" result="$3"
    case "$status" in
        ok|partial|denied|unsupported|unavailable|error|unknown) ;;
        *) echo "ERR invalid_status status=$status" >&2; return 1 ;;
    esac
    jwrite "outcome op=$op_id status=$status v=$VERSION $result"
}

# allocate: generate ID, reject duplicates (pending reservation +
# retained-store scan), write intent before any effect.
allocate() {
    local capability="$1" request="$2"
    local op_id
    op_id=$(generate_op_id)
    if grep -q "^intent op=$op_id " "$JOURNAL" 2>/dev/null; then
        echo "ERR duplicate_op_id op=$op_id" >&2
        return 1
    fi
    jwrite "intent op=$op_id cap=$capability v=$VERSION $request" || return 1
    echo "$op_id"
}

# recover: query by operation ID. Fails closed on journal corruption.
# Classification: unknown (no intent) / partial (intent, no outcome) /
# completed (outcome present).
recover() {
    local op_id="$1"
    jvalidate || { echo "ERR journal_corrupt op=$op_id"; return 1; }
    local n_intent n_outcome
    n_intent=$(grep -c "^intent op=$op_id " "$JOURNAL" 2>/dev/null || true)
    n_outcome=$(grep -c "^outcome op=$op_id " "$JOURNAL" 2>/dev/null || true)
    if [ "${n_intent:-0}" -eq 0 ]; then
        echo "unknown op=$op_id"
    elif [ "${n_outcome:-0}" -gt 0 ]; then
        grep -m1 "^outcome op=$op_id " "$JOURNAL"
    else
        echo "partial op=$op_id status=in_flight"
    fi
}

# ---------- discovery (gap item 4) ----------

# probe_file_cap: file capability with available/absent/denied states.
# Path absent -> absent; path exists but not readable/writable -> denied.
probe_file_cap() {
    local name="$1" op="$2" path="$3"
    [ -e "$path" ] || { echo "$name=absent"; return; }
    if [ "$op" "$path" ]; then
        echo "$name=available"
    else
        echo "$name=denied"
    fi
}

# discover: report the body's executable capability set with per-capability
# state (available / absent / denied / unknown) and the record version.
discover() {
    local caps="" cap
    if command -v sed >/dev/null 2>&1 && sed --version >/dev/null 2>&1; then
        caps="filter=available"
    elif ! command -v sed >/dev/null 2>&1; then
        caps="filter=absent"
    else
        caps="filter=unknown"
    fi
    for cap in "$(probe_file_cap fs.read -r /proc/meminfo)" \
                "$(probe_file_cap fs.write -w /tmp)"; do
        caps="$caps,$cap"
    done
    echo "os=$(uname -s) caps=$caps v=$VERSION"
}
# field: extract the value of key from a space-free key=value request.
field() {
    local key="$1" req="$2"
    printf '%s' "$req" | grep -o "$key=[^ ]*" | head -n 1 | cut -d= -f2-
}

# classify_failure: the host call failed. denied when the host reports
# permission denied; otherwise error.
classify_failure() {
    local op_id="$1" cap="$2"
    if grep -qi "permission denied" "$ERR_FILE" 2>/dev/null; then
        outcome "$op_id" denied "reason=host_denied cap=$cap"
        echo "ERR denied op=$op_id cap=$cap" >&2
    else
        outcome "$op_id" error "reason=host_error cap=$cap"
        echo "ERR error op=$op_id cap=$cap" >&2
    fi
    return 1
}

# execute: run one capability effect. Capabilities outside the host set are
# absent -> unsupported; host OS refusals -> denied; missing host resources
# -> unavailable; other host failures -> error.
execute() {
    local op_id="$1" capability="$2" request="$3"
    local path content result_path data
    case "$capability" in
        filter)
            path=$(field path "$request")
            result_path=$(field result "$request")
            if ( sed '/^$/d' "$path" > "$result_path" ) 2>"$ERR_FILE"; then
                outcome "$op_id" ok "result=$result_path"
                echo "ok op=$op_id result=$result_path"
            else
                classify_failure "$op_id" "$capability"
            fi
            ;;
        fs.read)
            path=$(field path "$request")
            data=$(cat "$path" 2>"$ERR_FILE") && {
                outcome "$op_id" ok "bytes_read=${#data}"
                echo "ok op=$op_id content=$data"
                return 0
            }
            classify_failure "$op_id" "$capability"
            ;;
        fs.write)
            path=$(field path "$request")
            content=$(field content "$request")
            if [ ! -d "$(dirname "$path")" ]; then
                outcome "$op_id" unavailable "reason=host_resource_absent path=$path"
                echo "ERR unavailable op=$op_id path=$path" >&2
                return 1
            fi
            if ( printf '%s\n' "$content" >> "$path" ) 2>"$ERR_FILE"; then
                outcome "$op_id" ok "bytes_written=$((${#content} + 1))"
                echo "ok op=$op_id bytes_written=$((${#content} + 1))"
            else
                classify_failure "$op_id" "$capability"
            fi
            ;;
        *)
            outcome "$op_id" unsupported "reason=capability_absent cap=$capability"
            echo "ERR unsupported op=$op_id cap=$capability" >&2
            return 1
            ;;
    esac
}

# ---------- reconcile (gap item 5) ----------

# reconcile: scan the journal for intent-without-outcome records, classify
# each as complete (ok) / partial / unknown by verifying the effect, and
# write a synthesized outcome. Fails closed on journal corruption.
reconcile() {
    jvalidate || { echo "ERR journal_corrupt" >&2; return 1; }
    local ops op_id rest cap status reason path content result_path
    ops=$(grep -o '^intent op=[^ ]*' "$JOURNAL" 2>/dev/null | cut -d= -f2 | sort -u)
    for op_id in $ops; do
        if ! grep -q "^outcome op=$op_id " "$JOURNAL" 2>/dev/null; then
            rest=$(grep -m1 "^intent op=$op_id " "$JOURNAL")
            cap=$(printf '%s' "$rest" | grep -o 'cap=[^ ]*' | head -n 1 | cut -d= -f2)
            status=unknown
            reason=cannot_verify
            case "$cap" in
                fs.write)
                    path=$(field path "$rest")
                    content=$(field content "$rest")
                    if [ -f "$path" ] && grep -qF -- "$content" "$path" 2>/dev/null; then
                        status=ok
                        reason=effect_verified
                    elif [ -e "$path" ]; then
                        status=partial
                        reason=effect_mismatch
                    else
                        reason=effect_absent
                    fi
                    ;;
                filter)
                    result_path=$(field result "$rest")
                    if [ -f "$result_path" ] && [ -s "$result_path" ]; then
                        status=ok
                        reason=effect_verified
                    elif [ -e "$result_path" ]; then
                        status=partial
                        reason=effect_empty
                    else
                        reason=effect_absent
                    fi
                    ;;
                *)
                    reason=no_durable_effect
                    ;;
            esac
            jwrite "outcome op=$op_id status=$status v=$VERSION reason=$reason"
        fi
    done
    echo "ok reconciled journal=$JOURNAL"
}

# ---------- dispatch ----------

cmd="${1:-}"
[ $# -gt 0 ] && shift
case "$cmd" in
    discover) discover ;;
    allocate)
        [ $# -ge 2 ] || { echo "ERR usage: allocate <capability> <request>" >&2; exit 1; }
        allocate "$@"
        ;;
    execute)
        [ $# -ge 3 ] || { echo "ERR usage: execute <op_id> <capability> <request>" >&2; exit 1; }
        execute "$@"
        ;;
    outcome)
        [ $# -ge 3 ] || { echo "ERR usage: outcome <op_id> <status> <result>" >&2; exit 1; }
        outcome "$@"
        ;;
    recover)
        [ $# -ge 1 ] || { echo "ERR usage: recover <op_id>" >&2; exit 1; }
        recover "$@"
        ;;
    reconcile) reconcile ;;
    *) echo "ERR unknown_command cmd=$cmd" >&2; exit 1 ;;
esac
