import json
from pathlib import Path

import pytest

from tools.check_parsing import inspect_source, check, main


def policy(root, *, boundaries=("src/adapter.py",), legacy=None):
    (root / "src").mkdir(exist_ok=True)
    for name in boundaries:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).touch()
    path = root / "policy.json"
    path.write_text(json.dumps({"version": 1, "boundaries": list(boundaries), "legacy": legacy or {}}))
    return path


def test_new_parsing_is_rejected_and_literal_boundary_file_is_allowed(tmp_path):
    path = policy(tmp_path, boundaries=("src/adapter.py",))
    (tmp_path / "src/adapter.py").write_text("value = raw.split(':')\n")
    assert check(tmp_path, path).violations == []
    (tmp_path / "src/core.py").write_text("value = raw.split(':')\n")
    assert "src/core.py:1" in check(tmp_path, path).violations[0]


def test_legacy_budget_cannot_hide_changed_or_additional_parsing(tmp_path):
    old = "value = raw.split(':')\n"
    sites = inspect_source("src/core.py", old)
    path = policy(tmp_path, legacy={"src/core.py": {sites[0].fingerprint: 1}})
    (tmp_path / "src/core.py").write_text(old)
    assert check(tmp_path, path).legacy_count == 1
    (tmp_path / "src/core.py").write_text(old + old)
    assert len(check(tmp_path, path).violations) == 1
    (tmp_path / "src/core.py").write_text("value = raw.split('=')\n")
    assert len(check(tmp_path, path).violations) == 1


def test_aliases_and_non_regex_parsing_are_detected():
    code = "from re import compile as make\nimport json as j\np = make('x')\nv = j.loads(raw)\nw = p.fullmatch(raw)\n"
    assert len(inspect_source("src/core.py", code)) == 3


def test_missing_broken_or_empty_policy_cannot_report_success(tmp_path):
    (tmp_path / "src").mkdir()
    with pytest.raises((OSError, ValueError)):
        check(tmp_path, tmp_path / "missing.json")
    path = tmp_path / "policy.json"
    for contents in ("broken", "{}", '{"version": 1, "boundaries": [], "legacy": {}}'):
        path.write_text(contents)
        with pytest.raises(ValueError):
            check(tmp_path, path)


def test_broken_python_cannot_report_success(tmp_path):
    path = policy(tmp_path, boundaries=("src/adapter.py",))
    (tmp_path / "src/adapter.py").write_text("def broken(:\n")
    with pytest.raises(SyntaxError):
        check(tmp_path, path)


def test_missing_declared_boundary_cannot_report_success(tmp_path):
    path = policy(tmp_path)
    (tmp_path / "src/adapter.py").unlink()
    with pytest.raises(ValueError, match="source missing"):
        check(tmp_path, path)


def test_repository_obeys_recorded_boundary_and_legacy_budget():
    root = Path(__file__).resolve().parents[1]
    result = check(root, root / "parsing-policy.json")
    assert result.violations == []


def test_exempt_sites_inside_an_adapter_are_counted_and_stay_out_of_the_legacy_budget(tmp_path):
    path = policy(tmp_path, boundaries=("src/adapter.py",))
    (tmp_path / "src/adapter.py").write_text("a = raw.split(':')\nb = other.splitlines()\n")
    old = "value = raw.split(':')\n"
    sites = inspect_source("src/core.py", old)
    (tmp_path / "src/core.py").write_text(old)
    path.write_text(json.dumps({
        "version": 1,
        "boundaries": ["src/adapter.py"],
        "legacy": {"src/core.py": {sites[0].fingerprint: 1}},
    }))
    result = check(tmp_path, path)
    assert result.violations == []
    assert result.legacy_count == 1
    assert result.exempt_sites == {"src/adapter.py": 2}


def test_a_registered_adapter_may_parse_arbitrary_prose_while_the_same_code_outside_is_rejected(tmp_path):
    """The exemption is granted per path, not per decoded dialect.

    The boundary's contract ("human prose is content; adapters decode external
    dialects") is carried by review and by the doc paragraph, not by the
    checker: registering a path exempts every parsing call in that file. This
    pins that granularity so a narrower rule is a visible policy change rather
    than an unnoticed drift.
    """
    path = policy(tmp_path, boundaries=("src/adapter.py",))
    prose = (
        "import re\n"
        "PROSE = re.compile(r'\\b[A-Z][a-z]+ [a-z]+ of [a-z]+\\b')\n"
        "def interpret(sentence):\n"
        "    return [m.group(0) for m in PROSE.finditer(sentence)]\n"
    )
    (tmp_path / "src/adapter.py").write_text(prose)
    result = check(tmp_path, path)
    assert result.violations == []
    assert result.exempt_sites == {"src/adapter.py": 2}
    (tmp_path / "src/core.py").write_text(prose)
    unregistered = check(tmp_path, path).violations
    assert unregistered and all(item.startswith("src/core.py:") for item in unregistered)


def test_published_output_names_the_exempt_surface(tmp_path, capsys):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/adapter.py").write_text("a = raw.split(':')\n")
    (tmp_path / "parsing-policy.json").write_text(json.dumps({
        "version": 1, "boundaries": ["src/adapter.py"], "legacy": {}}))
    assert main(["--root", str(tmp_path)]) == 0
    printed = capsys.readouterr().out
    assert "1 exempt sites inside 1 approved adapters" in printed
    assert main(["--root", str(tmp_path), "--boundary-surface"]) == 0
    surface = capsys.readouterr().out
    assert "src/adapter.py" in surface
    assert "exempt sites inside approved adapters: 1 across 1 adapters" in surface
