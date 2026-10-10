#!/usr/bin/env python3
"""a3-shell-test-harness.py — tests for the extended A3 shell prototype.

Covers host-contract.md M1-M8 as implemented by a3-shell-prototype.sh,
including gap plan items 2-7 from the witness DM 485 review.

Each test drives the real script in a temporary journal and asserts on
observed stdout, stderr, exit status, journal bytes, or effect files.
"""

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "a3-shell-prototype.sh")

GRAMMAR = re.compile(
    r"^(intent op=[A-Za-z0-9._-]+ cap=[A-Za-z0-9._-]+ v=1"
    r"|outcome op=[A-Za-z0-9._-]+ "
    r"status=(ok|partial|denied|unsupported|unavailable|error|unknown) v=1)"
    r"( [A-Za-z0-9._-]+=[^ ]*)*$"
)

VALID_STATUSES = ["ok", "partial", "denied", "unsupported",
                  "unavailable", "error", "unknown"]
VALID_STATES = ["available", "absent", "denied", "unknown"]

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS {name}")
    else:
        failed += 1
        print(f"FAIL {name} {detail}")


class Body:
    """One prototype instance with its own journal."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="a3-test-")
        self.journal = os.path.join(self.dir, "journal")
        self.env = dict(os.environ, A3_JOURNAL=self.journal)

    def run(self, *args):
        return subprocess.run(["bash", SCRIPT, *args],
                              capture_output=True, text=True, env=self.env)

    def journal_lines(self):
        if not os.path.exists(self.journal):
            return []
        with open(self.journal, encoding="utf-8") as f:
            return f.read().splitlines()

    def journal_text(self):
        with open(self.journal, encoding="utf-8") as f:
            return f.read()

    def set_counter(self, n):
        with open(self.journal + ".counter", "w") as f:
            f.write(str(n))

    def append_raw(self, line, newline=True):
        with open(self.journal, "a", encoding="utf-8") as f:
            f.write(line + ("\n" if newline else ""))

    def write_intent(self, op_id, cap, request):
        self.append_raw(f"intent op={op_id} cap={cap} v=1 {request}")

    def path(self, name):
        return os.path.join(self.dir, name)

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def parse_caps(out):
    m = re.search(r"caps=(\S+)", out)
    if not m:
        return {}
    return dict(pair.split("=", 1) for pair in m.group(1).split(","))


def main():
    b = Body()
    try:
        # --- discovery: states + version (gap item 4) ---
        r = b.run("discover")
        out = r.stdout.strip()
        check("discovery-exit0", r.returncode == 0, r.stderr)
        caps = parse_caps(out)
        check("discovery-three-caps",
              set(caps) == {"filter", "fs.read", "fs.write"}, str(caps))
        check("discovery-states-valid",
              all(s in VALID_STATES for s in caps.values()), str(caps))
        check("discovery-versioned", "v=1" in out, out)
        check("discovery-os", out.startswith("os="), out)

        # --- transform: filter (removes empty lines) ---
        src = b.path("input.txt")
        dst = b.path("output.txt")
        with open(src, "w") as f:
            f.write("alpha\n\nbeta\n\ngamma\n")
        r = b.run("allocate", "filter", f"path={src} result={dst}")
        op1 = r.stdout.strip()
        check("allocate-returns-id",
              r.returncode == 0 and re.fullmatch(r"op-\d{8}", op1) is not None,
              r.stdout + r.stderr)
        r = b.run("execute", op1, "filter", f"path={src} result={dst}")
        check("transform-filter-exit0", r.returncode == 0, r.stderr)
        with open(dst) as f:
            result = f.read()
        check("transform-filter-removes-empty-lines",
              result == "alpha\nbeta\ngamma\n", repr(result))
        r = b.run("recover", op1)
        check("transform-recover-completed",
              r.returncode == 0 and "status=ok" in r.stdout, r.stdout)

        # --- transform: fs.write ---
        tgt = b.path("written.txt")
        r = b.run("allocate", "fs.write", f"path={tgt} content=hello")
        op2 = r.stdout.strip()
        r = b.run("execute", op2, "fs.write", f"path={tgt} content=hello")
        check("transform-fswrite-exit0", r.returncode == 0, r.stderr)
        with open(tgt) as f:
            written = f.read()
        check("transform-fswrite-content", written == "hello\n", repr(written))

        # --- recovery classification (gap item 3) ---
        r = b.run("recover", "op-99999999")
        check("recover-unknown", r.stdout.startswith("unknown op=op-99999999"),
              r.stdout)
        op3 = b.run("allocate", "fs.read", f"path={src}").stdout.strip()
        r = b.run("recover", op3)
        check("recover-in-flight-partial",
              r.stdout.startswith("partial op=") and "in_flight" in r.stdout,
              r.stdout)

        # --- gap item 2: duplicate op-ID policy ---
        b.append_raw("intent op=op-00000001 cap=fs.write v=1 path=/x content=y")
        b.set_counter(1)
        before = b.journal_lines()
        r = b.run("allocate", "fs.write", "path=/tmp/x content=y")
        check("duplicate-rejected",
              r.returncode != 0 and "duplicate_op_id" in r.stderr,
              r.stdout + r.stderr)
        check("duplicate-no-intent-written", b.journal_lines() == before)

        # counter still advances past the rejected ID (no infinite loop);
        # use a free counter value (journal already holds op-00000002)
        b.set_counter(50)
        op4 = b.run("allocate", "fs.read", f"path={src}").stdout.strip()
        check("counter-advances-after-reject", op4 == "op-00000050", op4)

        # --- gap item 3: journal corruption detection, fail closed ---
        r = b.run("outcome", op4, "ok", "bytes_read=2")
        check("outcome-valid-status", r.returncode == 0, r.stderr)
        b.append_raw("this is not a record")
        r = b.run("recover", op4)
        check("corrupt-malformed-fail-closed",
              r.returncode != 0 and "journal_corrupt" in r.stderr,
              r.stdout + r.stderr)
        # remove the malformed line; now a torn final line
        with open(b.journal, "w", encoding="utf-8") as f:
            f.write("".join(line + "\n"
                            for line in b.journal_lines()[:-1]))
        b.append_raw("intent op=op-00000009 cap=fs.write v=1 path=x",
                     newline=False)
        r = b.run("recover", op4)
        check("corrupt-torn-fail-closed",
              r.returncode != 0 and "journal_corrupt" in r.stderr,
              r.stdout + r.stderr)

        # --- gap item 3: status enumeration ---
        b2 = Body()
        try:
            r = b2.run("outcome", "op-x", "bogus", "reason=x")
            check("status-invalid-rejected",
                  r.returncode != 0 and "invalid_status" in r.stderr,
                  r.stdout + r.stderr)
            for st in VALID_STATUSES:
                r = b2.run("outcome", "op-x", st, f"reason={st}")
                check(f"status-{st}-accepted", r.returncode == 0, r.stderr)
        finally:
            b2.cleanup()

        # --- gap item 6: record bound ---
        big = "x" * 600
        r = b.run("allocate", "fs.write", f"path=/tmp/x content={big}")
        check("record-too-large-rejected",
              r.returncode != 0 and "record_too_large" in r.stderr,
              r.stdout + r.stderr)

        # --- gap item 7: absent / denied / unavailable / error ---
        r = b.run("execute", "op-77777777", "gpu.compute", "x=1")
        check("absent-capability-unsupported",
              r.returncode != 0 and "unsupported" in r.stderr,
              r.stdout + r.stderr)
        noperm = b.path("noperm.txt")
        with open(noperm, "w") as f:
            f.write("data\n")
        os.chmod(noperm, 0)
        try:
            r = b.run("execute", "op-77777778", "fs.write",
                      f"path={noperm} content=more")
            check("denied-capability",
                  r.returncode != 0 and "denied" in r.stderr,
                  r.stdout + r.stderr)
            with open(b.journal, encoding="utf-8") as f:
                check("denied-outcome-recorded",
                      "status=denied" in f.read())
        finally:
            os.chmod(noperm, stat.S_IRUSR | stat.S_IWUSR)
        r = b.run("execute", "op-77777779", "fs.write",
                  f"path={b.path('no-such-dir')}/f content=x")
        check("unavailable-capability",
              r.returncode != 0 and "unavailable" in r.stderr,
              r.stdout + r.stderr)

        # --- gap item 5: reconcile classification ---
        b3 = Body()
        try:
            tgt = b3.path("recon.txt")
            with open(tgt, "w") as f:
                f.write("verified\n")
            b3.write_intent("op-recon-1", "fs.write", f"path={tgt} content=verified")
            r = b3.run("reconcile")
            check("reconcile-exit0", r.returncode == 0, r.stderr)
            with open(b3.journal, encoding="utf-8") as f:
                lines = f.read().splitlines()
            got = [l for l in lines if l.startswith("outcome op=op-recon-1")]
            check("reconcile-complete-classified",
                  len(got) == 1 and "status=ok" in got[0]
                  and "effect_verified" in got[0], str(got))

            tgt2 = b3.path("mismatch.txt")
            with open(tgt2, "w") as f:
                f.write("other\n")
            b3.write_intent("op-recon-2", "fs.write", f"path={tgt2} content=verified")
            b3.run("reconcile")
            with open(b3.journal, encoding="utf-8") as f:
                lines = f.read().splitlines()
            got = [l for l in lines if l.startswith("outcome op=op-recon-2")]
            check("reconcile-partial-classified",
                  len(got) == 1 and "status=partial" in got[0]
                  and "effect_mismatch" in got[0], str(got))

            b3.write_intent("op-recon-3", "fs.read", "path=/etc/hostname")
            b3.run("reconcile")
            with open(b3.journal, encoding="utf-8") as f:
                lines = f.read().splitlines()
            got = [l for l in lines if l.startswith("outcome op=op-recon-3")]
            check("reconcile-unknown-classified",
                  len(got) == 1 and "status=unknown" in got[0]
                  and "no_durable_effect" in got[0], str(got))

            # connection-loss walkthrough: intent, crash (no outcome),
            # recover -> partial, reconcile -> classified
            b3.write_intent("op-recon-4", "fs.write",
                           f"path={tgt} content=verified")
            r = b3.run("recover", "op-recon-4")
            check("connection-loss-recover-partial",
                  r.stdout.startswith("partial op="), r.stdout)
            b3.run("reconcile")
            with open(b3.journal, encoding="utf-8") as f:
                lines = f.read().splitlines()
            got = [l for l in lines if l.startswith("outcome op=op-recon-4")]
            check("connection-loss-reconcile-ok",
                  len(got) == 1 and "status=ok" in got[0], str(got))
            # reconcile is idempotent: no second outcome for op-recon-4
            b3.run("reconcile")
            with open(b3.journal, encoding="utf-8") as f:
                lines = f.read().splitlines()
            got = [l for l in lines if l.startswith("outcome op=op-recon-4")]
            check("reconcile-idempotent", len(got) == 1, str(got))
        finally:
            b3.cleanup()

        # --- gap item 6: text boundary grammar over the whole journal ---
        bad = [l for l in b.journal_lines() if not GRAMMAR.match(l)]
        check("text-boundary-grammar", not bad, str(bad[:3]))

        # --- durability + UTF-8 ---
        try:
            b.journal_text().encode("utf-8")
            check("journal-utf8", True)
        except UnicodeEncodeError:
            check("journal-utf8", False)

        # --- script size (informational bound) ---
        size = os.path.getsize(SCRIPT)
        check("script-size-bounded", size <= 12288, f"{size} B")
        print(f"INFO script-size bytes={size}")
    finally:
        b.cleanup()

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
