"""Author-owned committed candidates; genome performs only ready integration."""
from __future__ import annotations

import fcntl
import hashlib
import json
import re
import subprocess
from contextlib import contextmanager, ExitStack
from dataclasses import replace
from pathlib import Path

from . import task_state
from .records import payload as record_payload
from .feed import Feed
from .observations import validate_slug


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                            text=True, timeout=30)
    if result.returncode:
        raise ValueError(result.stderr.strip() or "Git command failed")
    return result.stdout.strip()


def _path(home: Path, identity: str) -> Path:
    task_state._event(identity)
    return home / "deliveries" / f"{identity}.json"


@contextmanager
def _lock(home: Path):
    directory = home / "deliveries"
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def load(home: Path, identity: str) -> dict:
    data = json.loads(_path(home, identity).read_text())
    strings = ("owner", "repo", "workspace", "origin", "branch", "base", "head", "review", "review_sha256", "phase", "reason")
    if (not isinstance(data, dict) or data.get("version") != 1 or data.get("identity") != identity or
            any(not isinstance(data.get(key), str) or not data[key] for key in strings) or
            data.get("phase") not in {"branch-ci", "ready", "blocked", "integrated", "done"} or
            not isinstance(data.get("transition"), int) or data["transition"] < 1 or
            not isinstance(data.get("ci"), dict)):
        raise ValueError("invalid delivery record")
    retry_wait = data.get("author_retry_wait_sequence")
    if retry_wait is not None and (type(retry_wait) is not int or retry_wait < 1):
        raise ValueError("invalid delivery author retry binding")
    validate_slug(data["owner"])
    if any(not re.fullmatch(r"[0-9a-f]{40}", data[key]) for key in ("base", "head")):
        raise ValueError("invalid delivery revision")
    if data["phase"] == "done":
        _, digest = task_state._evidence(Path(data.get("rollout", "")))
        if digest != data.get("rollout_sha256"):
            raise ValueError("completed rollout evidence changed")
    return data


def _save(home: Path, record: dict, *, recovered_wait: int | None = None) -> Path:
    path = _path(home, record["identity"])
    old = load(home, record["identity"]) if path.exists() else None
    changed = old is None or any(old.get(key) != record.get(key) for key in ("head", "phase", "reason"))
    record["transition"] = (old.get("transition", 0) if old else 0) + int(changed)
    if changed:
        entries = Feed(home).entries()
        author = task_state.registry(entries).get(record["identity"])
        event = f"delivery-{record['identity']}-updated"
        record["author_retry_wait_sequence"] = (
            author.sequence if record["phase"] in {"blocked", "integrated"} and author
            and author.owner == record["owner"] and author.status == "waiting"
            and author.retry_event == event and not task_state.eligible(author, entries) else None)
    else:
        record["author_retry_wait_sequence"] = old.get("author_retry_wait_sequence")
    if recovered_wait is not None and record["phase"] in {"blocked", "integrated"}:
        entries = Feed(home).entries()
        author = task_state.registry(entries).get(record["identity"])
        if (not author or author.sequence != recovered_wait or author.owner != record["owner"]
                or author.status != "waiting" or author.retry_event != f"delivery-{record['identity']}-updated"
                or task_state.eligible(author, entries)):
            raise ValueError("recovery-created author wait changed; preserve the newer registration")
        record["author_retry_wait_sequence"] = recovered_wait
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n")
    temporary.replace(path)
    return path


def _remote(repo: Path, branch: str) -> str:
    rows = _git(repo, "ls-remote", "--refs", "origin", f"refs/heads/{branch}").splitlines()
    return rows[0].split()[0] if rows else ""


def _repository(record: dict) -> Path:
    repo = Path(record["repo"])
    workspace = Path(record["workspace"])
    if (_git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir") !=
            _git(workspace, "rev-parse", "--path-format=absolute", "--git-common-dir") or
            _git(workspace, "remote", "get-url", "origin") != record["origin"]):
        raise ValueError("candidate must belong to canonical repository and origin")
    if _git(repo, "remote", "get-url", "origin") != record["origin"]:
        raise ValueError("candidate origin changed")
    return repo


def _candidate(record: dict) -> str:
    repo = _repository(record)
    if _git(repo, "rev-parse", "--show-toplevel") != str(repo) or _git(repo, "status", "--porcelain"):
        raise ValueError("candidate must be a clean isolated worktree")
    if _git(repo, "rev-parse", "HEAD") != record["head"]:
        raise ValueError("candidate head changed; author must submit newly reviewed exact bytes")
    _git(repo, "merge-base", "--is-ancestor", record["base"], record["head"])
    if record["base"] == record["head"] or _git(repo, "ls-files", ".mishe-tauftauf"):
        raise ValueError("candidate must change source and exclude local plant state")
    if record.get("main_only"):
        # A main-only repository forbids publication branches. The exact local
        # candidate is the reviewed artifact; exact main CI is verified after
        # integration by `finish`. The candidate must still be an isolated
        # worktree, so it must not simply be sitting on main.
        if _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main":
            raise ValueError("main-only candidate must be an isolated worktree, not main")
    elif _remote(repo, record["branch"]) != record["head"]:
        raise ValueError("candidate branch must be published at the exact head")
    path, digest = task_state._evidence(Path(record["review"]))
    review = json.loads(Path(path).read_text())
    if digest != record["review_sha256"]:
        raise ValueError("review evidence changed; submit a new review")
    if review.get("base") != record["base"] or review.get("head") != record["head"] or review.get("verdict") != "pass":
        raise ValueError("review must pass for the exact candidate base and head")
    reviewer = validate_slug(review.get("reviewer", ""))
    if reviewer == record["owner"]:
        raise ValueError("review must be independent of the delivery owner")
    return _remote(repo, "main")


def read_ci(repo: Path, sha: str, branch: str) -> dict:
    from .ci_watch import read
    return read(repo / ".unused", workspace=repo, sha=sha, branch=branch,
                repository=_git(repo, "remote", "get-url", "origin"), required_workflows=("CI",))


def _integration_id(record: dict) -> str:
    return "integrate-" + hashlib.sha256((record["identity"] + record["head"]).encode()).hexdigest()[:24]


def _push(record: dict) -> None:
    _git(Path(record["repo"]), "push", "origin", f"{record['head']}:refs/heads/main",
         f"--force-with-lease=refs/heads/main:{record['base']}")


def _snapshot(home: Path, record: dict) -> Path:
    text = json.dumps(record, sort_keys=True, indent=2) + "\n"
    digest = hashlib.sha256(text.encode()).hexdigest()
    path = home / "artifacts" / f"delivery-{record['identity']}-{digest}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(text)
    return path


def _publish(home: Path, record: dict, writer, *, tasks=()):
    """Admit outside the fact lock, then hold it only through the guarded commit."""
    before = task_state.registry(Feed(home).entries())
    expected_tasks = {identity: before.get(identity) for identity in tasks}
    with ExitStack() as locks:
        def guard():
            locks.enter_context(_lock(home))
            current = load(home, record["identity"])
            if record.get("superseded"):
                valid = current["head"] != record["head"] and current["owner"] == record["owner"]
            else:
                valid = current == record
            if not valid:
                raise ValueError("delivery changed during publication admission; reconcile current facts")
            latest = task_state.registry(Feed(home).entries())
            if any(latest.get(identity) != old for identity, old in expected_tasks.items()):
                raise ValueError("task registration changed during delivery publication admission")
        return writer(guard)


def _wait_owner(home: Path, record: dict):
    identity, owner = record["identity"], record["owner"]
    evidence = _snapshot(home, record)
    current = task_state.registry(Feed(home).entries()).get(identity)
    if current is None:
        _publish(home, record, lambda guard: task_state.add_task(home, identity, owner,
            "Complete candidate delivery through deployed consumers", "Author retains delivery ownership",
            evidence, commit_guard=guard), tasks=(identity,))
    elif current.owner != owner or current.status in {"done", "dropped"}:
        raise ValueError("delivery task must be open and belong to its author")
    next_step = "Inspect delivery show after a real transition; repair candidate or verify final main CI and deploy consumers"
    reason = "Published candidate awaits CI or main integration; do not poll from model turns"
    def wait_commit(guard):
        guard()
        # Publication admission serializes feed writers, so the next sequence is
        # reserved through this commit. Preserve intent before the feed flush;
        # recovery can recognize only the exact successfully written wait.
        record["author_wait_pending"] = True
        record["author_wait_projection"] = dict(sequence=Feed(home).tail_sequence() + 1,
            evidence=str(evidence), evidence_sha256=task_state._evidence(evidence)[1],
            next_step=next_step, reason=reason, producer="delivery",
            retry_event=f"delivery-{identity}-updated")
        _save(home, record)
    return _publish(home, record, lambda guard: task_state.wait_for(home, identity, owner,
        next_step, reason, evidence, retry_event=f"delivery-{identity}-updated",
        producer="delivery", commit_guard=lambda: wait_commit(guard)), tasks=(identity,))


def _complete_owner_wait(home: Path, record: dict, sequence: int) -> dict:
    with _lock(home):
        if load(home, record["identity"]) != record:
            raise ValueError("delivery changed during author wait recovery")
        current = task_state.registry(Feed(home).entries()).get(record["identity"])
        if not current or current.sequence != sequence:
            # A deliberate newer wait supersedes this projection. A subsequent
            # unchanged check must not recreate/release it as crash recovery.
            record = {**record, "author_wait_pending": False}
            _save(home, record)
            raise ValueError("recovery-created author wait changed; preserve the newer registration")
        record = {**record, "author_wait_pending": False}
        _save(home, record, recovered_wait=sequence)
    return record


def _register_owner_wait(home: Path, record: dict) -> dict:
    wait = _wait_owner(home, record)
    return _complete_owner_wait(home, record, wait.sequence)


def _recover_owner_wait(home: Path, record: dict) -> dict:
    current = task_state.registry(Feed(home).entries()).get(record["identity"])
    planned = record.get("author_wait_projection")
    if current and current.status == "waiting":
        exact = (planned and current.owner == record["owner"]
                 and all(getattr(current, key) == value for key, value in planned.items()))
        if exact:
            return _complete_owner_wait(home, record, current.sequence)
        # The committed feed does not match our checkpoint: preserve the author's
        # actual wait. Only a real later delivery transition may release it.
        with _lock(home):
            if load(home, record["identity"]) != record:
                raise ValueError("delivery changed during author wait recovery")
            record = {**record, "author_wait_pending": False}
            _save(home, record)
        return record
    return _register_owner_wait(home, record)


def _sync(home: Path, record: dict, previous: dict | None) -> None:
    """Crash-safe projection: readiness comes from facts, never progress prose."""
    from .seed import _lock as task_lock

    identity = _integration_id(record)
    author_retry_notified = False
    evidence, digest = task_state._evidence(_snapshot(home, record))
    token = f"{record['head']}:{record['transition']}"
    with task_lock(home):
        entries = Feed(home).entries()
        old = task_state.registry(entries).get(identity)
        if record["phase"] == "ready":
            if old is None:
                state = task_state.TaskState(identity, "genome", 0, managed=True,
                    delivery=record["identity"], delivery_token=token, evidence=evidence, evidence_sha256=digest,
                    next_step=f"Run delivery integrate {record['identity']} --source genome; author {record['owner']} owns rollout.",
                    progress=("Independent review passes for exact head " + record['head'] + "; main-only landing, no publication branch."
                              if record.get("main_only") else f"Independent review and branch CI pass for exact head {record['head']}."))
                _publish(home, record, lambda guard: task_state._append(home, "genome", state, "task-add", commit_guard=guard), tasks=(identity,))
            elif (old.status == "waiting" and old.delivery_token != token
                  and identity not in task_state.pending_tasks(entries).values()):
                _publish(home, record, lambda guard: task_state._append(home, "genome", replace(old, status="ready", reason="",
                    delivery_token=token, evidence=evidence, evidence_sha256=digest,
                    retry_event=None, retry_task=None, retry_at=None), commit_guard=guard), tasks=(identity,))
        elif old and old.status not in {"done", "dropped"}:
            if record["phase"] in {"integrated", "done"}:
                _publish(home, record, lambda guard: task_state._append(home, "genome", replace(old, status="done", progress=f"Integrated {record['head']}; author owns final CI and rollout.",
                    evidence=evidence, evidence_sha256=digest), "task-close", commit_guard=guard), tasks=(identity,))
            elif old.status == "ready":
                _publish(home, record, lambda guard: task_state._append(home, "genome", replace(old, status="waiting", reason=record["reason"],
                    evidence=evidence, evidence_sha256=digest, retry_event=None, retry_task=None, retry_at=None), commit_guard=guard), tasks=(identity,))
    if record["phase"] in {"integrated", "done"}:
        token = f"delivery-{record['identity']}-integrated"
        # Reconcile receipt even if a process died after saving the integration record.
        if not any(e.body.startswith(f"[task-event] {token}\n") for e in Feed(home).entries()):
            _publish(home, record, lambda guard: task_state.signal(home, token, "delivery", Path(evidence),
                f"Main contains {record['head']}; {record['owner']} owns final CI and deployment.", commit_guard=guard))
    if record["phase"] in {"blocked", "integrated"} and not record.get("superseded"):
        event = f"delivery-{record['identity']}-updated"
        entries = Feed(home).entries()
        author = task_state.registry(entries).get(record["identity"])
        if (author and author.owner == record["owner"] and author.status == "waiting"
                and author.retry_event == event and not task_state.eligible(author, entries)
                and author.sequence == record.get("author_retry_wait_sequence")):
            reason = (f"The registered waiting author task {author.identity}, owned by {author.owner}, "
                      f"requires this exact retry event. Candidate transition {record['transition']} "
                      f"to {record['phase']} ({record['reason']}) now releases one eligible attempt: {author.next_step}. "
                      "Inspect the referenced delivery and exact current CI before further effects.")
            if not any(e.body.startswith(f"[task-event] {event}\n") and
                       record_payload(e).get("reason") == reason for e in entries):
                expected = (record["head"], record["transition"], record["phase"], author.sequence)
                def author_retry_guard():
                    active = load(home, record["identity"])
                    latest_entries = Feed(home).entries()
                    latest = task_state.registry(latest_entries).get(record["identity"])
                    if (active["head"], active["transition"], active["phase"],
                            active.get("author_retry_wait_sequence")) != expected:
                        raise ValueError("delivery transition changed before author retry publication")
                    if (not latest or latest.sequence != expected[3] or latest.owner != record["owner"]
                            or latest.status != "waiting" or latest.retry_event != event
                            or task_state.eligible(latest, latest_entries)):
                        raise ValueError("bound author wait changed before retry publication; preserve the newer wait")
                _publish(home, record, lambda guard: task_state.signal(home, event, "delivery", Path(evidence), reason,
                    commit_guard=lambda: (guard(), author_retry_guard())), tasks=(record["identity"],))
                author_retry_notified = True
    if record["phase"] == "done":
        rollout, rollout_digest = task_state._evidence(Path(record["rollout"]))
        if rollout_digest != record["rollout_sha256"]:
            raise ValueError("rollout evidence changed before completion reconciliation")
        current = task_state.registry(Feed(home).entries()).get(record["identity"])
        if current and current.status not in {"done", "dropped"}:
            _publish(home, record, lambda guard: task_state.finish(home, record["identity"], record["owner"],
                "Verified exact final main CI and deployed consumers", Path(rollout), commit_guard=guard), tasks=(record["identity"],))
    # The integration notice already records this outcome; the author notice
    # separately releases its exact wait. Do not add a third unchanged sample.
    if (record["phase"] != "integrated" and not author_retry_notified and
            (previous is None or (record["phase"], record["reason"]) != (previous["phase"], previous["reason"]))):
        _publish(home, record, lambda guard: Feed(home).append_record("delivery", f"Delivery {record['identity']} author={record['owner']} head={record['head']} "
            f"phase={record['phase']}: {record['reason']}. Evidence: {evidence}. "
            "Unrelated source work remains admissible.", record, kind="delivery", commit_guard=guard))


def submit(home: Path, identity: str, owner: str, repo: Path, base: str, branch: str, review: Path,
           *, main_only: bool = False) -> dict:
    task_state._event(identity)
    validate_slug(owner)
    if len(identity) > 100:
        raise ValueError("delivery ID must fit its retry event (at most 100 characters)")
    repo = repo.resolve()
    if repo == home.parent.resolve() or not (repo / ".git").is_file():
        raise ValueError("author must use a separate linked candidate worktree")
    common = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common != _git(home.parent, "rev-parse", "--path-format=absolute", "--git-common-dir"):
        raise ValueError("candidate must belong to the canonical site's repository")
    _git(repo, "check-ref-format", "--branch", branch)
    if branch == "main":
        raise ValueError("publish an author branch before main integration")
    if not re.fullmatch(r"[0-9a-f]{40}", base):
        raise ValueError("candidate base must be a full SHA")
    path, digest = task_state._evidence(review)
    record = dict(version=1, identity=identity, owner=owner, repo=str(repo), workspace=str(home.parent.resolve()), base=base,
                  head=_git(repo, "rev-parse", "HEAD"), branch=branch, main_only=main_only,
                  origin=_git(repo, "remote", "get-url", "origin"), review=path, review_sha256=digest,
                  phase="ready" if main_only else "branch-ci",
                  reason=("Reviewed main-only candidate awaits integration; no publication branch"
                          if main_only else "Published reviewed candidate awaits exact branch CI"),
                  ci={}, author_wait_pending=True)
    main = _candidate(record)
    if main != base:
        raise ValueError("main advanced; author must rebase and obtain exact review before submitting")
    wait_owner = True
    recover_owner = False
    superseded = None
    with _lock(home):
        previous = load(home, identity) if _path(home, identity).exists() else None
        if previous and previous["owner"] != owner:
            raise ValueError("delivery owner mismatch")
        if previous and previous["phase"] in {"integrated", "done"}:
            raise ValueError("delivery already integrated; use a new identity for new work")
        if previous and _integration_id(previous) in task_state.pending_tasks(Feed(home).entries()).values():
            raise ValueError("active integration is reserved; reconcile and settle it before replacing the candidate")
        if previous and previous["head"] == record["head"] and previous["review_sha256"] == digest:
            author = task_state.registry(Feed(home).entries()).get(identity)
            wait_owner = author is None or previous.get("author_wait_pending", False) or (previous["phase"] in {"branch-ci", "ready"} and
                                           author.retry_event != f"delivery-{identity}-updated")
            record = previous
            recover_owner = True
        else:
            author = task_state.registry(Feed(home).entries()).get(identity)
            if author and (author.owner != owner or author.status in {"done", "dropped"}):
                raise ValueError("delivery task must be open and belong to its author")
            for other in (home / "deliveries").glob("*.json"):
                active = load(home, other.stem)
                parked = active["phase"] in {"done", "blocked"} or active.get("final_ci", {}).get("state") == "fail"
                if active["identity"] != identity and active["owner"] == owner and active["origin"] == record["origin"] and not parked:
                    raise ValueError("author already has an active candidate; finish or park it first")
            if previous:
                superseded = {**previous, "phase": "blocked", "reason": "Author submitted a newly reviewed revision", "superseded": True}
            _save(home, record)
    if superseded:
        _sync(home, superseded, previous)
    if wait_owner:
        record = (_recover_owner_wait if recover_owner else _register_owner_wait)(home, record)
    _sync(home, record, previous)
    return record


def check(home: Path, identity: str) -> dict:
    with _lock(home):
        previous = load(home, identity)
        record = dict(previous)
        missing_author = (task_state.registry(Feed(home).entries()).get(identity) is None
                          or record.get("author_wait_pending", False))
        if record["phase"] not in {"integrated", "done"}:
            try:
                main = _candidate(record)
                # Reconcile remote side effects before consulting asynchronous CI again.
                repo = Path(record["repo"])
                _git(repo, "fetch", "-q", "origin", "refs/heads/main")
                if _git(repo, "merge-base", record["head"], "FETCH_HEAD") == record["head"]:
                    record.update(phase="integrated", reason="Remote main contains exact candidate", integrated=record["head"])
                elif main != record["base"]:
                    record.update(phase="blocked", reason="Remote main advanced; author must rebase and renew review/CI")
                elif record.get("main_only"):
                    record.update(phase="ready", reason="Exact review passes; main-only candidate awaits integration")
                else:
                    result = read_ci(repo, record["head"], record["branch"])
                    record["ci"] = result
                    passing = result.get("state") == "pass" and result.get("sha") == record["head"]
                    phase = "ready" if passing else "blocked" if result.get("state") == "fail" else "branch-ci"
                    record.update(phase=phase, reason="Exact review and branch CI pass" if passing else
                                  f"Author awaits/repairs exact branch CI: {result.get('state')} {result.get('detail', '')}")
                    if passing and record.get("integration_started"):
                        # Resume only a genome/operator-initiated exact operation.
                        # A real leased push checks transport and server policy;
                        # a dry-run cannot prove receive-hook acceptance.
                        _push(record)
                        record.update(phase="integrated", integrated=record["head"],
                                      reason="Resumed exact integration succeeded; author owns rollout")
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                record.update(phase="blocked", reason=str(exc))
        if record["phase"] == "integrated":
            try:
                result = read_ci(_repository(record), record["head"], "main")
                record.update(final_ci=result, reason=f"Integrated; author owns rollout after exact main CI: {result.get('state')} {result.get('detail', '')}")
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                record.update(final_ci={"state": "unknown"}, reason=str(exc))
        _save(home, record)
    if missing_author:
        record = _recover_owner_wait(home, record)
    _sync(home, record, previous)
    return record


def check_all(home: Path) -> None:
    for path in sorted((home / "deliveries").glob("*.json")):
        try:
            record = check(home, path.stem)
            if record["phase"] == "done":
                from .retirement import retire
                retire(home, path.stem)
        except (OSError, ValueError, KeyError) as exc:
            # Malformed records remain visibly UNKNOWN; do not stop the main CI watcher.
            print(f"DELIVERY: UNKNOWN {path.name}: {exc}", flush=True)


def main_owner(home: Path, sha: str) -> str:
    for path in sorted((home / "deliveries").glob("*.json")):
        try:
            record = load(home, path.stem)
            if record["head"] == sha and record["phase"] in {"integrated", "done"}:
                return record["owner"]
        except (OSError, ValueError, KeyError):
            continue
    return "genome"


def integrate(home: Path, identity: str, source: str) -> dict:
    if source not in {"genome", "operator"}:
        raise ValueError("only genome or operator serializes main integration")
    failure = None
    with _lock(home):
        previous = load(home, identity)
        record = dict(previous)
        main = _candidate(record)
        repo = Path(record["repo"])
        if main != record["head"]:
            if main != record["base"]:
                raise ValueError("main advanced; author must rebase and renew review/CI")
            if not record.get("main_only"):
                result = read_ci(repo, record["head"], record["branch"])
                if result.get("state") != "pass" or result.get("sha") != record["head"]:
                    raise ValueError("exact branch CI must pass before integration")
            # The ancestry check above makes this a fast-forward. The exact lease
            # rejects a concurrently changed main, including another site writer.
            record["integration_started"] = True
            _save(home, record)
            try:
                _push(record)
                if _remote(repo, "main") != record["head"]:
                    raise ValueError("remote main changed after push; reconcile before retrying")
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                record.update(phase="blocked", reason=str(exc))
                _save(home, record)
                failure = exc
        if failure is None:
            record.update(phase="integrated", integrated=record["head"], reason="Exact candidate integrated; author owns final CI and rollout")
            _save(home, record)
    _sync(home, record, previous)
    if failure is not None:
        raise ValueError(str(failure)) from failure
    return record


def finish(home: Path, identity: str, owner: str, evidence: Path) -> dict:
    with _lock(home):
        previous = load(home, identity)
        if previous["owner"] != owner:
            raise ValueError("delivery owner mismatch")
        if previous["phase"] == "done" and previous.get("retirement", {}).get("state") == "retired":
            path, digest = task_state._evidence(evidence)
            if path != previous["rollout"] or digest != previous["rollout_sha256"]:
                raise ValueError("retired delivery requires its original verified rollout evidence")
            return previous
        if previous["phase"] not in {"integrated", "done"}:
            raise ValueError("integrate before completing rollout")
        head = previous["head"]
        result = read_ci(_repository(previous), head, "main")
        if result.get("state") != "pass" or result.get("sha") != head:
            raise ValueError("exact final main CI must pass before delivery completion")
        path, digest = task_state._evidence(evidence)
        rollout = json.loads(Path(path).read_text())
        if rollout.get("sha") != head or rollout.get("state") != "pass" or not rollout.get("consumers"):
            raise ValueError("rollout evidence must name checked consumers and exact SHA")
        record = {**previous, "phase": "done", "reason": "Author verified final CI and deployed consumers",
                  "rollout": path, "rollout_sha256": digest, "final_ci": result}
        if any(s.parent == identity for s in task_state.states(Feed(home).entries()).values()):
            raise ValueError("unfinished author child tasks prevent delivery completion")
        _save(home, record)
    _sync(home, record, previous)
    return record


def line(home: Path) -> str:
    rows = []
    for path in sorted((home / "deliveries").glob("*.json")):
        try:
            record = load(home, path.stem)
            if record["phase"] == "done" and record.get("retirement", {}).get("state") != "retired":
                rows.append(f"{record['identity']} retirement=pending: {record.get('retirement', {}).get('reason', 'watcher will verify branch and worktree retirement')}")
            if record["phase"] != "done":
                rows.append(f"{record['identity']} author={record['owner']} phase={record['phase']} head={record['head'][:12]}: {record['reason']}")
        except (OSError, ValueError, KeyError) as exc:
            rows.append(f"UNKNOWN {path.name}: {exc}")
    return "DELIVERY: author-owned; source production allowed\n" + ("\n".join(rows) or "No open committed candidates.")
