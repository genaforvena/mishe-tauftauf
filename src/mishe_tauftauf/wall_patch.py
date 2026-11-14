"""Small patch gate: exact scoped bytes, tests, System 1 review, activation/revert."""
from __future__ import annotations
import argparse
import ast
import functools
import base64
import difflib
from datetime import datetime, timezone
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
import re
from pathlib import Path
import shutil
import subprocess

from . import consumer_fence
from .argv_text import decode_argv
from .patch_text import (decode_b64_text, decode_lossy, import_candidates, in_package,
                         is_python, last_segment, module_file, source_parts, split_lines,
                         split_lines_keepends)
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


PATCHABLE_DOTFILES = frozenset({".gitignore"})
"""Tracked repository-root dotfiles a patch may name; every other dot path (hidden dirs, .git, site state) stays refused."""


def target(root, name):
    path = root / name
    hidden = name.startswith(".") and name not in PATCHABLE_DOTFILES
    if Path(name).is_absolute() or ".." in Path(name).parts or hidden or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"invalid patch path: {name}")
    return path


RUN_TIMEOUT_S = 300
# Observe commands drain old in-memory consumers (consumer_fence.drain waits up to DRAIN_TIMEOUT_S) and then start the
# new one. A 300 s kill here reverted a correct delivery (review of moving-pin-5d3c6729): the bound must exceed the drain.
OBSERVE_TIMEOUT_S = consumer_fence.DRAIN_TIMEOUT_S + 180


def run(home, identity, label, command, timeout=RUN_TIMEOUT_S):
    if not command or not all(isinstance(x, str) for x in command):
        raise ValueError("command must be a nonempty argument list")
    result = subprocess.run(command, cwd=home.parent, capture_output=True, timeout=timeout)
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


def committed_or_none(root, name):
    """Bytes ``name`` has at HEAD, or None when ``root`` has no readable HEAD.

    The delivered artifact is a commit, so a manifest file's reviewed bytes can be
    read back from HEAD. A unit-test layout is not a Git checkout; there the caller
    keeps the strict working-tree comparison.
    """
    try:
        return committed_snapshot(root, "HEAD", name)
    except ValueError:
        return None


def tree_drifted(root, name, after):
    """Has the shared checkout's ``name`` left the reviewed ``after`` bytes?

    ``after`` is the reviewed snapshot. A tree that now equals HEAD's committed
    bytes is a peer's commit during the check window, not drift of the delivered
    bytes: what is delivered is the commit, ``activate`` materializes the release
    worktree from it, and ``bind_reviewed_source`` binds that worktree to
    ``after``. Only an uncommitted local write to the path, or a layout with no
    readable HEAD, is drift.
    """
    current = snapshot(target(root, name))
    if current == after:
        return False
    committed = committed_or_none(root, name)
    # Git records only the executable bit, so umask can leave the checkout mode
    # different from the committed one; the commit's content is what a peer's
    # commit moved, and mode drift is neither readable nor delivered here.
    return committed is None or current is None or current["bytes"] != committed["bytes"]


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


# The reviewer needs what the patch calls and what calls the changed code, not
# whole modules: a 400 KB module hides the one caller under unrelated text, yet
# a reviewer that cannot see a caller answers unknown. So each name the patch
# imports from the package is sent as its definition, and each changed
# definition is sent as bounded windows around its call sites.
CONTEXT_EXCERPT_BYTES = 64_000  # at 12 000 twelve call-path modules were cut; at 48 000 wall.py still was
CONTEXT_TOTAL_BYTES = 640_000  # 7b705c2e's full context measured 565 KB with every entry whole
CALLER_SITES = 60  # `check` has 18 sites and `main` 23; callers:UNKNOWN stayed truncated at 40
CALLER_RADIUS = 2
RESOLVE_HOPS = 2


def _definitions(text):
    """Source text of every def, class and top-level assignment, by dotted name."""
    found = {}

    def walk(nodes, prefix):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                found[prefix + node.name] = ast.get_source_segment(text, node) or ""
                walk(node.body, prefix + node.name + ".")
            elif isinstance(node, ast.Assign) and not prefix:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        found[target.id] = ast.get_source_segment(text, node) or ""
            elif isinstance(node, ast.AnnAssign) and not prefix and isinstance(node.target, ast.Name):
                found[node.target.id] = ast.get_source_segment(text, node) or ""

    walk(ast.parse(text).body, "")
    return found


def _imported_names(name, text):
    """(package module, name) for each name a patched file imports from the package."""
    owner = source_parts(name)
    pairs = []
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.ImportFrom):
            continue
        parts = owner[:-1]
        for _ in range(max(node.level - 1, 0)):
            parts = parts[:-1]
        if node.level:
            module = ".".join(parts + ([node.module] if node.module else []))
        else:
            module = node.module or ""
        if not module or not in_package(module):
            continue
        pairs.extend((module, alias.name) for alias in node.names if alias.name != "*")
    return pairs


def _definition_diff(record):
    """(changed, removed) names between the base and the patched bytes. A removed
    name still matters: a deleted symbol with a surviving caller is exactly what
    the reviewer must see."""
    changed, removed = set(), set()
    for name, values in record["files"].items():
        if not is_python(name):
            continue
        try:
            after = _definitions(decode_b64_text(values["after"]["bytes"])) if values["after"] else {}
            before = _definitions(decode_b64_text(values["before"]["bytes"])) if values["before"] else {}
        except SyntaxError:
            continue  # named as unresolved by symbol_context
        changed.update(last_segment(qual) for qual, text in after.items() if before.get(qual) != text)
        removed.update(last_segment(qual) for qual in before if qual not in after)
    return changed | removed, removed


def _changed_names(record):
    """Names whose definition differs between the base and the patched bytes."""
    return _definition_diff(record)[0]


def _resolve_export(root, src, module, alias, patched, depth=0):
    """(repo path, definition text) for `alias` as seen through `module`.

    A name the module only re-exports (`from .impl import alias`) is followed one
    import hop at a time, up to a small depth, so the reviewer gets the definition.
    """
    path = module_file(src, module)
    if path is None or path.relative_to(root).as_posix() in patched:
        return None
    rel = path.relative_to(root).as_posix()
    text = decode_lossy(path.read_bytes())
    try:
        defs = _definitions(text)
        imports = _imported_names(rel, text)
    except SyntaxError:
        return None  # a broken dependency is named as unresolved, never raised
    if alias in defs:
        return rel, defs[alias]
    if depth >= RESOLVE_HOPS:
        return None
    for next_module, next_alias in imports:
        if next_alias == alias and next_module != module:
            found = _resolve_export(root, src, next_module, next_alias, patched, depth + 1)
            if found is not None:
                return found
    return None


@functools.lru_cache(maxsize=512)
def _parsed(text):
    try:
        return ast.parse(text)
    except SyntaxError:
        return None


def _symbol_references(text, symbol):
    """0-based line numbers where a file that imports `symbol` from the package uses it."""
    tree = _parsed(text)
    if tree is None:
        return []
    imports = [node for node in ast.walk(tree)
               if isinstance(node, ast.ImportFrom)
               and any(alias.name == symbol for alias in node.names)
               and (node.level or in_package(node.module or ""))]
    if not imports:
        return []
    lines = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == symbol:
            lines.add(node.lineno - 1)
        elif isinstance(node, ast.Attribute) and node.attr == symbol:
            lines.add(node.lineno - 1)
    lines.update(node.lineno - 1 for node in imports)
    return sorted(lines)


def _statement_names(node):
    names = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            names.add(child.attr)
    return names


def _defined_names(node):
    """Names a top-level statement defines: a def, a class, or plain assignment targets."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, ast.Assign):
        return {target.id for target in node.targets if isinstance(target, ast.Name)}
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return {node.target.id}
    return set()


def _reduce_definition(text, wanted):
    """(text, kept_bytes, cut_bytes, hard_cut) for one imported definition.

    A definition within the excerpt cap is sent whole. An oversized one keeps its
    header (signature and docstring) and each body statement that names something
    the patch changes or imports; every other run of statements becomes a one-line
    note with its line range and byte count, so the reviewer sees what was left
    out. Only a definition that still does not fit after that is hard-cut, and that
    cut is named as truncated.
    """
    if len(text) <= CONTEXT_EXCERPT_BYTES:
        return text, len(text.encode()), 0, False
    tree = _parsed(text)
    if tree is None or not tree.body:
        cut = text[:CONTEXT_EXCERPT_BYTES]
        return cut, len(cut.encode()), len(text.encode()) - len(cut.encode()), True
    lines = split_lines_keepends(text)
    # A definition is reduced inside its body. A whole module (a submodule imported with
    # `from . import sub`, which is never one definition) is reduced at its top level,
    # so an import or a docstring first statement no longer forces a hard cut.
    owner = tree.body[0]
    module = not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    body, indent = (tree.body, "") if module else (owner.body, "    ")
    # Header: signature plus docstring when the first statement is one.
    statements = list(body)
    header_end = 0 if module else len(lines)
    if body:
        first = body[0]
        is_doc = (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                  and isinstance(first.value.value, str))
        if is_doc:
            header_end = first.end_lineno
            statements = body[1:]
        elif not module:
            header_end = first.lineno - 1
    head = lines[:header_end]
    head_budget = CONTEXT_EXCERPT_BYTES // 4
    head_bytes = sum(len(item.encode()) for item in head)
    if head_bytes > head_budget:
        # An oversized header (a long docstring) keeps its first lines within a quarter of
        # the excerpt cap and names the rest, so the statements the patch uses still fit.
        used, keep = 0, 0
        while keep < len(head) and used + len(head[keep].encode()) <= head_budget:
            used += len(head[keep].encode())
            keep += 1
        head = head[:keep] + [f"{indent}# header lines {keep + 1}-{header_end} ({head_bytes - used} bytes) omitted: documentation beyond the excerpt budget\n"]
    kept = ["".join(head)]
    run = []  # consecutive omitted statements: (first line, last line, bytes)
    def flush():
        if run:
            first_line, last_line = run[0][0], run[-1][1]
            size = sum(item[2] for item in run)
            kept.append(f"{indent}# omitted lines {first_line}-{last_line} ({size} bytes): no name the patch changes or imports\n")
            run.clear()
    for stmt in statements:
        segment = "".join(lines[stmt.lineno - 1:stmt.end_lineno])
        # Inside a whole module a statement is kept when it defines a wanted name; a function
        # that merely calls one is not the definition the reviewer needs. Inside a definition,
        # a statement that names a wanted name is kept as before.
        if isinstance(stmt, (ast.Import, ast.ImportFrom)) or _defined_names(stmt) & wanted or (not module and _statement_names(stmt) & wanted):
            flush()
            kept.append(segment)
        else:
            run.append((stmt.lineno, stmt.end_lineno, len(segment.encode())))
    flush()
    result = "".join(kept)
    kept_bytes = len(result.encode())
    cut = max(len(text.encode()) - kept_bytes, 0)
    if len(result) > CONTEXT_EXCERPT_BYTES:
        result = result[:CONTEXT_EXCERPT_BYTES]
        return result, len(result.encode()), len(text.encode()) - len(result.encode()), True
    return result, kept_bytes, cut, False


def _attribute_uses(sources, alias):
    """Attribute names the patch reads through `alias` (`alias.name`), across its sources."""
    names = set()
    for _, encoded in sources:
        try:
            tree = ast.parse(decode_b64_text(encoded))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == alias:
                names.add(node.attr)
    return names


def symbol_context(home, record):
    """Read-only excerpts a reviewer needs: definitions the patch imports and
    bounded call-site windows for each changed definition in the shared checkout.

    Anything omitted, cut or unresolved is named, so the reviewer can see the
    context is incomplete instead of assuming it is whole.
    """
    root = home.parent
    src = root / "src"
    patched = set(record["files"])
    unresolved, truncated, omitted = [], [], []
    submodules = {}  # label -> alias, for a whole submodule named by `from . import sub`
    ordered = []  # imported definitions, (label, path, text), before the total cap
    callers = []  # changed-definition call sites: the reviewer's cheapest must-have, budgeted first
    changed, removed = _definition_diff(record)
    wanted = set(changed)  # names a reduced definition must keep: changed here or imported by the patch

    # Patched files and the delivery scripts that apply them are both reviewed
    # bytes: an import either one makes must resolve, or the reviewer cannot judge.
    scripts = (record.get("delivery_plan") or {}).get("artifacts") or {}
    sources = [(name, values["after"]["bytes"]) for name, values in record["files"].items()
               if values["after"] and is_python(name)]
    sources += [(name, values["bytes"]) for name, values in scripts.items() if is_python(name)]
    for name, encoded in sources:
        try:
            pairs = _imported_names(name, decode_b64_text(encoded))
        except SyntaxError:
            unresolved.append(f"syntax:{name}")
            continue
        wanted.update(alias for _, alias in pairs)
        for module, alias in pairs:
            path = module_file(src, module)
            if path is not None and path.relative_to(root).as_posix() in patched:
                continue  # the reviewer already has this module's after bytes
            if path is None and "." not in module:
                continue  # a bare package (`from . import x`) has no module file here
            found = _resolve_export(root, src, module, alias, patched)
            if found is not None:
                ordered.append((f"{module}:{alias}", found[0], found[1]))
                continue
            # `from . import sub` names a submodule, not an export: include its
            # bytes instead of dropping it silently (the reviewer then lacks evidence).
            sub = module_file(src, f"{module}.{alias}")
            if sub is None:
                unresolved.append(f"{module}:{alias}")
            elif sub.relative_to(root).as_posix() not in patched:
                ordered.append((f"{module}.{alias}", sub.relative_to(root).as_posix(),
                                sub.read_text(errors="replace")))
                submodules[f"{module}.{alias}"] = alias

    corpus = {}
    for base in (src, root / "checks", root / "tests", root / "tools"):
        if base.is_dir():
            for path in sorted(base.rglob("*.py")):
                rel = path.relative_to(root).as_posix()
                if rel not in patched:
                    corpus[rel] = path.read_text(errors="replace")
    for symbol in sorted(changed):
        windows, sites = [], 0
        for rel, text in corpus.items():
            refs = _symbol_references(text, symbol)
            if not refs:
                continue
            lines = split_lines(text)
            sites += len(refs)
            for index in refs:
                if len(windows) < CALLER_SITES:
                    low, high = max(0, index - CALLER_RADIUS), index + CALLER_RADIUS + 1
                    windows.append(f"{rel}:{index + 1}\n" + "\n".join(lines[low:high]))
        if windows:
            callers.append((f"callers:{symbol}", "shared checkout", "\n---\n".join(windows)))
            if sites > CALLER_SITES:
                truncated.append(f"callers:{symbol}")
        elif symbol in removed:
            # A removed name with no surviving reference is evidence, not silence:
            # say the search ran so the reviewer does not assume unseen callers.
            callers.append((f"callers:{symbol}", "shared checkout",
                            "no imported reference found in src/, checks/, tests/ or tools/"))

    cut_bytes, reduced = {}, []
    for label, path, text in ordered:
        # A whole submodule the patch does not change keeps only the statements that name
        # what the patch reads through it (`alias.attr`). Common import names (json, Path)
        # and other modules' changed names must not pull in every function of a 400 KB module.
        scope = wanted
        if label in submodules:
            scope = _attribute_uses(sources, submodules[label])
        text, _kept, cut, hard = _reduce_definition(text, scope)
        if hard:
            truncated.append(label)
        cut_bytes[label] = cut
        reduced.append((label, path, text))
    modules, total, seen, budget = {}, 0, set(), {}
    for label, path, text in callers + reduced:
        # A name imported by several patched files is one excerpt: charge the
        # budget once, or duplicates crowd out distinct context.
        if label in seen:
            continue
        seen.add(label)
        if len(text) > CONTEXT_EXCERPT_BYTES:
            text = text[:CONTEXT_EXCERPT_BYTES]
            truncated.append(label)
        if total + len(text) > CONTEXT_TOTAL_BYTES:
            omitted.append(label)
            continue
        total += len(text)
        modules[label] = {"path": path, "text": text}
        budget[label] = {"kept_bytes": len(text.encode()), "cut_bytes": cut_bytes.get(label, 0)}
    return {"modules": modules, "truncated": sorted(set(truncated)),
            "omitted_over_limit": sorted(set(omitted)), "unresolved": sorted(set(unresolved)),
            "budget": budget,
            "source": "shared checkout at review time; patch files use their after bytes"}


def review(home, record):
    from .codex_review import validate_response
    config_path = home / "patch-review.json"
    config = json.loads((config_path if config_path.exists() else home / "publication-check.json").read_text())
    context = symbol_context(home, record)
    evidence = {"runtime": record["runtime"], "phase": record["phase"], "test": record["test"],
                "test_output": Path(record["test"]["log"]).read_text(), "files": {},
                "context_modules": context}
    # Omitted, truncated or unresolved modules mean the reviewer lacks evidence
    # it needs; say so rather than claiming a complete context.
    context_complete = not (context["omitted_over_limit"] or context["truncated"] or context["unresolved"])
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
        "context": {"context_complete": context_complete, "evidence_references": [reference], "patch": evidence}}
    # The outer budget must exceed the reviewer's own model-call budget, or the
    # gate kills a reviewer that would have finished and reports a false refusal.
    outer = _worker_timeout(config)
    result = json.loads(_worker(config["command"], json.dumps(request).encode(), outer))
    validate_response(request, result)
    return {"clear": result["results"][0].get("verdict") == "clear", "request_hash": request["input_hash"], "response": result}


def check(home, identity, command, *, activate=None, observe=None, revert_observe=None, revert=None, description=None):
    from .delivery_requests import check_admission
    with lock(home):
        check_admission(home)
        record = status(home, identity)
        # The reviewer judges intent from this field; without it an unknown
        # verdict on the same bytes is the only possible answer.
        if description:
            record["description"] = description
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
        changed = [name for name, values in record["files"].items()
                   if tree_drifted(home.parent, name, values["after"])]
        if changed:
            # Bytes changed under the check; any earlier verdict was on those bytes.
            # Name the files: a bare phase cannot tell a peer's commit to a shared
            # checkout from a test that wrote to a tracked file, and that difference
            # picks the fix. Every sibling failure phase already records `failure`.
            record.update(phase="test-mutated-source",
                          failure="source changed during deterministic check: " + ", ".join(sorted(changed)))
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
        if tree_drifted(home.parent, name, values["after"]):
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
        record["observation"] = run(home, identity, "observe", observe, timeout=OBSERVE_TIMEOUT_S)
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
            record["revert_observation"] = run(home, identity, "revert-observe", plan["revert_observe"], timeout=OBSERVE_TIMEOUT_S)
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
    parser.add_argument("--description", help="what changed and why, shown to the strong reviewer")
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.home, args.id, args.files or [], base=args.base)
    elif args.action == "check":
        check(args.home, args.id, decode_argv(args.command),
              activate=decode_argv(args.activate),
              observe=decode_argv(args.observe),
              revert_observe=decode_argv(args.revert_observe),
              revert=decode_argv(args.revert), description=args.description)
    elif args.action == "apply":
        apply(args.home, args.id, decode_argv(args.activate), decode_argv(args.observe))
    elif args.action == "revert":
        revert(args.home, args.id, decode_argv(args.activate))
    elif args.action == "verify":
        verify(args.home, args.id)
    print(json.dumps(status(args.home, args.id), indent=2))


if __name__ == "__main__":
    main()
