"""Small patch gate: exact scoped bytes, tests, System 1 review, activation/revert."""
from __future__ import annotations
import argparse
import base64
import difflib
from datetime import datetime, timezone
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess

from .argv_text import decode_argv
from .git_base_text import committed_snapshot
from .observations import validate_slug
from .post_check import _save, _worker
from .wall import settings

WORKER_SETTLE_SECONDS = 60
"""Extra outer budget so the reviewer can settle after its own model-call budget."""
def verified_delivery(record: dict) -> bool:
    """A patch record that is a verified delivery, not merely a set boolean.

    ``verify`` writes ``delivery_verified=True`` together with the
    ``verification`` digest and time, and no other producer sets the boolean
    true, so the binding is the evidence and the boolean alone is not. A record
    carrying only the flag is a hand-edit or a torn write; every consumer shares
    this test so none renders or counts it as verified delivery
    (docs/wall-coordination.md: "Only a successful exercise records
    ``delivery_verified=true``, the exact patch digest and verification time").
    """
    return bool(record.get("delivery_verified")) and isinstance(record.get("verification"), dict)


def snapshot(path):
    if path.is_symlink():
        raise ValueError(f"symlink cannot be a patch target: {path}")
    return {"bytes": base64.b64encode(path.read_bytes()).decode(), "mode": path.stat().st_mode & 0o777} if path.exists() else None


_UNSET = object()


def restore(path, value, expected=_UNSET):
    current = snapshot(path)
    if expected is not _UNSET and current != expected:
        raise ValueError(f"runtime target changed before replacement: {path}")
    if value is None:
        if current is not None:
            path.unlink()
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name("." + path.name + ".wall-patch")
        temporary.write_bytes(base64.b64decode(value["bytes"]))
        temporary.chmod(value["mode"])
        temporary.replace(path)


def location(home, identity):
    validate_slug(identity)
    return home / "patches" / f"{identity}.json"


@contextmanager
def lock(home):
    with (home / ".wall-patch.lock").open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


def refuse_inside_own_wake(home):
    """Refuse activation from inside a wake while that wake holds the dispatch lock.

    A wake holds the dispatch lock for its whole run.  An apply or revert that
    runs inside it can only wait for the lock its own wake will not release
    until the apply returns, so it times out and aborts without applying (seen
    2026-11-08 08:42Z and 09:12Z).  Inside a wake the lock is busy by
    definition, so the refusal is immediate instead of a long wait.  Outside a
    wake, or when the lock is free, activation proceeds.
    """
    if not os.environ.get("MISHE_WAKE_KEY"):
        return
    from .core.state import DispatchStore
    with DispatchStore(Path(home) / "dispatch").lock_path.open("a+") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError(
                f"activation refused: wake {os.environ['MISHE_WAKE_KEY']} holds the dispatch lock; "
                "run the delivery after the wake settles (mishe release request or a later wake)"
            ) from None
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def status(home, identity):
    return json.loads(location(home, identity).read_text())


def target(root, name):
    path = root / name
    if Path(name).is_absolute() or ".." in Path(name).parts or name.startswith(".") or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"invalid patch path: {name}")
    return path


def run(home, identity, label, command):
    if not command or not all(isinstance(x, str) for x in command):
        raise ValueError("command must be a nonempty argument list")
    result = subprocess.run(command, cwd=home.parent, capture_output=True, timeout=300)
    out = home / "patches" / f"{identity}-{label}.log"
    out.write_bytes(result.stdout + b"\nSTDERR\n" + result.stderr)
    return {"command": command, "code": result.returncode, "log": str(out)}


def command_artifacts(home, commands):
    """Bind owned command scripts; system executables are outside the scoped patch."""
    artifacts = {}
    for command in commands:
        if command is None:
            continue
        if not command or not all(isinstance(x, str) for x in command):
            raise ValueError("delivery command must be a nonempty argument list")
        skip_next = False
        for arg in command:
            if skip_next:
                skip_next = False
                continue
            if arg in {"-c", "-m"}:
                skip_next = True
                continue
            path = Path(arg)
            if not path.is_absolute():
                path = home.parent / path
            if path.resolve().is_relative_to(home.parent.resolve()) and path.is_file():
                artifacts[str(path.resolve())] = snapshot(path)
    return artifacts


def delivery_plan(home, activate, observe, revert_observe=None, revert=None):
    """``revert`` is the argv that restores the old consumer pin. Absent, the
    revert re-runs ``activate`` (legacy plans, which were recorded that way)."""
    plan = {"activate": activate, "observe": observe, "revert_observe": revert_observe}
    if revert is not None:
        plan["revert"] = revert
    plan["artifacts"] = command_artifacts(home, [activate, observe, revert_observe, revert])
    return plan


def validate_plan(home, record, activate, observe):
    plan = record["delivery_plan"]
    if plan != delivery_plan(home, activate, observe, plan.get("revert_observe"), revert=plan.get("revert")):
        raise ValueError("delivery command or script changed since review; recheck")


def _in_checkout(home, arg):
    path = Path(arg)
    return path if path.is_absolute() else home.parent / path


def _executable(home, command):
    """Resolve the program and a script argument as ``run`` will, without running it.

    ``run`` executes with cwd = the checkout, so a relative path is resolved
    there. A missing program or script must fail before admission, not after
    the runtime bytes have moved.
    """
    if not command or not all(isinstance(x, str) for x in command):
        raise ValueError("command must be a nonempty argument list")
    words = list(command)
    if words[0] == "env":
        words = words[1:]
        while words and "=" in words[0]:
            words = words[1:]
    if not words:
        raise ValueError(f"command has no program: {command}")
    program = words[0]
    if "/" in program:
        found = _in_checkout(home, program)
        if not (found.is_file() and os.access(found, os.X_OK)):
            raise ValueError(f"program is not an executable file: {program}")
    elif shutil.which(program) is None:
        raise ValueError(f"program is not on PATH: {program}")
    if Path(program).name in {"bash", "sh"} or Path(program).name[:6] == "python":
        for arg in words[1:]:
            if arg in {"-c", "-m"}:
                break
            if arg[:1] == "-":
                continue
            if "/" in arg or Path(arg).suffix in (".sh", ".py"):
                if not _in_checkout(home, arg).is_file():
                    raise ValueError(f"script not found: {arg}")
            break


def preflight(home, plan):
    """Every delivery command in a plan must resolve before any admission or effect."""
    for key in ("activate", "observe", "revert_observe", "revert"):
        if plan.get(key) is not None:
            _executable(Path(home), plan[key])


def patch_hash(record):
    return hashlib.sha256(json.dumps({name: values["after"] for name, values in record["files"].items()}, sort_keys=True).encode()).hexdigest()


def _worker_timeout(config) -> float:
    """Outer review budget: the configured budget plus settle slack."""
    chosen = config.get("timeout_seconds", 295)
    if isinstance(chosen, bool) or not isinstance(chosen, (int, float)) or not math.isfinite(chosen) or not 0 < chosen <= 600:
        raise ValueError("review timeout_seconds must be finite and in (0, 600]")
    return float(chosen) + WORKER_SETTLE_SECONDS


def base_snapshot(root, base, name):
    """Committed bytes of ``name`` at ``base``: the "before" of a patch whose
    working tree already equals HEAD. Without this, before == after and the
    review diff is empty for a committed change."""
    target(root, name)
    return committed_snapshot(root, base, name)


def prepare(home, identity, paths, base=None):
    with lock(home):
        path = location(home, identity)
        if path.exists():
            raise ValueError("patch already prepared")
        runtime = Path(settings(home)["runtime"]).resolve()
        if runtime == home.parent.resolve():
            raise ValueError("runtime snapshot must be separate from shared checkout")
        files = {name: {"before": base_snapshot(home.parent, base, name) if base else snapshot(target(home.parent, name)),
                        "runtime_before": snapshot(target(runtime, name))} for name in paths}
        if not files:
            raise ValueError("empty patch")
        record = {"phase": "prepared", "runtime": str(runtime), "files": files,
                  "prepared_at": datetime.now(timezone.utc).isoformat()}
        if base:
            record["base"] = base
        _save(path, record)


def review(home, record):
    from .codex_review import validate_response
    config_path = home / "patch-review.json"
    config = json.loads((config_path if config_path.exists() else home / "publication-check.json").read_text())
    evidence = {"runtime": record["runtime"], "phase": record["phase"], "test": record["test"],
                "test_output": Path(record["test"]["log"]).read_text(), "files": {}}
    for name, values in record["files"].items():
        texts = {key: base64.b64decode(values[key]["bytes"]).decode("utf-8") if values[key] else ""
                 for key in ("before", "after", "runtime_before")}
        # Full after-text for every manifest file: an unchanged dependency has an
        # empty diff, so without this the reviewer sees only its hash.
        changes = {"after_text": texts["after"],
                   "modes": {key: values[key]["mode"] if values[key] else None for key in texts},
                   "hashes": {key: hashlib.sha256(text.encode()).hexdigest() for key, text in texts.items()},
                   "source_diff": "".join(difflib.unified_diff(texts["before"].splitlines(True), texts["after"].splitlines(True), fromfile=name+" before", tofile=name+" after"))}
        changes["runtime_diff"] = "same as source_diff" if values["runtime_before"] == values["before"] else "".join(
            difflib.unified_diff(texts["runtime_before"].splitlines(True), texts["after"].splitlines(True), fromfile=name+" running before", tofile=name+" after"))
        evidence["files"][name] = changes
    evidence["gate_source"] = Path(__file__).read_text()
    if "delivery_plan" in record:
        plan = record["delivery_plan"]
        evidence["delivery_plan"] = {key: value for key, value in plan.items() if key != "artifacts"}
        evidence["delivery_scripts"] = {name: base64.b64decode(value["bytes"]).decode("utf-8")
                                        for name, value in plan["artifacts"].items()}
    # Bacchelli §VI.B: rationale ("what changed and why") determines reviewer
    # understanding; include it when present so the understanding gate can
    # distinguish low-context from well-described patches.
    for field in ("description", "rationale"):
        value = record.get(field)
        if isinstance(value, str) and value and not value.isspace():
            evidence["rationale"] = value
            break
    body = json.dumps(evidence, sort_keys=True)
    reference = "exact scoped patch bytes and test result"
    request = {"version": 1, "input_hash": hashlib.sha256(body.encode()).hexdigest(), "body": "Review the complete scoped patch and test evidence supplied in context.",
        "questions": [{"id": "PATCH", "question": "This is a pre-activation review of code, deterministic tests and the executable delivery/recovery plan. Live activation and recovery results are not yet expected: apply runs only after this review passes, and verify independently exercises revert/reapply before delivery_verified can become true. Assess whether the supplied plan can safely produce that evidence; do not treat this review as verified delivery. Do the supplied patch bytes and test result support this bounded change under its stated cooperating-writer scope? Check code correctness, visible failures and scoped runtime recovery. Activation commands are required to restart consumers without editing source or performing irreversible external actions; arbitrary shell side effects and hostile concurrent writers are outside this scope. Do not require task/receipt/branch machinery or whole-system transactions. Suspicious for a concrete code defect under these preconditions; unknown for missing essential evidence."}],
        "context": {"context_complete": True, "evidence_references": [reference], "patch": evidence}}
    # The outer budget must exceed the reviewer's own model-call budget, or the
    # gate kills a reviewer that would have finished and reports a false refusal.
    outer = _worker_timeout(config)
    result = json.loads(_worker(config["command"], json.dumps(request).encode(), outer))
    validate_response(request, result)
    return {"clear": result["results"][0].get("verdict") == "clear", "request_hash": request["input_hash"], "response": result}


def check(home, identity, command, *, activate=None, observe=None, revert_observe=None, revert=None):
    from .delivery_requests import check_admission
    with lock(home):
        check_admission(home)
        record = status(home, identity)
        # A check produces no delivery verdict of its own: leave the field unset
        # so an aborted attempt is not read as a genuine non-delivery. _apply and
        # _revert write False only once bytes actually move.
        record.pop("delivery_verified", None)
        record.pop("verification", None)
        # A fresh check supersedes an earlier failure; otherwise a review- or
        # test-unavailable attempt leaves `failure` set and a later successful
        # check renders a false failure for a healthy patch.
        record.pop("failure", None)
        if activate is not None or observe is not None or revert_observe is not None:
            if not activate or not observe or not revert_observe:
                raise ValueError("check requires activate, observe and revert-observe together")
            record["delivery_plan"] = delivery_plan(home, activate, observe, revert_observe, revert=revert)
            # An unresolvable command is refused before tests or review are spent on it.
            preflight(home, record["delivery_plan"])
        else:
            record.pop("delivery_plan", None)
        for name, values in record["files"].items():
            values["after"] = snapshot(target(home.parent, name))
        try:
            record["test"] = run(home, identity, "test", command)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            # A failed test must not keep a verdict from an earlier review of the
            # same record, or the phase falls while the dashboard still reads it.
            record.update(phase="test-unavailable", failure=str(exc))
            record.pop("review", None)
            _save(location(home, identity), record)
            raise
        if record["test"]["code"]:
            # A failing deterministic check invalidates any earlier verdict on this
            # record; leaving it renders a healthy review under a failed phase.
            record.update(phase="test-failed")
            record.pop("review", None)
            _save(location(home, identity), record)
            raise ValueError("deterministic patch check failed")
        if any(snapshot(target(home.parent, name)) != values["after"] for name, values in record["files"].items()):
            # Bytes changed under the check; any earlier verdict was on those bytes.
            record.update(phase="test-mutated-source")
            record.pop("review", None)
            _save(location(home, identity), record)
            raise ValueError("source changed during deterministic check; recheck exact bytes")
        try:
            record["review"] = review(home, record)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            # A failed attempt must not leave a stale verdict behind: a superseding
            # flake overwrites the phase while keeping an older passing result, and
            # the dashboard prints only this failure string, hiding a real delivery
            # failure (wall_view.py).
            record.update(phase="review-unavailable", failure=str(exc))
            record.pop("review", None)
            _save(location(home, identity), record)
            raise
        record["phase"] = "reviewed" if record["review"]["clear"] else "review-refused"
        _save(location(home, identity), record)
        if not record["review"]["clear"]:
            raise ValueError("patch review refused; see saved response")


def _revert(home, identity, record, activate):
    runtime = Path(record["runtime"])
    record["delivery_verified"] = False
    try:
        for name, values in record["files"].items():
            current = snapshot(target(runtime, name))
            if current not in (values["runtime_before"], values.get("after")):
                raise ValueError(f"runtime changed outside this patch: {name}; reconcile before revert")
        for name, values in record["files"].items():
            path = target(runtime, name)
            restore(path, values["runtime_before"], expected=snapshot(path))
        # The plan's revert argv restores the old pin; re-running the new
        # activation would re-pin the new code and call that a revert.
        revert_argv = (record.get("delivery_plan") or {}).get("revert") or activate
        record["revert_activation"] = run(home, identity, "revert", revert_argv)
        record["phase"] = "reverted" if record["revert_activation"]["code"] == 0 else "reverted-activation-failed"
        if record["revert_activation"]["code"]:
            raise ValueError("revert activation failed; scoped bytes restored but consumer state UNKNOWN")
        # A later successful revert supersedes an earlier failed one; leaving the
        # stale marker makes an applied, verified record (e.g. a final verify
        # revert) carry a failure that no longer applies.
        record.pop("rollback_failure", None)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        record.update(phase="revert-failed", rollback_failure=str(exc))
        raise
    finally:
        _save(location(home, identity), record)


def revert(home, identity, activate):
    from .delivery_requests import check_admission
    refuse_inside_own_wake(home)
    with lock(home):
        check_admission(home)
        record = status(home, identity)
        _revert(home, identity, record, activate)


def apply(home, identity, activate, observe):
    from .delivery_requests import check_admission
    refuse_inside_own_wake(home)
    check_admission(home)
    with lock(home):
        check_admission(home)
        record = status(home, identity)
        _apply(home, identity, record, activate, observe)


def _apply(home, identity, record, activate, observe):
    if record["phase"] != "reviewed":
        raise ValueError("patch needs a passing review")
    runtime = Path(record["runtime"])
    for name, values in record["files"].items():
        if snapshot(target(home.parent, name)) != values["after"]:
            raise ValueError(f"patch changed since review: {name}")
        if snapshot(target(runtime, name)) != values["runtime_before"]:
            raise ValueError(f"runtime changed since prepare: {name}")
    # Historical callers supplied commands at apply time. Independently review
    # that plan before any runtime writes; it cannot certify exercised recovery.
    if "delivery_plan" not in record:
        record["delivery_plan"] = delivery_plan(home, activate, observe)
        try:
            record["review"] = review(home, record)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            # Same hazard as check(): a flake here must not leave the verdict of an
            # earlier review, which certified different evidence, under a failed phase.
            record.update(phase="review-unavailable", failure=str(exc))
            record.pop("review", None)
            _save(location(home, identity), record)
            raise
        if not record["review"]["clear"]:
            record["phase"] = "review-refused"
            _save(location(home, identity), record)
            raise ValueError("delivery plan review refused; see saved response")
    validate_plan(home, record, activate, observe)
    record.update(phase="applying", activate=activate, observe=observe, delivery_verified=False)
    _save(location(home, identity), record)
    try:
        for name, values in record["files"].items():
            restore(target(runtime, name), values["after"], expected=values["runtime_before"])
        record["activation"] = run(home, identity, "activate", activate)
        record["observation"] = run(home, identity, "observe", observe)
        if record["activation"]["code"] or record["observation"]["code"]:
            raise ValueError("live patch activation/observation failed")
        for name, values in record["files"].items():
            if snapshot(target(runtime, name)) != values["after"]:
                raise ValueError(f"runtime changed during live observation: {name}")
        validate_plan(home, record, activate, observe)
        record.update(phase="applied", applied_at=datetime.now(timezone.utc).isoformat())
        _save(location(home, identity), record)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        record["failure"] = record["apply_failure"] = str(exc)
        _revert(home, identity, record, activate)
        raise


def verify(home, identity):
    """Exercise revert, observe the old consumer, reapply, and observe the new one."""
    from .delivery_requests import check_admission
    # Before any runtime write: refusing after _revert would leave the old bytes in place.
    refuse_inside_own_wake(home)
    check_admission(home)
    with lock(home):
        check_admission(home)
        record = status(home, identity)
        plan = record.get("delivery_plan", {})
        if record["phase"] != "applied" or not plan.get("revert_observe"):
            raise ValueError("verification needs applied bytes and a reviewed revert-observe plan")
        validate_plan(home, record, plan["activate"], plan["observe"])
        for name, values in record["files"].items():
            if snapshot(target(Path(record["runtime"]), name)) != values["after"]:
                raise ValueError(f"runtime changed before verification: {name}")
        _revert(home, identity, record, plan["activate"])
        restored_consumer_observed = False
        try:
            record["revert_observation"] = run(home, identity, "revert-observe", plan["revert_observe"])
            if record["revert_observation"]["code"]:
                raise ValueError("revert observation failed; delivery remains incomplete")
            restored_consumer_observed = True
            record["phase"] = "reviewed"
            _save(location(home, identity), record)
            _apply(home, identity, record, plan["activate"], plan["observe"])
            # A successful exercise supersedes an earlier failed apply: leaving
            # its failure strings would render a verified patch as still broken
            # (wall_view prints record["failure"] for any record that has it).
            record.pop("apply_failure", None)
            record.pop("failure", None)
            record["verification"] = {"at": datetime.now(timezone.utc).isoformat(),
                                      "patch_hash": patch_hash(record),
                                      "plan_hash": hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()}
            record["delivery_verified"] = True
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            if record["phase"] == "reverted" and not restored_consumer_observed:
                record["phase"] = "revert-observation-failed"
            record["verification_failure"] = str(exc)
            raise
        finally:
            _save(location(home, identity), record)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--id", required=True)
    parser.add_argument("action", choices=["prepare", "check", "apply", "revert", "verify", "status"])
    parser.add_argument("--files", nargs="+")
    parser.add_argument("--base", help="git ref whose committed bytes are the patch's before (for a change already committed)")
    parser.add_argument("--command", help="JSON argument list for deterministic check")
    parser.add_argument("--activate", help="JSON argument list restarting affected consumers")
    parser.add_argument("--observe", help="JSON argument list checking real live consumers")
    parser.add_argument("--revert-observe", help="JSON argument list checking the restored live consumer")
    parser.add_argument("--revert", help="JSON argument list restoring the old consumer pin")
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.home, args.id, args.files or [], base=args.base)
    elif args.action == "check":
        check(args.home, args.id, decode_argv(args.command),
              activate=decode_argv(args.activate),
              observe=decode_argv(args.observe),
              revert_observe=decode_argv(args.revert_observe),
              revert=decode_argv(args.revert))
    elif args.action == "apply":
        apply(args.home, args.id, decode_argv(args.activate), decode_argv(args.observe))
    elif args.action == "revert":
        revert(args.home, args.id, decode_argv(args.activate))
    elif args.action == "verify":
        verify(args.home, args.id)
    print(json.dumps(status(args.home, args.id), indent=2))


if __name__ == "__main__":
    main()
