"""Read the current GitHub Actions result into the local observation tape."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .feed import Feed, utc_now


def _command(*argv: str, cwd: Path | None = None) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=25, cwd=cwd)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"{' '.join(argv)} failed")
    return result.stdout.strip()


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
