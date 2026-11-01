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
    # ``wall clear`` stays out of the trie on purpose so the dispositioned
    # legacy-name span below is still an unknown-leaf finding.
    trie = {"wall": {"show": {}}, "pain": {"read": {}, "watch": {}}}
    corpus = {
        "docs/example.md": (
            "Use `bin/mishe --home SITE wall show --owner ROLE`.\n"
            "Do not use `bin/mishe --home SITE wall resume`.\n"
            "This span names the historical entry point and is separately "
            "dispositioned: `bin/mishe-tauftauf --home SITE wall clear`.\n"
            "Do not use the bare short name with an unknown leaf: "
            "`mishe pain stare ROLE`.\n"
        )
    }
    tool.DISPOSITIONS = {
        ("docs/example.md", "bin/mishe-tauftauf --home SITE wall clear"):
            "external tool, not this CLI"
    }
    checker = SimpleNamespace(
        served_release_src=lambda home: (src.parent, "a" * 40, src),
        reader_corpus=lambda root: corpus,
    )
    monkeypatch.setattr(tool, "load_leaf_checker", lambda: checker)
    monkeypatch.setattr(tool, "leaf_trie", lambda served_src: (trie, set(trie)))
    result = tool.audit(tmp_path / "site")
    assert result["served_src"] == str(src)

    by_span = {finding["span"]: finding for finding in result["findings"]}
    assert len(result["findings"]) == 3
    # Unknown leaves are flagged from the short-name wrapper form and from the
    # bare short command, never because of the recognized command prefix.
    assert by_span["bin/mishe --home SITE wall resume"]["token"] == "resume"
    assert by_span["mishe pain stare ROLE"]["token"] == "stare"
    assert by_span["bin/mishe-tauftauf --home SITE wall clear"]["disposition"] == (
        "external tool, not this CLI")
    assert result["undispositioned"] == [
        by_span["bin/mishe --home SITE wall resume"],
        by_span["mishe pain stare ROLE"],
    ]
    assert result["dispositioned"] == [by_span["bin/mishe-tauftauf --home SITE wall clear"]]


def test_raw_tmux_command_is_not_misclassified_as_site_cli() -> None:
    tool = _load_tool()

    start = tool.invocation_start(["tmux", "list-panes", "-a"], {"tmux"})

    assert start is None


def test_prefix_forms_begin_a_site_cli_invocation() -> None:
    tool = _load_tool()
    tops = {"wall", "feed", "pain", "append", "predict"}

    assert tool.invocation_start(["mishe", "pain", "read"], tops) == 1
    assert tool.invocation_start(["bin/mishe", "feed"], tops) == 1
    assert tool.invocation_start(["bin/mishe-tauftauf", "--home", "SITE",
                                  "append"], tops) == 3
    # The ``-m module`` pair is skipped as one option argument: ``predict`` is
    # at index 5, not 4.
    assert tool.invocation_start(["python3", "-m", "mishe_tauftauf", "--home",
                                  "SITE", "predict"], tops) == 5


def test_the_audit_reads_the_committed_corpus_not_the_working_tree(
        monkeypatch, tmp_path: Path) -> None:
    """A page a clone never receives cannot carry a finding for a reader.

    Seven reader pages are gitignored since commit 237db71f and still exist on
    this checkout, so a working-tree scan audits pages a clone never
    receives.  The audit therefore reads the committed bytes.
    """
    tool = _load_tool()
    src = tmp_path / "served" / "src"
    trie = {"wall": {"show": {}}, "pain": {"read": {}}}
    checker = SimpleNamespace(
        served_release_src=lambda home: (src.parent, "a" * 40, src),
        reader_corpus=lambda root: {"README.md": "run `mishe wall show`\n"},
        committed_reader_corpus=lambda repo: {"README.md": "run `mishe wall show`\n"},
    )
    monkeypatch.setattr(tool, "load_leaf_checker", lambda: checker)
    monkeypatch.setattr(tool, "leaf_trie", lambda served_src: (trie, set(trie)))

    result = tool.audit(tmp_path / "site", checker.committed_reader_corpus(tmp_path))

    assert result["corpus_files"] == 1
    assert result["findings"] == []


def test_a_committed_corpus_cannot_be_emptied_into_a_clean_audit(
        monkeypatch, tmp_path: Path) -> None:
    """An unreadable committed corpus is UNKNOWN, never a clean book."""
    tool = _load_tool()
    src = tmp_path / "served" / "src"
    checker = SimpleNamespace(
        served_release_src=lambda home: (src.parent, "a" * 40, src),
        reader_corpus=lambda root: {},
        committed_reader_corpus=lambda repo: {},
    )
    monkeypatch.setattr(tool, "load_leaf_checker", lambda: checker)
    monkeypatch.setattr(tool, "leaf_trie", lambda served_src: ({}, set()))

    code = tool.main(["--home", str(tmp_path / "site"),
                       "--repo", str(tmp_path / "repo"),
                       "--out", str(tmp_path / "out.json")])

    assert code == 2
