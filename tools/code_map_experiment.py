#!/usr/bin/env python3
"""Experimental name-based candidate finder; never proof that code is unused."""
from collections import Counter
from pathlib import Path
import subprocess
import sys


def main():
    if len(sys.argv) < 2:
        print("Usage: code_map_experiment.py FILE_OR_DIRECTORY [...]")
        return 2
    paths = [Path(value) for value in sys.argv[1:]]
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_python
        import tree_sitter_rust
    except ImportError as exc:
        print(f"UNKNOWN: missing optional Tree-sitter dependency: {exc}")
        return 2
    parsers = {".py": Parser(Language(tree_sitter_python.language())),
               ".rs": Parser(Language(tree_sitter_rust.language()))}
    files = set()
    problems = []
    for path in paths:
        if path.is_file() and path.suffix in parsers:
            files.add(path)
        elif path.is_dir():
            files.update(p for p in path.rglob("*") if p.is_file() and p.suffix in parsers
                         and not any(part in {"target", "__pycache__", ".git", ".venv", "node_modules"} or part[:7] == ".mishe-" for part in p.parts))
        else:
            problems.append(f"unsupported or missing path: {path}")
    names, definitions = Counter(), []
    for path in sorted(files):
        try:
            source = path.read_bytes()
            tree = parsers[path.suffix].parse(source)
        except (OSError, ValueError) as exc:
            problems.append(f"{path}: {exc}")
            continue
        if tree.root_node.has_error:
            problems.append(f"{path}: syntax error or incomplete parse")
        stack = [tree.root_node]
        while stack:
            node = stack.pop()
            if node.type in {"function_definition", "class_definition", "function_item"}:
                name = node.child_by_field_name("name")
                if name is not None:
                    definitions.append((str(source[name.start_byte:name.end_byte], "utf-8", "replace"), path, name.start_point.row + 1))
            if node.type in {"identifier", "type_identifier"}:
                names[str(source[node.start_byte:node.end_byte], "utf-8", "replace")] += 1
            stack.extend(node.children)
    print("UNKNOWN: candidates are syntactic names, not reachability or safe deletion; dynamic uses, entry points, macros, configuration and unscanned files are unresolved.")
    print("Scope:", ", ".join(map(str, paths)), f"({len(files)} files)")
    revision = subprocess.run(["git", "rev-parse", "--short", "HEAD"], text=True, capture_output=True)
    print("Checkout revision:", revision.stdout[:-1] if revision.returncode == 0 else "UNKNOWN", "(working files may differ)")
    for problem in problems:
        print("UNKNOWN:", problem)
    print("Candidates with no other same-name identifier in scope:")
    for name, path, line in sorted(definitions, key=lambda d: (str(d[1]), d[2])):
        if names[name] == 1:
            print(f"{path}:{line} {name}")
    print(f"Scanned {len(definitions)} definitions; {len(problems)} incomplete path(s).")
    return 1 if problems or not files else 0


if __name__ == "__main__":
    sys.exit(main())
