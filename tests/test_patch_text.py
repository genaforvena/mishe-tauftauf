from mishe_tauftauf import patch_text


GIT_TWO_FILES = """diff --git a/a.py b/a.py
index 1111111..2222222 100644
--- a/a.py
+++ b/a.py
@@ -1,1 +1,2 @@
 keep
+++counter;
diff --git a/b.py b/b.py
index 3333333..4444444 100644
--- a/b.py
+++ b/b.py
@@ -1,1 +1,2 @@
 keep
+z = 1
"""

PLAIN_TWO_FILES = """--- a/a.py
+++ b/a.py
@@ -1,1 +1,2 @@
 keep
+y
--- a/b.py
+++ b/b.py
@@ -1,1 +1,2 @@
 keep
+z = 1
"""


def test_git_second_file_header_is_not_content():
    assert patch_text.added_lines(GIT_TWO_FILES) == ["++counter;", "z = 1"]


def test_plain_diff_second_file_header_is_not_content():
    assert patch_text.added_lines(PLAIN_TWO_FILES) == ["y", "z = 1"]


def test_plus_plus_plus_inside_hunk_is_content_in_plain_diff():
    patch = "--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,2 @@\n keep\n+++beta\n"
    assert patch_text.added_lines(patch) == ["++beta"]


def test_removed_line_that_looks_like_header_does_not_end_hunk():
    patch = (
        "--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n"
        "--- old text\n"
        "+new text\n"
        " keep\n"
    )
    assert patch_text.added_lines(patch) == ["new text"]


def test_no_newline_marker_and_blank_additions_are_ignored():
    patch = "@@ -1 +1,2 @@\n-old\n\\ No newline at end of file\n+new\n+   \n"
    assert patch_text.added_lines(patch) == ["new"]


def test_patch_ids_maps_sha_to_id_and_skips_malformed_lines():
    output = "aaa111 sha1\nbbb222 sha2\nmalformed\n\nccc333 sha1\n"
    assert patch_text.patch_ids(output) == {"sha1": "ccc333", "sha2": "bbb222"}
