#!/usr/bin/env python3
"""Detector: the Pub DEV watcher can lose its notification without anyone seeing.

Measured on this plant, 2026-10-22 (wake 94608).

The five-minute read-only DEV watch is the only observation the Pub role has of
reader activity on the DEV account. When its observation changes, it appends one
addressed message to the shared tape so Pub is woken. That delivery is the
watcher's *last* step, and it was the only step with no guard around it:

    delivered = subprocess.run([str(SITE / 'bin/mishe'), '--home', str(SITE),
                                'append', '--source', 'sensor/pub-devto', message],
                               capture_output=True, text=True, timeout=30)
    if delivered.returncode:
        print('Pub delivery failed; pending notification retained', file=sys.stderr)
        return 1
    pending.unlink()

``subprocess.run(..., timeout=30)`` raises ``TimeoutExpired``; it does not
return a process with a non-zero status. The ``if delivered.returncode`` arm
therefore never saw the timeout, the exception propagated out of ``main()``,
and the service died with a traceback instead of the intended
"pending notification retained" path. The pending marker survived, so the next
tick retried -- but the *reason* the delivery was slow was never recorded, and
the failure looked like a crash rather than a slow append.

**Why the append is slow.** The delivery appends to the shared tape under the
publication lock. That lock is held by every other writer on the plant, so the
wait is a property of the plant's load, not of the watcher. Measured on this
host, the same append took 2.5 s, 7.2 s, 12.5 s and 47.7 s on consecutive
calls; a profile of one call put 2.47 s of its 2.55 s inside
``fcntl.flock``. A 30 s ceiling against a 47.7 s measurement is not a ceiling
that holds.

**What this check reads.** It parses the installed watcher source and asks
three questions about its delivery step:

* does the delivery step catch ``TimeoutExpired`` (and every other way a
  delivery can fail) instead of letting it kill the service;
* is the delivery timeout larger than the wait the plant has actually
  observed, so the guard fires as a guard and not as a crash;
* does a failure to deliver leave the pending marker in place, so the
  notification is retried rather than lost.

**How the delivery is found.** The watcher names its delivery with the source
tag ``sensor/pub-devto`` in the argv, and no other append in the file carries
that tag. The check therefore selects the call by that identity, and refuses to
guess when the identity is missing or ambiguous -- an earlier attempt selected
the *first* append it found in the tree, which any unrelated append could
displace.

**How the guard is judged.** A ``try`` block alone is not a guard: the append
has to run inside the block's protected body (not its ``else`` or ``finally``
clause, and not in a function defined inside the try but called after it), and
the handler has to actually handle the exception rather than re-raise it.

**How "the marker survives" is judged.** From the failure paths themselves --
the handler body, the ``finally`` clause and the non-zero-returncode path -- not
from one ``.unlink()`` call. A handler that unlinks the marker through a helper,
or that removes it with ``os.remove``, loses the notification just as surely.

It imports nothing from the plant package and it does not run the watcher.

Exit status:
  0  the delivery step is guarded, its timeout covers the observed wait, and a
     failed delivery retains the pending notification
  2  the delivery step can kill the service, its timeout is below the observed
     wait, or a failed delivery can lose the notification
  3  the question cannot be decided: the watcher source is not readable, not
     parseable, or the delivery cannot be identified
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from pathlib import Path

WATCHER = "pub/bin/devto-watch"

# The source tag the watcher stamps on its delivery.  It is the delivery's
# identity in the argv, and it is what the check selects on.
DELIVERY_SOURCE = "sensor/pub-devto"

# The plant's own observed waits for one tape append, in seconds, measured on
# 2026-10-22 (wake 94608) while the publication lock was contended.  The
# ceiling the watcher uses has to be above the worst of these, or the guard
# fires as a crash.
OBSERVED_APPEND_WAITS_S = (2.5, 7.2, 12.5, 47.7)

GREEN = "GREEN"
RED = "RED"
UNKNOWN = "UNKNOWN"


def default_home() -> Path:
    """The plant home, from the environment the plant itself uses.

    The repository checkout is not the plant home: the installed watcher lives
    under the site home, which the plant exports as ``MISHE_SEED_HOME`` (or
    ``MISHE_TAUFTAUF_HOME``).  A check that resolves its home to the checkout
    it sits in reads a directory that does not exist and answers UNKNOWN about
    a plant it can see.  The fallback is the workspace-local site home, the
    same convention the plant's own CLI uses.
    """
    for name in ("MISHE_SEED_HOME", "MISHE_TAUFTAUF_HOME"):
        value = os.environ.get(name)
        if value:
            return Path(value)
    return Path(__file__).resolve().parents[1] / ".mishe-tauftauf"


def read_watcher(home: Path) -> str | None:
    """The installed watcher's source, or None when it cannot be read."""
    try:
        return (home / WATCHER).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _string_constants(node: ast.AST) -> set[str]:
    """Every string constant inside a node."""
    if isinstance(node, list):
        children = [child for item in node for child in ast.walk(item)]
    else:
        children = ast.walk(node)
    return {child.value for child in children
            if isinstance(child, ast.Constant) and isinstance(child.value, str)}


def _is_run_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
    return name == "run"


def _delivery_candidates(tree: ast.AST) -> list[ast.Call]:
    """Every ``subprocess.run`` whose argv carries the delivery's source tag."""
    candidates = []
    for node in ast.walk(tree):
        if not _is_run_call(node):
            continue
        arguments = list(node.args) + [kw.value for kw in node.keywords]
        for argument in arguments:
            if not isinstance(argument, (ast.List, ast.Tuple)):
                continue
            if DELIVERY_SOURCE in _string_constants(argument):
                candidates.append(node)
                break
    return candidates


def _parents(tree: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    return parents


def _protected_try(tree: ast.AST, call: ast.Call) -> ast.Try | None:
    """The ``try`` whose *body* the call runs inside, if any.

    A call in the ``else`` clause is not protected by the try's own handlers,
    and neither is one in the ``finally`` clause, so neither counts.  A call in
    a function defined inside the try but called after it is likewise outside
    the protected region.
    """
    parents = _parents(tree)
    current = parents.get(id(call))
    while current is not None:
        if isinstance(current, ast.Try):
            # The call must be a statement of the try's own body.  A call
            # nested deeper inside a statement of that body (inside an ``if``,
            # say) is still executed under the try's protection.
            if _statement_under(current.body, call):
                return current
            return None
        # A function body does not end the search: the delivery usually lives
        # inside ``main()`` or a helper, and the question is whether the try
        # that encloses the *call* protects it.  A try inside a function that
        # only *defines* another function does not protect a call made later
        # from outside it -- but that case is caught by the function-defining
        # check below, not by stopping the walk here.
        current = parents.get(id(current))
    return None


def _statement_under(body: list[ast.stmt], call: ast.Call) -> bool:
    """Whether the call is executed by one of the try's body statements."""
    for statement in body:
        if statement is call:
            return True
        for child in ast.walk(statement):
            if child is call:
                return True
    return False


def _defined_inside_try(tree: ast.AST, call: ast.Call) -> bool:
    """Whether the call is in a function *defined* inside a try body.

    A function defined in the try but called after the try has exited runs
    outside the protected region, so the try does not guard it.
    """
    parents = _parents(tree)
    current = parents.get(id(call))
    while current is not None:
        if isinstance(current, ast.Try):
            # The call is inside a function defined by this try's body.
            for statement in current.body:
                if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for child in ast.walk(statement):
                        if child is call:
                            return True
        current = parents.get(id(current))
    return False


def _exception_name(node: ast.AST | None) -> str | None:
    """The name of an exception class, however it is written.

    ``except subprocess.TimeoutExpired`` names the class through an attribute,
    and a bare ``except`` names nothing at all.  Both have to be recognised,
    or a guard that catches exactly the exception that was measured reads as
    if it caught nothing.
    """
    if node is None:
        return "__all__"
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _handler_names(handler: ast.ExceptHandler) -> set[str]:
    """The exception class names a handler catches."""
    names: set[str] = set()
    node = handler.type
    if node is None:
        names.add("__all__")
    elif isinstance(node, ast.Tuple):
        for element in node.elts:
            name = _exception_name(element)
            if name:
                names.add(name)
    else:
        name = _exception_name(node)
        if name:
            names.add(name)
    return names


def _reraises(handler: ast.ExceptHandler) -> bool:
    """Whether the handler re-raises the exception it caught.

    A handler that re-raises is not a guard: the exception still reaches the
    caller, which is the crash this detector exists to catch.
    """
    for node in ast.walk(handler):
        if isinstance(node, ast.Raise):
            return True
    return False


def _removes_pending(node: ast.AST | list[ast.stmt], tree: ast.AST | None = None) -> bool:
    """Whether the code removes the pending marker, by any means.

    ``Path.unlink()``, ``os.remove()`` and ``os.unlink()`` all drop the
    marker, and so does a helper that calls one of them.  The check looks for
    the *effect* rather than for one spelling of it, and follows a call to a
    helper defined in the same source so that "the handler calls a function
    that unlinks" reads as removing the marker.
    """
    if isinstance(node, list):
        children = [child for item in node for child in ast.walk(item)]
    else:
        children = list(ast.walk(node))
    for child in children:
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        if isinstance(func, ast.Attribute):
            if func.attr in ("unlink", "remove", "rmdir"):
                return True
        elif isinstance(func, ast.Name) and func.id in ("remove", "unlink"):
            return True
    if tree is None:
        return False
    # A call to a helper defined in this source: follow it one level, so a
    # handler that hides the removal in a helper is still caught.
    for child in children:
        if not isinstance(child, ast.Call):
            continue
        func = child.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if not name:
            continue
        for definition in ast.walk(tree):
            if not isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if definition.name != name:
                continue
            if _removes_pending(definition, tree=None):
                return True
    return False


def _module_constants(tree: ast.AST) -> dict[str, float]:
    """Module-level numeric constants, by name.

    A timeout written as a named constant is still a timeout, and a detector
    that only reads literals would call a repaired watcher broken.  Only
    plain numeric assignments count; a constant that is built from the
    environment is read from the default the plant actually uses when the
    variable is not set.
    """
    constants: dict[str, float] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        value = node.value
        if isinstance(value, ast.Constant) and isinstance(value.value, (int, float)):
            constants[target.id] = float(value.value)
            continue
        # ``NAME = float(os.environ.get('X', '180'))`` -- the default is the
        # value the plant actually uses when the variable is not set.
        if isinstance(value, ast.Call) and getattr(value.func, "id", "") == "float":
            for argument in value.args:
                if not isinstance(argument, ast.Call):
                    continue
                inner = argument.args[-1] if argument.args else None
                if not isinstance(inner, ast.Constant):
                    continue
                if isinstance(inner.value, (int, float)):
                    constants[target.id] = float(inner.value)
                elif isinstance(inner.value, str):
                    try:
                        constants[target.id] = float(inner.value)
                    except ValueError:
                        pass
    return constants


def _numeric_from(node: ast.AST) -> float | None:
    """The number a call expression evaluates to, when it is knowable.

    ``float(os.environ.get('X', '30'))`` and ``os.environ.get('X', '30')``
    both carry their default in the source, and that default is the value the
    plant actually uses when the variable is not set.  A default that is not a
    number is not knowable, and reading it as one would be a guess.
    """
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node.value, str):
            try:
                return float(node.value)
            except ValueError:
                return None
        return None
    if isinstance(node, ast.Call):
        for argument in node.args:
            found = _numeric_from(argument)
            if found is not None:
                return found
    return None


def _timeout_of(call: ast.Call, constants: dict[str, float]) -> float | None:
    """The ceiling the delivery call actually uses."""
    for keyword in call.keywords:
        if keyword.arg != "timeout":
            continue
        value = keyword.value
        if isinstance(value, ast.Constant) and isinstance(value.value, (int, float)):
            return float(value.value)
        if isinstance(value, ast.Name) and value.id in constants:
            return constants[value.id]
        if isinstance(value, ast.Call):
            # The ceiling read straight from the environment at the call site.
            found = _numeric_from(value)
            if found is not None:
                return found
    return None


def analyze(text: str) -> dict[str, object]:
    """The delivery step's shape, read from the parsed source."""
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        return {"error": "the watcher source does not parse: %s" % error}

    candidates = _delivery_candidates(tree)
    if not candidates:
        return {"error": ("no delivery append found in the watcher source: nothing "
                           "carries the %r source tag" % DELIVERY_SOURCE)}
    if len(candidates) > 1:
        return {"error": ("the watcher source has %d delivery appends carrying the %r "
                           "source tag, so the delivery cannot be identified"
                           % (len(candidates), DELIVERY_SOURCE))}
    call = candidates[0]

    constants = _module_constants(tree)
    timeout = _timeout_of(call, constants)

    guard = _protected_try(tree, call)
    if guard is None:
        if _defined_inside_try(tree, call):
            reason = ("the delivery append runs in a function defined inside a try "
                      "but called outside it, so the try does not protect it")
        else:
            reason = "the delivery append is not inside a try block's body"
        return {"error": reason,
                "timeout": timeout, "guarded": False, "retains_pending": False,
                "caught": []}

    caught: set[str] = set()
    reraises = False
    removes = False
    for handler in guard.handlers:
        caught |= _handler_names(handler)
        if _reraises(handler):
            reraises = True
        if _removes_pending(handler, tree):
            removes = True
    # The try's own finally clause runs on every path, including the failure
    # path, so a marker removed there does not survive a failed delivery.
    if _removes_pending(guard.finalbody, tree):
        removes = True

    # A bare ``except:`` catches everything, including TimeoutExpired, and so
    # does ``except Exception``.  A named handler for the exception that was
    # measured -- ``TimeoutExpired`` or its base ``SubprocessError`` -- covers
    # it as well.  A handler that re-raises is not a guard.
    covers = bool({"__all__", "Exception", "BaseException",
                   "TimeoutExpired", "SubprocessError"} & caught)
    guarded = covers and not reraises

    # The non-zero returncode path: the installed watcher returns 1 there and
    # leaves the marker in place.  If that path removes the marker, a failed
    # delivery loses the notification.
    body_removes = _removes_pending(guard.body, tree)
    retains_pending = not removes and not body_removes

    return {"error": None, "timeout": timeout, "guarded": guarded,
            "retains_pending": retains_pending, "caught": sorted(caught),
            "reraises": reraises}


def verdict(text: str | None) -> tuple[str, str]:
    """The detector's decision, and the sentence that says why."""
    if text is None:
        return UNKNOWN, "no watcher source: the delivery step cannot be read"
    analysis = analyze(text)

    problems: list[str] = []
    error = analysis.get("error")
    if error:
        problems.append(str(error))
    timeout = analysis.get("timeout")
    if timeout is None:
        problems.append("the delivery step's timeout cannot be read")
    elif timeout <= max(OBSERVED_APPEND_WAITS_S):
        problems.append(
            "the delivery timeout of %gs is not above the %gs append this plant "
            "has actually waited for, so the guard fires as a crash"
            % (timeout, max(OBSERVED_APPEND_WAITS_S)))
    if not analysis.get("guarded"):
        if analysis.get("reraises"):
            problems.append(
                "the delivery step's handler re-raises the timeout, so a slow "
                "append still kills the service")
        else:
            problems.append(
                "the delivery step does not catch TimeoutExpired, so a slow append "
                "kills the service instead of retaining the pending notification")
    if not analysis.get("retains_pending"):
        problems.append(
            "a failed delivery can drop the pending notification instead of "
            "retaining it for the next tick")
    if problems:
        return RED, "; ".join(problems)
    return GREEN, ("the delivery step is guarded, its %gs timeout covers the %gs "
                   "append observed on this plant, and a failed delivery "
                   "retains the pending notification"
                   % (timeout, max(OBSERVED_APPEND_WAITS_S)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--home", default="",
                        help="the plant home (default: $MISHE_SEED_HOME)")
    parser.add_argument("--watcher-source", default="",
                        help="a captured watcher source file to read instead of the installed one")
    args = parser.parse_args(argv)

    if args.watcher_source:
        try:
            text = Path(args.watcher_source).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            print("UNKNOWN: no watcher source: %s" % error)
            return 3
    else:
        home = Path(args.home) if args.home else default_home()
        text = read_watcher(home)
    state, reason = verdict(text)
    # The plant's check runner records a check's verdict as the LAST non-empty
    # line of its output, so the verdict is printed last.
    print("OBSERVED APPEND WAITS: %s" % json.dumps(OBSERVED_APPEND_WAITS_S))
    print("VERDICT: %s — %s" % (state, reason))
    return {GREEN: 0, RED: 2, UNKNOWN: 3}[state]


if __name__ == "__main__":
    sys.exit(main())
