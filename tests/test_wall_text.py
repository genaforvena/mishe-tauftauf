"""Ownership comparison for wall notes: the approved wall-text adapter."""
import pytest
from mishe_tauftauf.wall_text import is_peer_copy


def test_identical_notes_are_a_peer_copy():
    assert is_peer_copy("Plan: verify the detector.", b"Plan: verify the detector.")


def test_trailing_blank_lines_are_not_authorship():
    peer = b"Plan: verify the detector.\n"
    assert is_peer_copy("Plan: verify the detector.\n\n\n", peer)
    assert is_peer_copy("Plan: verify the detector.", b"Plan: verify the detector.\n\n")
    assert is_peer_copy("Plan: verify the detector.\n", b"Plan: verify the detector.")


def test_a_content_difference_is_own_text():
    assert not is_peer_copy("Plan: verify the detector myself.", b"Plan: verify the detector.")


def test_an_interior_line_difference_is_own_text():
    assert not is_peer_copy("One.\n\nTwo.", b"One.\nTwo.")


def test_empty_notes_are_not_a_peer_copy_of_a_nonempty_wall():
    assert not is_peer_copy("", b"Plan: verify the detector.")
    assert not is_peer_copy("Plan: verify the detector.", b"")


def test_notes_without_trailing_newlines_match_an_unterminated_wall():
    assert is_peer_copy("Same evidence, new framing", b"Same evidence, new framing")


def test_non_ascii_notes_compare_as_utf8_bytes():
    assert is_peer_copy("Rückfrage an genome\n\n", "Rückfrage an genome".encode("utf-8") + b"\n")
    assert not is_peer_copy("Rückfrage an genome", b"R\xc3\xbcckfrage an genome.")
