"""Per-component deploy: ``SITE/components/NAME/{releases/SHA,current}``.

Design and rules: docs/component-deploy.md. A component is a leaf executable
(first cut: a Rust crate under ``rust/NAME``). Its consumers call
``components/NAME/current/bin/NAME``; promotion builds the release from the
committed bytes of one SHA, checks it, and moves ``current`` by one atomic
``rename(2)``. Nothing global is held, so wakes keep running. Rollback re-points
``current`` at the previous release with the same rename.

Only this module writes ``current``. The detector ``verify`` reports RED when the
pointer, the release bytes or the runtime pin disagree; a missing pointer is RED.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

from . import component_text as ctext

STATES = ("GREEN", "RED")
CARGO = os.environ.get("MISHE_CARGO", str(Path.home() / ".cargo" / "bin" / "cargo"))


def _name(name: str) -> str:
    if not ctext.name_ok(name):
        raise ValueError(f"component name must match {ctext.NAME_PATTERN}: {name!r}")
    return name


def _sha(sha: str) -> str:
    if not ctext.sha_ok(sha):
        raise ValueError("component release must be a full 40-hex commit sha")
    return sha


def root(home, name: str) -> Path:
    return Path(home) / "components" / _name(name)


def current(home, name: str) -> Path:
    return root(home, name) / "current"


def release_dir(home, name: str, sha: str) -> Path:
    return root(home, name) / "releases" / _sha(sha)


def _ledger(home, name: str) -> Path:
    return root(home, name) / "promotions.jsonl"


def _record(home, name: str, event: dict) -> None:
    path = _ledger(home, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps({"ts": time.time(), **event}, sort_keys=True) + "\n")


def _ledger_events(home, name: str) -> list[dict]:
    path = _ledger(home, name)
    if not path.exists():
        return []
    return ctext.decode_ledger(path.read_text())


def pointed_sha(home, name: str) -> str | None:
    """The sha ``current`` names, or None when the pointer is missing or malformed."""
    link = current(home, name)
    if not link.is_symlink():
        return None
    target = Path(os.readlink(link))
    if not target.is_absolute():
        target = link.parent / target
    return ctext.sha_from_parts(target.parts)


def _swap(home, name: str, sha: str) -> None:
    """Re-point ``current`` at releases/SHA with one rename over the old link."""
    link = current(home, name)
    tmp = link.with_name(f"current.tmp.{os.getpid()}")
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(os.path.join("releases", sha), tmp)   # relative: survives moving the site
    os.replace(tmp, link)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(repo, *args) -> str:
    return ctext.git_line(subprocess.check_output(["git", "-C", str(repo), *args], text=True, stderr=subprocess.PIPE))


def _build(staging: Path, repo, name: str, sha: str) -> Path:
    """Export rust/NAME at SHA into staging, build it there, and return the built binary."""
    src = staging / "src"
    src.mkdir(parents=True)
    tar_path = staging / "source.tar"
    subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", "-o", str(tar_path),
                    sha, f"rust/{name}"], check=True, stderr=subprocess.PIPE)
    with tarfile.open(tar_path) as archive:
        archive.extractall(src, filter="data")
    tar_path.unlink()
    manifest = src / "rust" / name / "Cargo.toml"
    if not manifest.is_file():
        raise ValueError(f"rust/{name}/Cargo.toml is missing at {sha}")
    target = staging / "target"
    subprocess.run([CARGO, "build", "--release", "--locked", "--manifest-path", str(manifest),
                    "--target-dir", str(target)], check=True, cwd=src)
    built = target / "release" / name
    if not built.is_file():
        raise ValueError(f"build produced no binary at {built}")
    return built


def _run_check(argv: list[str], cwd: Path, release: Path) -> dict:
    env = {**os.environ, "MISHE_COMPONENT_RELEASE": str(release)}
    done = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True)
    return {"argv": argv, "code": done.returncode, "output": (done.stdout + done.stderr)[-2000:]}


def promote(home, repo, name: str, sha: str, *, check: list[str], builder=None) -> dict:
    """Build release SHA of NAME from committed bytes, check it, then move ``current`` to it.

    ``builder(staging, repo, name, sha) -> binary`` defaults to a cargo build of
    rust/NAME at SHA. A failed build or check leaves ``current`` untouched.
    """
    _name(name); _sha(sha)
    if not isinstance(check, list) or not check or not all(isinstance(x, str) and x for x in check):
        raise ValueError("check must be a nonempty argv list")
    full = _git(repo, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
    if full != sha:
        raise ValueError("sha must be a full committed commit in the repository")
    final = release_dir(home, name, sha)
    previous = pointed_sha(home, name)
    if final.exists() and _manifest_ok(final, name, sha):
        staging = None
    else:
        staging = root(home, name) / "releases" / f".staging-{sha}-{os.getpid()}"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        try:
            binary = (builder or _build)(staging, repo, name, sha)
            out = staging / "out"
            (out / "bin").mkdir(parents=True)
            shutil.copy2(binary, out / "bin" / name)
            (out / "bin" / name).chmod(0o755)
            contract = staging / "src" / "rust" / name / "CONTRACT.md"
            if contract.is_file():
                shutil.copy2(contract, out / "CONTRACT.md")
            manifest = {"name": name, "sha": sha, "bin_sha256": _sha256(out / "bin" / name),
                        "check": check, "built_at": time.time()}
            (out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            checked = _run_check(check, out, out)
            if checked["code"] != 0:
                raise RuntimeError(f"check failed for {name}@{sha}: exit {checked['code']}: {checked['output']}")
            shutil.rmtree(staging / "src", ignore_errors=True); shutil.rmtree(staging / "target", ignore_errors=True)
            final.parent.mkdir(parents=True, exist_ok=True)
            if final.exists():
                quarantine = final.parent / f".corrupt-{sha}-{time.time_ns()}"
                os.rename(final, quarantine)
                try:
                    os.rename(out, final)
                except OSError:
                    os.rename(quarantine, final)
                    raise
            else:
                os.rename(out, final)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        finally:
            if staging is not None and staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
    _record(home, name, {"event": "promote", "name": name, "sha": sha, "previous": previous})
    _swap(home, name, sha)
    verdict = verify(home, name)
    if verdict["state"] != "GREEN":
        if previous:
            _swap(home, name, previous)
            restored = verify(home, name)
            _record(home, name, {"event": "auto-rollback", "name": name, "from": sha, "to": previous,
                                 "problems": verdict["problems"], "verify": restored})
            return {"phase": "rolled-back" if restored["state"] == "GREEN" else "unknown",
                    "name": name, "sha": sha, "current": pointed_sha(home, name), "verify": verdict,
                    "rollback_verify": restored}
        _record(home, name, {"event": "promotion-red", "name": name, "sha": sha,
                             "problems": verdict["problems"]})
        return {"phase": "unknown", "name": name, "sha": sha,
                "current": pointed_sha(home, name), "verify": verdict}
    return {"phase": "promoted", "name": name, "sha": sha, "previous": previous, "verify": verdict}


def _manifest_ok(release: Path, name: str, sha: str) -> bool:
    try:
        manifest = ctext.decode_manifest((release / "manifest.json").read_text())
        return manifest.get("sha") == sha and _sha256(release / "bin" / name) == manifest.get("bin_sha256")
    except (OSError, ValueError):
        return False


def rollback(home, name: str) -> dict:
    """Re-point ``current`` at the sha it had before the last promotion."""
    _name(name)
    now = pointed_sha(home, name)
    previous = None
    for event in reversed(_ledger_events(home, name)):
        if event.get("event") == "promote" and event.get("sha") == now:
            previous = event.get("previous")
            break
    if not previous:
        raise ValueError(f"no previous release recorded for {name} at {now}")
    if not _manifest_ok(release_dir(home, name, previous), name, previous):
        raise ValueError(f"previous release {previous} does not verify; refusing to point at it")
    _record(home, name, {"event": "rollback", "name": name, "from": now, "to": previous})
    _swap(home, name, previous)
    verdict = verify(home, name)
    return {"phase": "rolled-back" if verdict["state"] == "GREEN" else "unknown",
            "name": name, "from": now, "to": previous, "verify": verdict}


def verify(home, name: str) -> dict:
    """Detector: GREEN only when pointer, release bytes and runtime pin agree.

    Problems: pointer missing or malformed; release dir or manifest missing; binary
    bytes not matching the recorded sha256; NAME still served from runtime/current.
    """
    _name(name)
    problems: list[str] = []
    sha = pointed_sha(home, name)
    if sha is None:
        problems.append("pointer missing or malformed")
    else:
        release = release_dir(home, name, sha)
        if not (release / "bin" / name).is_file():
            problems.append(f"target binary missing for {sha}")
        elif not _manifest_ok(release, name, sha):
            problems.append(f"bytes do not match the recorded sha256 for {sha}")
    pin = Path(home) / "runtime" / "current" / "bin" / name
    if pin.exists() or pin.is_symlink():
        problems.append("NAME still served from runtime/current/bin")
    return {"name": name, "sha": sha, "state": "RED" if problems else "GREEN", "problems": problems}


def main_component(args) -> int:
    home = Path(args.home)
    try:
        if args.component_command == "verify":
            names = [args.name] if args.name else sorted(p.name for p in (home / "components").glob("*") if p.is_dir())
            result = [verify(home, name) for name in names]
            print(json.dumps(result, indent=2))
            return 0 if result and all(item["state"] == "GREEN" for item in result) else 3
        if args.component_command == "promote":
            if not args.repo:
                raise ValueError("promote needs --repo or MISHE_REPO")
            result = promote(home, args.repo, args.name, args.sha, check=ctext.decode_check(args.check_file.read_text()))
        else:
            result = rollback(home, args.name)
    except (subprocess.CalledProcessError, RuntimeError) as exc:
        print(f"mishe component: {exc}")
        return 3
    print(json.dumps(result, indent=2))
    return 0 if result["verify"]["state"] == "GREEN" and result["phase"] in ("promoted", "rolled-back") else 3
