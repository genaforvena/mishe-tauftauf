from __future__ import annotations

from mishe_tauftauf.unit_properties import declared_paths


def test_declared_paths_reads_all_three_systemd_renderings() -> None:
    # systemd renders ExecStart as a structured value, WorkingDirectory as one
    # path, and Environment as space-separated KEY=value pairs whose values can
    # name several colon-separated paths; every declared path is collected.
    text = ("ExecStart={ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 "
            "/home/host/scripts/tool follow ; ignore_errors=no }\n"
            "WorkingDirectory=/home/host/work\n"
            "Environment=PATH=/usr/bin:/bin MESH_HOME=/home/host/state\n")
    assert declared_paths(text) == ["/usr/bin/python3", "/home/host/scripts/tool",
                                    "/home/host/work", "/usr/bin", "/bin",
                                    "/home/host/state"]


def test_declared_paths_deduplicates_and_keeps_order() -> None:
    text = ("ExecStart={ path=/opt/a/tool ; argv[]=/opt/a/tool run }\n"
            "Environment=TOOL=/opt/a/tool")
    assert declared_paths(text) == ["/opt/a/tool"]


def test_declared_paths_is_empty_without_an_absolute_path() -> None:
    assert declared_paths("") == []
    # A relative value is not an absolute path: the slash inside ``./tool`` is
    # the value's own separator.
    assert declared_paths("Environment=RELATIVE=./tool\n") == []
    assert declared_paths("Environment=RELATIVE=some/where/tool\n") == []


def test_declared_paths_stops_at_the_renderings_own_punctuation() -> None:
    # A path ends where systemd's list punctuation begins, not inside it.
    text = ("ExecStart={ path=/opt/tool ; argv[]=/opt/tool \"quoted arg\" }\n"
            "Environment=PATH=/usr/bin:/usr/local/bin,\n")
    assert declared_paths(text) == ["/opt/tool", "/usr/bin", "/usr/local/bin"]
