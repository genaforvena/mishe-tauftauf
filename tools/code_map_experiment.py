#!/usr/bin/env python3
"""Experimental name-based candidate finder; never proof that code is unused."""
from collections import Counter
import os
from pathlib import Path
import subprocess
import sys
from code_map_args import parse


def main():
    root_labels, values = parse(sys.argv[1:])
    paths = [Path(value) for value in values]
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
            def excluded(p):
                return any(part in {"target", "__pycache__", ".git", ".venv", "node_modules"} or part[:7] == ".mishe-" for part in p.parts)

            for directory, dirs, filenames in os.walk(path, onerror=lambda error: problems.append(str(error))):
                dirs[:] = [name for name in dirs if not excluded(Path(directory) / name)]
                files.update(p for name in filenames if not excluded(p := Path(directory) / name)
                             and p.is_file() and p.suffix in parsers)
        else:
            problems.append(f"unsupported or missing path: {path}")
    names, definitions, references = Counter(), [], []
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
                    subtree, found = [node], set()
                    while subtree:
                        item = subtree.pop()
                        if item.type in {"identifier", "type_identifier"} and item != name:
                            found.add(str(source[item.start_byte:item.end_byte], "utf-8", "replace"))
                        subtree.extend(item.children)
                    references.append(found)
            if node.type in {"identifier", "type_identifier"}:
                names[str(source[node.start_byte:node.end_byte], "utf-8", "replace")] += 1
            stack.extend(node.children)
    print("UNKNOWN: candidates are syntactic names, not reachability or safe deletion; dynamic uses, entry points, macros, configuration and unscanned files are unresolved.")
    print("Scope:", ", ".join(map(str, paths)), f"({len(files)} files)")
    revision = subprocess.run(["git", "rev-parse", "--short", "HEAD"], text=True, capture_output=True)
    print("Checkout revision:", revision.stdout[:-1] if revision.returncode == 0 else "UNKNOWN", "(working files may differ)")
    if root_labels:
        roots = set()
        for value in root_labels:
            matches = [i for i, (_, path, line) in enumerate(definitions)
                       if value == f"{path}:{line}"]
            if len(matches) != 1:
                problems.append(f"root {value}: expected exactly one scanned definition, found {len(matches)}")
            roots.update(matches)
        by_name = {}
        for i, (name, path, _) in enumerate(definitions):
            by_name.setdefault((path.suffix, name), []).append(i)
        reached = {i: None for i in roots}
        pending = list(sorted(roots))
        while pending:
            current = pending.pop()
            for name in sorted(references[current]):
                for target in by_name.get((definitions[current][1].suffix, name), []):
                    if target not in reached:
                        reached[target] = current
                        pending.append(target)
        print("UNKNOWN: dynamic dispatch, macros, incomplete roots and unscanned consumers; parse errors invalidate completeness. Name matches are possible references, not proven calls or safe deletion.")
        print("Assumed roots:", ", ".join(root_labels))
        for i, (name, path, line) in enumerate(definitions):
            if i in reached:
                parent = reached[i]
                via = "root" if parent is None else f"via {definitions[parent][1]}:{definitions[parent][2]} {definitions[parent][0]}"
                print(f"POSSIBLE {path}:{line} {name} ({via})")
            else:
                print(f"UNKNOWN {path}:{line} {name} (no path found from supplied roots)")
        for problem in problems:
            print("UNKNOWN:", problem)
        print(f"Scanned {len(definitions)} definitions; {len(problems)} incomplete path(s).")
        return 1 if problems or not files else 0
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
