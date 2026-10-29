"""Read the current GitHub Actions result into the local observation tape.

SLSA vocabulary (SLSA Specification v1.0, 2023-04 — https://slsa.dev/spec/v1.0/):
Build track L0–L3 (levels p.1–2, Source removed in v1.0), Build Platform
Provenance generation Exists/Authentic/Unforgeable with
Completeness/Authenticity/Accuracy and Hosted/Isolated isolation strength
(requirements: Provenance generation, Isolation strength — L2 Hosted
``shared/dedicated not workstation e.g. GitHub Actions/Cloud Build/Travis CI``,
L3 Isolated ``MUST NOT cross-run influence even within project`` and
``secret material MUST NOT be accessible to user steps``), in-toto Statement
predicateType ``https://slsa.dev/provenance/v1`` with
buildDefinition {buildType, externalParameters, internalParameters,
resolvedDependencies[]} + runDetails {builder {id, version},
metadata {invocationId, startedOn, finishedOn}} + subject digest
(provenance; verifying-artifacts), Producer MUSTs (choose capable builder,
consistent build, distribute provenance). L1 ``trivial to bypass or forge``
caveat (levels p.2) kept. Pure vocabulary beside read/tick/latest — no
signature fetch, I/O, feed or wall mutation.
"""

from typing import Final

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .feed import Feed, utc_now


# --- SLSA vocabulary (SLSA v1.0 Build track) — levels p.1–2; requirements: Provenance generation / Isolation strength; provenance predicate ---
SLSA_BUILD_LEVELS: Final[tuple[str, ...]] = ("L0", "L1", "L2", "L3")
"""Build Levels L0–L3 — SLSA v1.0 levels p.1–2 (Source removed), requirements Levels table: L0 none, L1 Provenance Exists, L2 Authentic+Hosted+signed, L3 Unforgeable+Isolated. L1 trivial-to-bypass caveat."""

SLSA_PROVENANCE_STATES: Final[tuple[str, ...]] = ("none", "exists", "authentic", "unforgeable")
"""Provenance generation states — requirements Build Platform > Provenance generation: Exists (L1 MUST generate digest-identifying provenance, Completeness best-effort) → Authentic (L2 signature) → Unforgeable (L3 Accuracy: signing material build-service-only)."""

SLSA_ISOLATION_LEVELS: Final[tuple[str, ...]] = ("none", "hosted", "isolated")
"""Isolation strength — requirements Isolation strength: none, Hosted (L2: shared/dedicated not workstation) vs Isolated (L3: no cross-run influence, dedicated). Examples GitHub Actions, Cloud Build, Travis CI (requirements)."""

SLSA_PREDICATE_TYPE: Final[str] = "https://slsa.dev/provenance/v1"
"""SLSA Provenance predicateType — in-toto Statement predicateType for SLSA Provenance v1 (provenance p.1)."""

SLSA_BUILDER_ID_NOTE: Final[str] = "transitive closure of trusted build platform"
"""Builder.id definition — provenance builder.id is transitive closure of trusted build platform (SHOULD define security model)."""


def _is_hex(value: object, length: int) -> bool:
    if not isinstance(value, str) or len(value) != length:
        return False
    for ch in value:
        if ch not in "0123456789abcdefABCDEF":
            return False
    return True


def slsa_build_level_is_valid(level: object) -> bool:
    """Whether level is an SLSA_BUILD_LEVELS member — levels p.1–2."""
    return isinstance(level, str) and level in SLSA_BUILD_LEVELS


def slsa_provenance_state_is_valid(state: object) -> bool:
    """Whether state is an SLSA_PROVENANCE_STATES member — Provenance generation ladder."""
    return isinstance(state, str) and state in SLSA_PROVENANCE_STATES


def slsa_isolation_level_is_valid(level: object) -> bool:
    """Whether level is an SLSA_ISOLATION_LEVELS member — Isolation strength."""
    return isinstance(level, str) and level in SLSA_ISOLATION_LEVELS


def slsa_builder_id_is_valid(builder_id: object) -> bool:
    """Whether builder_id is non-empty non-whitespace string — builder.id syntax check (transitive closure identifier, no fetch)."""
    if not isinstance(builder_id, str) or len(builder_id) == 0:
        return False
    for ch in builder_id:
        if ch not in " \t\n\r\x0c\x0b":
            return True
    return False


def slsa_subject_is_valid(subject: object) -> bool:
    """Whether subject has digest sha256 hex64 or gitCommit hex40 and uri/name — provenance subject."""
    if not isinstance(subject, dict):
        return False
    digest = subject.get("digest")
    if not isinstance(digest, dict):
        return False
    sha256 = digest.get("sha256")
    sha512 = digest.get("sha512")
    git_commit = digest.get("gitCommit")
    has_digest = False
    if isinstance(sha256, str) and _is_hex(sha256, 64):
        has_digest = True
    if isinstance(sha512, str) and _is_hex(sha512, 128):
        has_digest = True
    if isinstance(git_commit, str) and _is_hex(git_commit, 40):
        has_digest = True
    if not has_digest:
        return False
    has_name = isinstance(subject.get("name"), str) and len(subject.get("name")) > 0  # type: ignore[arg-type]
    has_uri = isinstance(subject.get("uri"), str) and len(subject.get("uri")) > 0  # type: ignore[arg-type]
    return bool(has_name or has_uri)


def slsa_provenance_is_valid(provenance: object) -> bool:
    """Whether provenance has predicateType, subject digest, buildType and builder.id — pure shape (no crypto)."""
    if not isinstance(provenance, dict):
        return False
    if provenance.get("predicateType") != SLSA_PREDICATE_TYPE:
        return False
    subject = provenance.get("subject")
    if not isinstance(subject, list) or len(subject) == 0:
        return False
    has_valid_subject = False
    for item in subject:
        if slsa_subject_is_valid(item):
            has_valid_subject = True
            break
    if not has_valid_subject:
        return False
    predicate = provenance.get("predicate")
    if not isinstance(predicate, dict):
        return False
    build_def = predicate.get("buildDefinition")
    if not isinstance(build_def, dict):
        return False
    build_type = build_def.get("buildType")
    if not isinstance(build_type, str) or len(build_type) == 0:
        return False
    run_details = predicate.get("runDetails")
    if not isinstance(run_details, dict):
        return False
    builder = run_details.get("builder")
    if not isinstance(builder, dict):
        return False
    builder_id = builder.get("id")
    if not slsa_builder_id_is_valid(builder_id):
        return False
    return True


def slsa_level_for(provenance_state: str, *, hosted: bool, isolated: bool, builder_id: str | None) -> str:
    """Deterministic L0–L3 mapping from generation+isolation+builder — levels p.2.

    L0 no provenance, L1 exists (trivial-to-bypass), L2 authentic+hosted+builder_id,
    L3 unforgeable+hosted+isolated+builder_id, else nearest lower.
    """
    if not slsa_provenance_state_is_valid(provenance_state) or provenance_state == "none":
        return "L0"
    if provenance_state == "exists":
        return "L1"
    has_builder = slsa_builder_id_is_valid(builder_id) if builder_id is not None else False
    if provenance_state == "authentic":
        if hosted and has_builder:
            return "L2"
        return "L1"
    if provenance_state == "unforgeable":
        if hosted and isolated and has_builder:
            return "L3"
        if hosted and has_builder:
            return "L2"
        return "L1"
    return "L0"


def slsa_provenance_summary(provenance: dict, *, level: str = "L1") -> dict:
    """Typed predicate skeleton from provenance — pure dict construction (future distribution shape before signing)."""
    base = provenance if isinstance(provenance, dict) else {}
    predicate = base.get("predicate") if isinstance(base.get("predicate"), dict) else {}
    build_def = predicate.get("buildDefinition") if isinstance(predicate.get("buildDefinition"), dict) else {}  # type: ignore[union-attr]
    run_details = predicate.get("runDetails") if isinstance(predicate.get("runDetails"), dict) else {}  # type: ignore[union-attr]
    builder = run_details.get("builder") if isinstance(run_details.get("builder"), dict) else {}  # type: ignore[union-attr]
    subject = base.get("subject") if isinstance(base.get("subject"), list) else []
    chosen_level = level if slsa_build_level_is_valid(level) else "L1"
    return {
        "predicateType": base.get("predicateType", SLSA_PREDICATE_TYPE),
        "subject": subject,
        "predicate": {
            "buildDefinition": {
                "buildType": build_def.get("buildType", ""),  # type: ignore[union-attr]
                "externalParameters": build_def.get("externalParameters", {}),  # type: ignore[union-attr]
                "resolvedDependencies": build_def.get("resolvedDependencies", []),  # type: ignore[union-attr]
            },
            "runDetails": {
                "builder": {"id": builder.get("id", "")},  # type: ignore[union-attr]
                "metadata": run_details.get("metadata", {}),  # type: ignore[union-attr]
            },
        },
        "level": chosen_level,
    }


_COMMAND_ATTEMPTS = 5
# A transient GitHub transport error (connection reset, TLS, timeout) is not a CI
# state; retry it with bounded backoff inside the same tick instead of publishing
# UNKNOWN. Five attempts with 2 s between them tolerate multi-second API
# flakiness (observed: intermittent "Post .../graphql: EOF" from gh).

_COMMAND_RETRY_SECONDS = 2.0


def _command(*argv: str, cwd: Path | None = None) -> str:
    # A nonzero exit is how ``gh`` reports a transport failure (the EOF/TLS text
    # lands in stderr), so both it and an OS-level spawn/timeout error are
    # retried. A persistent failure still raises after the bounded attempts and
    # read() keeps returning UNKNOWN, as does a legitimate empty run list.
    for attempt in range(_COMMAND_ATTEMPTS):
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=25, cwd=cwd)
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or f"{' '.join(argv)} failed")
            return result.stdout.strip()
        except (OSError, subprocess.TimeoutExpired, RuntimeError):
            if attempt + 1 >= _COMMAND_ATTEMPTS:
                raise
            time.sleep(_COMMAND_RETRY_SECONDS)


def read(home: Path, *, workspace: Path | None = None, sha: str | None = None,
         branch: str | None = None, repository: str | None = None,
         required_workflows: tuple[str, ...] = ()) -> dict[str, str]:
    workspace = workspace or home.parent
    try:
        repository = json.loads(_command("gh", "repo", "view", *([repository] if repository else []),
                                         "--json", "nameWithOwner,defaultBranchRef",
                                         cwd=workspace))
        default_branch = repository["defaultBranchRef"]["name"]
        sha = sha or _command("git", "-C", str(workspace), "rev-parse", f"origin/{default_branch}")
        rows = json.loads(_command("gh", "run", "list", "--repo", repository["nameWithOwner"],
            "--commit", sha, "--limit", "50", "--json",
            "databaseId,headSha,status,conclusion,url,workflowName,event,headBranch",
            *(["--branch", branch, "--event", "push"] if branch else []), cwd=workspace))
        matches = [row for row in rows if row.get("headSha") == sha and
                   (not branch or (row.get("event") == "push" and row.get("headBranch") == branch))]
        if not matches:
            return {"state": "unknown", "sha": sha, "run": "none", "url": "none",
                    "detail": f"no Actions run for exact commit {sha}"}
        current: dict[str, dict] = {}
        for row in matches:
            workflow = str(row.get("workflowName") or row["databaseId"])
            old = current.get(workflow)
            if old is None or int(row["databaseId"]) > int(old["databaseId"]):
                current[workflow] = row
        missing = set(required_workflows) - current.keys()
        if missing:
            return {"state": "unknown", "sha": sha, "run": "none", "url": "none",
                    "detail": f"required push workflows missing: {','.join(sorted(missing))}"}
        matches = list(current.values())
        failed = [row for row in matches if row.get("status") == "completed" and
                  row.get("conclusion") in {"failure", "timed_out", "cancelled", "action_required"}]
        pending = [row for row in matches if row.get("status") != "completed"]
        row = max(failed or pending or matches, key=lambda item: int(item["databaseId"]))
        conclusion = str(row.get("conclusion") or "")
        status = str(row.get("status") or "")
        state = "fail" if failed else "pending" if pending else (
            "pass" if all(item.get("conclusion") == "success" for item in matches) else "unknown")
        return {"state": state, "sha": sha, "run": str(row["databaseId"]),
                "url": str(row["url"]), "detail": f"{status}/{conclusion or 'none'}"}
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        return {"state": "unknown", "sha": "unknown", "run": "none", "url": "none", "detail": str(exc)[:200]}


def tick(home: Path, *, check_deliveries: bool = True) -> dict[str, str]:
    from . import wall
    wall.settings(home)
    path = home / "ci" / "latest.json"
    previous = latest(home)
    result = read(home)
    result["checked"] = utc_now()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
    identity = ("state", "sha", "run", "url", "detail")
    if previous is None or any(previous.get(key) != result[key] for key in identity):
        meaning = {"pass": "All observed Actions runs for this remote commit succeeded.",
                   "fail": "At least one Actions run failed; the source author owns repair, with genome as the unassigned-incident fallback.",
                   "pending": "An Actions run is still in progress; its result is not known yet.",
                   "unknown": "The watcher cannot establish a result for the current remote commit."}[result["state"]]
        Feed(home).append("ci", "[ci] state={state} sha={sha} run={run} url={url} detail={detail}\n"
                          "{meaning}".format(**result, meaning=meaning))
        if result["state"] == "fail":
            wall.message(home, "ci", "genome", f"Observed CI failure for {result['sha']}: {result['url']}. Read the current CI sensor and decide the repair.")
    return result


def latest(home: Path) -> dict[str, str] | None:
    try:
        return json.loads((home / "ci" / "latest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def line(home: Path) -> str:
    sample = latest(home)
    if sample is None:
        return "CI: UNKNOWN no reading"
    try:
        checked = datetime.fromisoformat(sample["checked"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - checked).total_seconds()
    except (KeyError, ValueError):
        age = 999999
    if age > 300:
        return "CI: UNKNOWN stale reading"
    return f"CI: {sample['state'].upper()} sha={sample['sha'][:12]} run={sample['run']} {sample['url']} {sample['detail']}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--follow", action="store_true")
    args = parser.parse_args()
    while True:
        print(tick(args.home), flush=True)
        if not args.follow:
            return
        time.sleep(60)


if __name__ == "__main__":
    main()
