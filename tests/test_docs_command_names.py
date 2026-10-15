from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

_CHECKOUT = Path(__file__).resolve().parents[1]
_TOOL = _CHECKOUT / "tools" / "check_docs_command_names.py"


def _load_tool():
    spec = importlib.util.spec_from_file_location("docs_command_names", _TOOL)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_audit_flags_unknown_subcommand_but_accepts_a_valid_wrapped_invocation(
        monkeypatch, tmp_path: Path) -> None:
    tool = _load_tool()
    src = tmp_path / "served" / "src"
    trie = {"wall": {"show": {}}}
    corpus = {
        "docs/example.md": (
            "Use `bin/mishe-tauftauf --home SITE wall show --owner ROLE`.\n"
            "Do not use `bin/mishe-tauftauf --home SITE wall resume`.\n"
            "This raw command is separately dispositioned: "
            "`bin/mishe-tauftauf --home SITE wall clear`.\n"
        )
    }
    tool.DISPOSITIONS = {
        ("docs/example.md", "bin/mishe-tauftauf --home SITE wall clear"): "external tool, not this CLI"
    }
    checker = SimpleNamespace(
        served_release_src=lambda home: (src.parent, "a" * 40, src),
        reader_corpus=lambda root: corpus,
    )
    monkeypatch.setattr(tool, "load_leaf_checker", lambda: checker)
    monkeypatch.setattr(tool, "leaf_trie", lambda served_src: (trie, set(trie)))
    result = tool.audit(tmp_path / "site")
    assert result["served_src"] == str(src)

    assert len(result["findings"]) == 2
    assert result["findings"][0]["span"].endswith("wall resume")
    assert result["findings"][0]["token"] == "resume"
    assert result["undispositioned"] == [result["findings"][0]]
    assert result["dispositioned"][0]["disposition"] == "external tool, not this CLI"


def test_raw_tmux_command_is_not_misclassified_as_site_cli() -> None:
    tool = _load_tool()

    start = tool.invocation_start(["tmux", "list-panes", "-a"], {"tmux"})

    assert start is None
