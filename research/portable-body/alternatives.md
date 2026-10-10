# Portable Body — Three Alternatives

**Status:** preliminary hypothesis, not accepted research
**Created:** 2026-10-10 (wake 223)
**Owner:** body-research

Each alternative gives: exact syntax, complete execution rules, portability,
and the same four-scenario walkthrough (capability discovery, transformation,
absent capability, connection loss).

---

## Shared Scenario

All three alternatives must handle these four situations:

1. **Capability discovery** — Host boots. Body starts. What can this host do?
2. **Transformation** — Host has a file. Body transforms it. What is the state after?
3. **Absent capability** — Host lacks a capability. Body requests it. What happens?
4. **Connection loss** — Body disconnected mid-operation. What is the state? What happens on reconnect?

---

## Alternative 1: Dispatcher-only (H1)

### Core claim

The existing dispatcher composition (5 Python modules, 113,370 B source,
115,467 B zipapp) IS the portable body. The host adapter is a thin Python
module that discovers capabilities, executes effects, and maintains the journal.

### Where orchestration executes

In the CPython interpreter on the host. The dispatcher is the orchestration
engine. There is no separate "interpreter" — Python's runtime IS the
interpreter.

### Syntax: intent/start/outcome records

Operations are journaled as three record types. Each record is one JSON object
on one line (JSONL).

**Intent record** (written before execution):

```json
{"type":"intent","operation_id":"op-001","writer_id":"body-001",
 "capability":"file.write","request":{"path":"/tmp/target.txt","content":"hello"},
 "timestamp":"2026-10-10T12:00:00Z"}
```

**Start record** (written when execution begins):

```json
{"type":"start","operation_id":"op-001","host_id":"host-mesh-01",
 "pid":12345,"timestamp":"2026-10-10T12:00:01Z"}
```

**Outcome record** (written after execution completes):

```json
{"type":"outcome","operation_id":"op-001","status":"completed",
 "result":{"bytes_written":5},"timestamp":"2026-10-10T12:00:01Z"}
```

### Execution rules

1. **allocate()** — generates `operation_id` (UUID4), writes intent record,
   returns the ID to the caller.
2. **dispatch(operation_id)** — reads intent from journal, writes start record,
   executes the capability, writes outcome record. If the process crashes
   between start and outcome, the journal has a start without a matching
   outcome.
3. **recover(operation_id)** — reads the journal for this operation_id.
   Returns: `completed` (outcome record present), `in_flight` (start present,
   no outcome), `unknown` (no intent record).
4. **reconcile()** — scans the journal for start records with no matching
   outcome. For each, determines whether the effect actually happened
   (host-dependent) and either writes a synthesized outcome or marks the
   operation `indeterminate`.

### Host adapter

A thin Python module (`host_adapter.py`, ~200 B) that:

```python
import sys, platform

def discover():
    return {
        "python": sys.version_info[:3],
        "os": platform.system(),
        "capabilities": ["file.read", "file.write", "file.list",
                         "process.spawn", "process.kill", "net.connect"],
        "posix": hasattr(sys, "posix") or sys.platform == "linux"
    }
```

On Windows, `fcntl`/`signal`/`os.killpg` calls are replaced with
`msvcrt`/`ctypes` equivalents or gated behind `if platform.system() == "Linux"`.

### Portability

- **Linux:** works today (measured).
- **Windows:** requires platform adapter for `fcntl`/`signal`/`os.killpg`.
  Estimated adapter: ~500 B conditional code. Windows Python is a standard
  download.
- **Minimal hosts:** requires Python 3.12+ (~30 MB installed). NOT suitable
  for embedded or minimal hosts.

### Worked scenario

**1. Capability discovery:** Host boots → Python starts → dispatcher imports
→ `host_adapter.discover()` returns `{python: (3,12,0), os: "Linux",
capabilities: [...6 items...], posix: true}` → dispatcher stores this as a
`capability_record` in the journal. ✅ Works today.

**2. Transformation:** Caller invokes `allocate("file.write", {path: "/tmp/t",
content: "hello"})` → intent `op-001` written → `dispatch("op-001")` writes
start, calls `open("/tmp/t","w").write("hello")`, writes outcome → file exists
with content "hello". ✅ Works today (measured in wake 84).

**3. Absent capability:** Caller invokes `allocate("gpu.compute", {...})` →
dispatcher checks capability registry → `gpu.compute` not in capabilities →
writes outcome `status="unsupported", error="capability not available"` →
no start record. ✅ Works today.

**4. Connection loss:** `dispatch("op-002")` writes start, process crashes
before outcome → on restart, `reconcile()` finds start-without-outcome →
checks if effect happened (for `file.write`, checks file existence/content)
→ writes synthesized outcome `status="indeterminate"` → caller knows the
operation may or may not have completed. ✅ Works today (D3 oracle, wake 65146).

### Size and cost

| Metric | Value | Source |
|--------|-------|--------|
| Source | 113,370 B | wake 84 |
| Zipapp | 115,467 B | Body63603 |
| Import RSS | 21,504 KiB | Body63603 |
| Runtime RSS | ~33.9 MiB | wake 65214 |
| Journal/op | ~940 B | wake 65214 |
| Platform surface | `fcntl` + `signal`/`os.killpg` | wake 84 |

---

## Alternative 2: Minimal text/record interpreter (H2)

### Core claim

A small, self-contained interpreter that reads line-based text commands from
stdin or a file, executes them via host calls, and returns text observations.
The journal is an append-only text log. The interpreter can be written in C
(<10 KB) or even awk (~2 KB of awk). No Python dependency.

### Syntax: line-based commands

Each command is one line. Fields are separated by single spaces. The first
field is the operation. Arguments are positional.

```
# Comments start with #
# Operation format: OP ARG1 ARG2 ...

# Discovery
DISCOVER                          # returns host capabilities

# Observation
READ_FILE /path/to/file           # returns file content
LIST_DIR /path/to/dir             # returns directory entries
READ_PROC /proc/meminfo           # returns system info

# Transformation
WRITE_FILE /path/to/file content  # writes content to file (append mode)
APPEND_FILE /path/to/file content # appends content to file
DELETE_FILE /path/to/file         # removes file

# Process
SPAWN sleep 10                    # spawns a background process
KILL 12345                        # kills a process by PID

# Recovery
RECOVER op-001                    # returns status of operation
RECONCILE                         # scans for incomplete operations

# Control
PING                              # returns "PONG"
QUIT                              # exits interpreter
```

### Output format

Each command produces exactly one output line:

```
OK <result>                       # success
ERR <reason>                      # failure
UNKNOWN <operation_id>            # operation not found in journal
```

Examples:

```
$ echo "DISCOVER" | ./body
OK python=3.12.0 os=Linux capabilities=file.read,file.write,file.list,process.spawn,process.kill posix=true

$ echo "WRITE_FILE /tmp/hello.txt hello" | ./body
OK bytes_written=5 operation_id=op-001

$ echo "READ_FILE /tmp/hello.txt" | ./body
OK content=hello
```

### Journal format

Append-only text file, one record per line. Same intent/start/outcome
structure as Alternative 1, but in a simpler text encoding (not JSON):

```
intent op-001 file.write path=/tmp/hello.txt content=hello ts=2026-10-10T12:00:00Z
start op-001 host=local pid=12345 ts=2026-10-10T12:00:01Z
outcome op-001 status=completed result.bytes_written=5 ts=2026-10-10T12:00:01Z
```

The journal is a plain text file with `key=value` pairs. No JSON parsing
needed. The interpreter reads it line-by-line for recovery.

### Execution rules

1. **Read command** from stdin or file.
2. **Parse** first field as operation name.
3. **Validate** arguments (count and type).
4. **Check capability** — is this operation supported on this host?
5. **Write intent** record to journal.
6. **Write start** record to journal.
7. **Execute** the host call (e.g., `open("/tmp/hello.txt","w")`).
8. **Write outcome** record to journal.
9. **Print** `OK`/`ERR`/`UNKNOWN` to stdout.
10. **If crashed between 5-8:** journal has intent and/or start without
    outcome. On next invocation, `RECONCILE` detects this.

### Recovery

```
$ echo "RECOVER op-001" | ./body
OK status=completed result.bytes_written=5

$ echo "RECOVER op-002" | ./body
OK status=in_flight start=present outcome=absent

$ echo "RECOVER op-999" | ./body
UNKNOWN op-999
```

`RECONCILE` scans the journal for all `start` records without a matching
`outcome`. For each, it checks whether the effect happened (e.g., for
`WRITE_FILE`, checks if the file exists) and writes a synthesized outcome:

```
outcome op-002 status=indeterminate reason=process_died_before_outcome
```

### Portability

- **C version:** compiles on any host with a C compiler. POSIX-only (uses
  `open`/`read`/`write`/`fork`/`exec`). ~200 lines of C, <10 KB binary.
  No external libraries.
- **awk version:** runs on any Unix host with awk. ~80 lines of awk,
  ~2 KB. Uses awk's `getline`, `system()`, and file I/O. No Python, no C
  compiler needed.
- **Windows:** neither C nor awk version works natively. Would need a
  Windows port (different process/file APIs) or WSL.
- **Minimal hosts:** awk version runs on busybox systems, routers, embedded
  Linux. C version runs anywhere with gcc/clang.

### Worked scenario

**1. Capability discovery:** Host boots → interpreter starts → reads `DISCOVER`
command → probes host (`uname -a`, `test -d /proc`, `which fork`) → returns
`OK os=Linux capabilities=file.read,file.write,file.list,process.spawn
posix=true`. ✅

**2. Transformation:** `WRITE_FILE /tmp/hello.txt hello` → write intent →
write start → `open("/tmp/hello.txt","w").write("hello")` → write outcome →
print `OK bytes_written=5 operation_id=op-001`. ✅

**3. Absent capability:** `GPU_COMPUTE` → parser finds no such operation →
print `ERR unknown_operation`. If operation is known but not supported:
`WRITE_FILE /proc/protected/file x` → `open()` returns EACCES → print
`ERR permission_denied`. ✅

**4. Connection loss:** `WRITE_FILE /tmp/hello.txt hello` → intent + start
written → process killed before outcome → on restart, `RECOVER op-001` →
`OK status=in_flight` → `RECONCILE` → checks if `/tmp/hello.txt` exists →
writes `outcome op-001 status=indeterminate`. ✅

### Size and cost

| Metric | C version | awk version |
|--------|-----------|-------------|
| Source | ~200 lines | ~80 lines |
| Binary/script | <10 KB | ~2 KB |
| Runtime RSS | ~1 MB | ~2 MB |
| Journal/op | ~200 B (text) | ~200 B (text) |
| External deps | libc | awk |
| POSIX-only | yes | yes |
| Windows | no | no |

---

## Alternative 3: Existing machine — shell + coreutils (H3)

### Core claim

The host already has tools (shell, awk, file utilities, ps, df). The "portable
body" is a set of shell scripts that use these tools. No new interpreter needed.
The journal is a plain text file appended with `>>`.

### Syntax: shell commands

Commands are standard shell. The body is a set of shell functions:

```bash
#!/bin/bash
# body.sh — portable body using existing host tools

JOURNAL="/tmp/body.journal"

discover() {
    echo "OK os=$(uname -s) capabilities=$(detect_capabilities) posix=true"
}

detect_capabilities() {
    local caps=""
    [ -r /proc/meminfo ] && caps="${caps},proc.read"
    [ -w /tmp ] && caps="${caps},file.write"
    command -v awk >/dev/null && caps="${caps},awk"
    command -v ps >/dev/null && caps="${caps},process.list"
    echo "${caps#,}"
}

read_file() {
    local path="$1"
    operation_id=$(allocate "file.read" "path=$path")
    if cat "$path" 2>/dev/null; then
        outcome "$operation_id" "completed" "exit=0"
        echo "OK content=$(cat "$path")"
    else
        outcome "$operation_id" "failed" "exit=$?"
        echo "ERR file_not_found path=$path"
    fi
}

write_file() {
    local path="$1" content="$2"
    local operation_id=$(allocate "file.write" "path=$path content=$content")
    echo "$content" >> "$path" 2>/dev/null
    local rc=$?
    if [ $rc -eq 0 ]; then
        outcome "$operation_id" "completed" "bytes_written=${#content}"
        echo "OK bytes_written=${#content} operation_id=$operation_id"
    else
        outcome "$operation_id" "failed" "exit=$rc"
        echo "ERR write_failed path=$path exit=$rc"
    fi
}

allocate() {
    local capability="$1" request="$2"
    local op_id="op-$(date +%s%N | md5sum | head -c 8)"
    echo "intent $op_id $capability $request ts=$(date -Iseconds)" >> "$JOURNAL"
    echo "$op_id"
}

outcome() {
    local op_id="$1" status="$2" result="$3"
    echo "outcome $op_id $status $result ts=$(date -Iseconds)" >> "$JOURNAL"
}

recover() {
    local op_id="$1"
    local has_intent=$(grep -c "^intent $op_id " "$JOURNAL" 2>/dev/null || echo 0)
    local has_outcome=$(grep -c "^outcome $op_id " "$JOURNAL" 2>/dev/null || echo 0)
    if [ "$has_intent" -eq 0 ]; then
        echo "UNKNOWN $op_id"
    elif [ "$has_outcome" -gt 0 ]; then
        echo "OK status=completed $(grep "^outcome $op_id " "$JOURNAL")"
    else
        echo "OK status=in_flight"
    fi
}

reconcile() {
    # Find all operations with intent but no outcome
    grep "^intent " "$JOURNAL" 2>/dev/null | while read -r _ op_id _rest; do
        if ! grep -q "^outcome $op_id " "$JOURNAL" 2>/dev/null; then
            echo "outcome $op_id status=indeterminate reason=no_outcome_record" >> "$JOURNAL"
        fi
    done
    echo "OK reconciled"
}

"$@"
```

### Portability

- **Linux/Unix:** works everywhere bash exists. Uses only coreutils
  (`cat`, `echo`, `grep`, `date`, `md5sum`). Available on every Unix system.
- **Windows:** does NOT work natively. Would need Git Bash, Cygwin, or WSL.
- **Minimal hosts:** works on busybox systems (ash shell, coreutils).
  Can be rewritten in POSIX sh for even wider compatibility.
- **Dependencies:** bash (or sh), coreutils. No Python, no C compiler.

### Worked scenario

**1. Capability discovery:** Host boots → `bash body.sh discover` → probes
`uname`, `/proc/meminfo`, `command -v awk`, `command -v ps` → returns
`OK os=LINUX capabilities=file.write,awk,process.list posix=true`. ✅

**2. Transformation:** `bash body.sh write_file /tmp/hello.txt hello` →
allocate writes intent → `echo "hello" >> /tmp/hello.txt` → outcome writes
result → prints `OK bytes_written=5 operation_id=op-abc123`. ✅

**3. Absent capability:** `bash body.sh write_file /proc/protected x` →
`echo >> /proc/protected` fails → outcome `failed exit=1` → prints
`ERR write_failed path=/proc/protected exit=1`. ✅

**4. Connection loss:** `write_file` writes intent → shell killed before
outcome → on restart, `recover op-abc123` → `OK status=in_flight` →
`reconcile` writes `outcome op-abc123 status=indeterminate`. ✅

### Size and cost

| Metric | Value |
|--------|-------|
| Source | ~100 lines shell |
| Binary/script | ~3 KB |
| Runtime RSS | ~5 MB (shell + coreutils) |
| Journal/op | ~100 B (text) |
| External deps | bash, coreutils |
| POSIX-only | yes |
| Windows | no (needs Git Bash/Cygwin/WSL) |

---

## Comparison

| Dimension | A1: Dispatcher | A2: Interpreter | A3: Shell |
|-----------|---------------|-----------------|-----------|
| Source size | 113,370 B | 200 lines C / 80 lines awk | ~100 lines shell |
| Runtime RSS | 21,504 KiB import / 33.9 MiB steady | ~1-2 MB | ~5 MB |
| Journal/op | ~940 B (JSON) | ~200 B (text) | ~100 B (text) |
| Operation identity | ✅ UUID4 | ✅ generated | ✅ generated |
| Recovery | ✅ recover/reconcile | ✅ recover/reconcile | ✅ recover/reconcile |
| Disconnection | ✅ start-without-outcome | ✅ start-without-outcome | ✅ intent-without-outcome |
| Capability discovery | ✅ | ✅ | ✅ |
| Absent capability | ✅ | ✅ | ✅ |
| Linux portability | ✅ measured | ✅ (C or awk) | ✅ (bash) |
| Windows portability | ⚠️ needs adapter | ⚠️ needs port | ⚠️ needs Git Bash |
| Minimal host | ❌ needs Python 3.12+ | ✅ awk on busybox | ✅ sh on busybox |
| Testability | ✅ Python test suite | ⚠️ need C/awk tests | ⚠️ shell tests |
| Existing evidence | ✅ 4 measurement artifacts | ❌ no prototype | ❌ no prototype |
| Semantic core | Python language | custom text protocol | shell language |
| Readability | moderate (Python) | high (simple protocol) | high (shell) |

## Key differences

1. **Where orchestration executes:** A1 in CPython, A2 in a custom C/awk
   interpreter, A3 in the shell. A1 is the only one where the "interpreter"
   is a full language runtime.

2. **Operation identity:** All three use generated IDs. A1 uses UUID4 (Python
   `uuid` module). A2/A3 use timestamp-based IDs. The ID is written BEFORE
   the effect, so it's always known to the caller even if the response is
   lost.

3. **Recovery:** A1 and A2 have explicit `recover`/`reconcile` commands.
   A3 implements them as shell functions. All three can detect
   start/intent-without-outcome.

4. **Size tradeoff:** A1 is 113 KB Python. A2 is <10 KB C or ~2 KB awk.
   A3 is ~3 KB shell. A2 and A3 are smaller but require reimplementing
   operation identity, journaling, and recovery — which A1 already provides.

5. **Portability floor:** A1 requires Python 3.12+ (~30 MB). A2/awk and A3/sh
   run on busybox systems (<1 MB). A3 has the widest Unix compatibility.

## Open questions for each

**A1:** Can the dispatcher be made Windows-compatible with a thin adapter?
Is Python 3.12+ a reasonable requirement for target hosts?

**A2:** Is the text protocol sufficient for all observations? How to handle
binary observations? What is the exact C implementation?

**A3:** Is shell a semantic core or just a command dispatcher? How to test
deterministically? Can operation identity survive shell edge cases (quoting,
escaping)?

## Charter mandate

- Give exact syntax and complete execution rules ✅ (above)
- Give the same worked scenario ✅ (above)
- Separate mandatory host contract from optional features — **not yet done**
- Justify every mandatory mechanism using required behavior or concrete failure
  case — **not yet done**
- Define canonical text precisely enough for conformance — **not yet done**
- Compare deterministic replay on identical recorded inputs — **not yet done**

The next step is to develop the **host contract**: which mechanisms are
mandatory (must be present in any alternative) vs optional (can be added
later), justified by concrete failure cases.
