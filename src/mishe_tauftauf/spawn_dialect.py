"""Decode how a unit's wrapper script spawns a child.

A systemd unit's ``ExecStart`` names a script, and the script declares how
it runs a child. When it declares ``-m <package>``, the child carries the
wrapper's ``PYTHONPATH``, so the spawn is the coupling itself rather than a
textual mention of the checkout.

Two external dialects meet here: the wrapper's own text (its shebang line
and its command string) and Python's command syntax (an argv list). They are
decoded in this module so the consumer reads a decoded answer instead of
interpreting another dialect inside its own file.
"""

import ast
import os
import re
import subprocess
from pathlib import Path

EXEC_START_SCRIPT_LIMIT = 128 * 1024
"""Bytes of a wrapper script read per unit. A script longer than this is read
as its first bytes only, so one large file cannot stretch a reading."""

MODULE_FLAG = "-m"
"""The interpreter flag that names a module to run."""


def spawns_site_package(tree: ast.AST, package: str) -> bool:
    """Whether a parsed script runs ``-m <package>`` as a command argument.

    An argv list (``subprocess.run([sys.executable, "-m", ...])``) and a
    shell string (``subprocess.run("python3 -m ...", shell=True)``) both
    declare it; a ``-m`` passed through a variable has no literal to read and
    is left to the live import-root path.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for arg in node.args:
            if isinstance(arg, (ast.List, ast.Tuple)):
                argv = [element.value for element in arg.elts
                        if isinstance(element, ast.Constant)
                        and isinstance(element.value, str)]
                for index, value in enumerate(argv):
                    if value != MODULE_FLAG or index + 1 == len(argv):
                        continue
                    target = argv[index + 1]
                    if target == package or target.startswith(package + "."):
                        return True
            elif isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if re.search(rf"(?<![\w.-]){MODULE_FLAG}\s+{re.escape(package)}\b",
                             arg.value):
                    return True
    return False


def script_spawns_site_package(unit: str, package: str) -> bool | None:
    """Whether the script a unit's ExecStart executes spawns the site package.

    ``None`` when no executed script can be read or parsed, so the sensor
    cannot say whether the unit couples through a spawned child. A readable
    script with no such spawn is ``False``: the unit runs whatever it runs
    without consuming the site package as a module.
    """
    environment = os.environ.copy()
    environment.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    try:
        result = subprocess.run(["systemctl", "--user", "show", unit,
                                 "-p", "ExecStart"],
                                capture_output=True, text=True, timeout=5,
                                env=environment)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode:
        return None
    from .unit_properties import declared_paths
    checked = False
    for path in declared_paths(result.stdout):
        candidate = Path(path)
        if not candidate.is_file():
            continue
        try:
            with candidate.open("rb") as handle:
                raw = handle.read(EXEC_START_SCRIPT_LIMIT)
        except OSError:
            continue
        checked = True
        first_line = raw.split(b"\n", 1)[0]
        if candidate.suffix != ".py" and not (
                first_line.startswith(b"#!") and b"python" in first_line):
            if re.search(rf"(?<![\w.-]){MODULE_FLAG}\s+{re.escape(package)}\b",
                         raw.decode("utf-8", "replace")):
                return True
            continue
        try:
            tree = ast.parse(raw)
        except SyntaxError:
            return None
        if spawns_site_package(tree, package):
            return True
    return False if checked else None
