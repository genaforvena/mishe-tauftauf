#!/usr/bin/env python3
"""Install the corrected host guard, or put the previous bytes back.

The guard is the plant's independent safety boundary, so replacing it is
exact-byte and reversible.  This follows the same pattern as the containment
update in `operator/containment-20261028/update.py`:

* the bytes come from named commits, so what is installed and what is put
  back are both reproducible from the repository;
* applying refuses if the installed file's bytes are not the expected ones,
  so a file that someone else changed is never overwritten;
* `revert` restores the previous bytes exactly, and refuses the same way.

The commit names live in `commits/`, one file each, so the procedure is
reviewable without carrying a hundred kilobytes of base64 in the tree.

Usage:
  sudo python3 apply.py            # install the corrected guard
  sudo python3 apply.py revert     # put the previous guard back
  sudo python3 apply.py check      # report what is installed, without writing
"""
import hashlib
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
COMMITS = HERE / "commits"
GUARD_PATH = "/usr/local/lib/mishe-containment/guard.py"
MODE = 0o644
SOURCE = "src/mishe_tauftauf/host_containment.py"


def read_commits() -> dict:
    """The commit names this tool installs and reverts to.

    Each name lives in its own file, so reading it needs no parser: this
    tool adds no parsing site outside an approved adapter.
    """
    names = {}
    for path in sorted(COMMITS.glob("*.commit")):
        names[path.stem] = path.read_text(encoding="utf-8")
    for required in ("install_commit", "revert_commit"):
        if required not in names:
            raise SystemExit("the commit directory has no " + required)
    return names


def source_at(commit: str) -> bytes:
    return subprocess.run(["git", "-C", str(REPO), "show", f"{commit}:{SOURCE}"],
                          capture_output=True, check=True).stdout


def installed() -> bytes | None:
    path = pathlib.Path(GUARD_PATH)
    if not path.exists():
        return None
    return subprocess.run(["sudo", "-n", "cat", GUARD_PATH],
                          capture_output=True).stdout or None


def main() -> int:
    names = read_commits()
    action = sys.argv[1] if len(sys.argv) > 1 else "apply"
    if action not in {"apply", "revert", "check"}:
        print("usage: apply.py [apply|revert|check]", file=sys.stderr)
        return 2
    before = source_at(names["revert_commit"])
    after = source_at(names["install_commit"])
    current = installed()
    current_digest = hashlib.sha256(current).hexdigest() if current else None
    if action == "check":
        print("installed:", current_digest)
        print("revert to:", hashlib.sha256(before).hexdigest())
        print("install   :", hashlib.sha256(after).hexdigest())
        return 0
    revert = action == "revert"
    expected = after if revert else before
    replacement = before if revert else after
    if current != expected:
        print("refusing changed peer file: " + GUARD_PATH, file=sys.stderr)
        print("installed sha256:", current_digest, file=sys.stderr)
        print("expected  sha256:", hashlib.sha256(expected).hexdigest(), file=sys.stderr)
        return 1
    path = pathlib.Path(GUARD_PATH)
    path.write_bytes(replacement)
    os.chmod(path, MODE)
    subprocess.run(["sudo", "-n", "chown", "0:0", str(path)], check=True)
    print("reverted" if revert else "applied", GUARD_PATH)
    print("sha256:", hashlib.sha256(replacement).hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
