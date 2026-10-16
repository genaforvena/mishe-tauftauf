"""Reader-corpus search preserves citations and distinguishes incomplete reads."""
from pathlib import Path

import pytest

from mishe_tauftauf.docs_tools import search_docs


def _corpus(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("Reader introduction\n", encoding="utf-8")
    (root / "docs").mkdir()
    return root


def test_casefold_literal_search_preserves_paths_lines_and_text(tmp_path):
    root = _corpus(tmp_path)
    (root / "docs" / "z.md").write_text("STRASSE [x].*\n", encoding="utf-8")
    (root / "docs" / "a.md").write_text(
        "Other line\n# Straße [x].*\nSTRASSE [x].* again\n", encoding="utf-8"
    )
    (root / "README.md").write_text("Straße [x].*\n", encoding="utf-8")

    assert search_docs(root, "strasse [x].*") == {
        "matches": [
            {"path": "README.md", "line": 1, "text": "Straße [x].*"},
            {"path": "docs/a.md", "line": 2, "text": "# Straße [x].*"},
            {"path": "docs/a.md", "line": 3, "text": "STRASSE [x].* again"},
            {"path": "docs/z.md", "line": 1, "text": "STRASSE [x].*"},
        ],
        "truncated": False,
    }


def test_limit_reports_only_actual_omitted_matches_and_no_hit_is_complete(tmp_path):
    root = _corpus(tmp_path)
    (root / "README.md").write_text("match\nno\nMATCH\n", encoding="utf-8")
    expected = [
        {"path": "README.md", "line": 1, "text": "match"},
        {"path": "README.md", "line": 3, "text": "MATCH"},
    ]

    assert search_docs(root, "match", limit=1) == {
        "matches": expected[:1], "truncated": True
    }
    assert search_docs(root, "match", limit=2) == {
        "matches": expected, "truncated": False
    }
    assert search_docs(root, "absent", limit=1) == {
        "matches": [], "truncated": False
    }


def test_only_immediate_reader_pages_are_searched_without_symlink_aliases(tmp_path):
    root = _corpus(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("secret needle\n", encoding="utf-8")
    (root / "site" / "private").mkdir(parents=True)
    private = root / "site" / "private" / "reader.md"
    private.write_text("secret needle\n", encoding="utf-8")
    (root / "private.md").write_text("secret needle\n", encoding="utf-8")
    (root / "docs" / "nested").mkdir()
    (root / "docs" / "nested" / "reader.md").write_text(
        "secret needle\n", encoding="utf-8"
    )
    (root / "docs" / "reader.txt").write_text("secret needle\n", encoding="utf-8")
    (root / "docs" / "escape.md").symlink_to(outside)
    (root / "docs" / "private.md").symlink_to(private)
    (root / "docs" / "dangling.md").symlink_to(tmp_path / "missing.md")
    (root / "README.md").unlink()
    (root / "README.md").symlink_to(outside)

    assert search_docs(root, "secret needle") == {"matches": [], "truncated": False}


def test_symlink_docs_directory_is_not_followed(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "README.md").write_text("Reader introduction\n", encoding="utf-8")
    private = root / "site" / "private"
    private.mkdir(parents=True)
    (private / "reader.md").write_text("secret needle\n", encoding="utf-8")
    (root / "docs").symlink_to(private, target_is_directory=True)

    assert search_docs(root, "secret needle") == {"matches": [], "truncated": False}


def test_decode_failure_is_not_hidden_by_no_hit_or_satisfied_limit(tmp_path):
    root = _corpus(tmp_path)
    (root / "README.md").write_text("match\nmatch\n", encoding="utf-8")
    (root / "docs" / "z.md").write_bytes(b"\xff")

    with pytest.raises(UnicodeDecodeError):
        search_docs(root, "absent")
    with pytest.raises(UnicodeDecodeError):
        search_docs(root, "match", limit=1)


def test_read_failure_propagates_instead_of_empty_success(tmp_path, monkeypatch):
    root = _corpus(tmp_path)
    blocked = root / "docs" / "blocked.md"
    blocked.write_text("Reader text\n", encoding="utf-8")
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        if path == blocked:
            raise PermissionError("reader page unavailable")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    with pytest.raises(PermissionError, match="reader page unavailable"):
        search_docs(root, "absent")


@pytest.mark.parametrize("query", ["", " \t\n", None, 42])
def test_query_must_be_a_nonblank_string(tmp_path, query):
    with pytest.raises(ValueError, match="query"):
        search_docs(tmp_path / "missing", query)


@pytest.mark.parametrize("limit", [0, -1, 101, True, 1.5, "2"])
def test_limit_must_be_a_bounded_positive_integer(tmp_path, limit):
    with pytest.raises(ValueError, match="limit"):
        search_docs(tmp_path / "missing", "reader", limit=limit)
