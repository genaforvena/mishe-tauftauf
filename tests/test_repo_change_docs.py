"""The repo-change docs seam answers 'which docs name this path' deterministically."""
from pathlib import Path

import pytest

from mishe_tauftauf.repo_change_docs import (
    docs_naming,
    identifiers_for,
    normalize_path,
    passages_for,
)


def _corpus(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "README.md").write_text(
        "The budget_producer writes the file.\n"
        "A budgeting line is not a module name.\n"
        "src/mishe_tauftauf/budget_producer.py is the source.\n",
        encoding="utf-8",
    )
    (root / "docs" / "contract.md").write_text(
        "Import mishe_tauftauf.budget_producer from the package.\n"
        "BUDGET_PRODUCER is named in caps here.\n",
        encoding="utf-8",
    )
    (root / "docs" / "other.md").write_text("Nothing about this change here.\n", encoding="utf-8")
    return root


def test_identifiers_are_precise_longest_first_and_deduplicated():
    assert identifiers_for("src/mishe_tauftauf/budget_producer.py") == [
        "src/mishe_tauftauf/budget_producer.py",
        "mishe_tauftauf.budget_producer",
        "budget_producer",
    ]
    assert identifiers_for("tools/map_tiles.py") == ["tools/map_tiles.py", "map_tiles"]
    assert identifiers_for("tests/__init__.py") == ["tests/__init__.py"]
    assert identifiers_for("docs/decision-rungs.md") == ["docs/decision-rungs.md", "decision-rungs"]


def test_normalize_path_accepts_posix_spelling_and_rejects_escapes():
    assert normalize_path("  ./src/a.py  ") == "src/a.py"
    assert normalize_path("src\\a.py") == "src/a.py"
    for bad in ("", "  ", "/src/a.py", "src/../a.py", "src//a.py", "src/a.py\x00", None, 12):
        with pytest.raises(ValueError, match="path"):
            normalize_path(bad)


def test_token_matching_does_not_promote_a_substring_to_a_naming(tmp_path):
    root = _corpus(tmp_path)
    # "budget" appears only inside longer words, never as a standalone identifier.
    assert passages_for(root, "budget") == {"passages": [], "truncated": False}
    named = passages_for(root, "budget_producer", limit=10)
    assert named["truncated"] is False
    assert named["passages"] == [
        {"line": 1, "text": "The budget_producer writes the file."},
        {"line": 3, "text": "src/mishe_tauftauf/budget_producer.py is the source."},
        {"line": 1, "text": "Import mishe_tauftauf.budget_producer from the package."},
        {"line": 2, "text": "BUDGET_PRODUCER is named in caps here."},
    ]


def test_docs_naming_groups_passages_by_doc_in_corpus_order(tmp_path):
    root = _corpus(tmp_path)
    result = docs_naming(root, ["src/mishe_tauftauf/budget_producer.py"], passages=10)
    assert result["doc_paths"] == ["README.md", "docs/contract.md"]
    change = result["changes"][0]
    assert change["named"] is True
    assert change["identifiers"] == [
        "src/mishe_tauftauf/budget_producer.py",
        "mishe_tauftauf.budget_producer",
        "budget_producer",
    ]
    assert change["docs"] == [
        {
            "path": "README.md",
            "passages": [
                {"line": 1, "text": "The budget_producer writes the file."},
                {"line": 3, "text": "src/mishe_tauftauf/budget_producer.py is the source."},
            ],
        },
        {
            "path": "docs/contract.md",
            "passages": [
                {"line": 1, "text": "Import mishe_tauftauf.budget_producer from the package."},
                {"line": 2, "text": "BUDGET_PRODUCER is named in caps here."},
            ],
        },
    ]
    assert result["summary"] == {
        "paths": 1, "named": 1, "unnamed": 0, "candidate_docs": 2, "truncated": False,
    }


def test_no_candidate_doc_is_a_complete_negative_not_an_error(tmp_path):
    root = _corpus(tmp_path)
    result = docs_naming(root, ["src/mishe_tauftauf/never_seen.py"])
    assert result["doc_paths"] == []
    assert result["changes"][0]["named"] is False
    assert result["summary"]["unnamed"] == 1
    assert result["summary"]["truncated"] is False


def test_changed_paths_deduplicate_and_results_are_repeatable(tmp_path):
    root = _corpus(tmp_path)
    paths = ["src/mishe_tauftauf/budget_producer.py", "tools/absent.py",
             "src/mishe_tauftauf/budget_producer.py"]
    first = docs_naming(root, paths)
    assert [change["path"] for change in first["changes"]] == [
        "src/mishe_tauftauf/budget_producer.py", "tools/absent.py",
    ]
    assert first == docs_naming(root, paths)
    assert first["summary"]["paths"] == 2


def test_passage_limit_is_reported_not_silently_truncated(tmp_path):
    root = _corpus(tmp_path)
    assert passages_for(root, "budget_producer", limit=1)["truncated"] is True
    assert len(passages_for(root, "budget_producer", limit=1)["passages"]) == 1
    assert docs_naming(root, ["src/mishe_tauftauf/budget_producer.py"], passages=1)[
        "summary"]["truncated"] is True
    for bad in (0, -1, 101, True, 1.5, "2"):
        with pytest.raises(ValueError, match="limit"):
            passages_for(root, "budget_producer", limit=bad)
        with pytest.raises(ValueError, match="passages"):
            docs_naming(root, [], passages=bad)


def test_symlink_doc_outside_the_corpus_is_not_followed(tmp_path):
    root = _corpus(tmp_path)
    outside = tmp_path / "private.md"
    outside.write_text("budget_producer private note\n", encoding="utf-8")
    (root / "docs" / "alias.md").symlink_to(outside)
    assert "docs/alias.md" not in docs_naming(root, ["budget_producer.py"])["doc_paths"]


@pytest.mark.parametrize("bad", ["", "  ", None, 42])
def test_identifier_must_be_a_nonblank_string(tmp_path, bad):
    with pytest.raises(ValueError, match="identifier"):
        passages_for(_corpus(tmp_path), bad)

def test_changed_paths_must_be_a_sequence(tmp_path):

    root = _corpus(tmp_path)
    for bad in ("src/a.py", None, {"src/a.py"}):
        with pytest.raises(ValueError, match="changed_paths"):
            docs_naming(root, bad)
