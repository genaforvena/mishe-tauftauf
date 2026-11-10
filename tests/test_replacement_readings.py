import subprocess
from pathlib import Path

from mishe_tauftauf.plant_tmux import parse_list_windows
from mishe_tauftauf.replacement_readings import (
    RENDERER_SENSE_ID, TMUX_SENSE_ID,
    read_renderer_coverage, read_tmux_windows,
)


def _done(code=0, out="", err=""):
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=out, stderr=err)


def test_parse_rejects_malformed_line():
    assert parse_list_windows("a\tb\t2\n") == [{"session": "a", "window": "b", "panes": 2}]
    assert parse_list_windows("a\tb\n") is None
    assert parse_list_windows("a\tb\tx\n") is None
    assert parse_list_windows("") == []


def test_tmux_no_socket_or_probe_failure_is_unknown():
    assert read_tmux_windows(None)["state"] == "unknown"
    assert read_tmux_windows("/s", runner=lambda s, a: None)["state"] == "unknown"


def test_tmux_nonzero_exit_is_unknown_with_cause():
    out = read_tmux_windows("/s", runner=lambda s, a: _done(1, err="no server running on /s\n"))
    assert out["state"] == "unknown"
    assert "exit=1" in out["sample"] and "no server" in out["sample"]


def test_tmux_answered_empty_is_verified_zero_and_windows_counted():
    assert read_tmux_windows("/s", runner=lambda s, a: _done(0, ""))["state"] == "verified"
    got = read_tmux_windows("/s", runner=lambda s, a: _done(0, "mishe\tsenses\t1\nmishe\tgenome\t2\n"))
    assert got["state"] == "verified" and got["id"] == TMUX_SENSE_ID
    assert "windows=2 panes=3" in got["sample"]


def test_tmux_malformed_output_is_unknown():
    assert read_tmux_windows("/s", runner=lambda s, a: _done(0, "garbage\n"))["state"] == "unknown"


def test_renderer_unset_root_is_unknown(tmp_path: Path):
    assert read_renderer_coverage(None)["state"] == "unknown"
    assert read_renderer_coverage(tmp_path / "absent")["state"] == "unknown"


def test_renderer_complete_root_verified(tmp_path: Path):
    pkg = tmp_path / "src" / "mishe_tauftauf"
    pkg.mkdir(parents=True)
    (pkg / "plant_view.py").write_text("x = 1\n")
    (pkg / "plant_view_boundary.py").write_text("y = 2\n")
    got = read_renderer_coverage(tmp_path)
    assert got["state"] == "verified" and got["id"] == RENDERER_SENSE_ID


def test_renderer_missing_or_broken_module_is_drift(tmp_path: Path):
    pkg = tmp_path / "src" / "mishe_tauftauf"
    pkg.mkdir(parents=True)
    (pkg / "plant_view.py").write_text("def broken(:\n")
    got = read_renderer_coverage(tmp_path)
    assert got["state"] == "drift"
    assert "plant_view.py=unparseable" in got["sample"]
    assert "plant_view_boundary.py=missing" in got["sample"]


def _wake_unit(unit_root: Path, pythonpath: str) -> None:
    unit_root.mkdir(parents=True, exist_ok=True)
    (unit_root / "mishe-wake@.service").write_text(
        "[Service]\n"
        f"Environment=PYTHONPATH={pythonpath}\n"
        "Environment=MISHE_SEED_HOME=/site\n")


def test_pinned_runtime_root_follows_the_installed_unit(tmp_path):
    from mishe_tauftauf import discovery

    unit_root = tmp_path / "units"
    _wake_unit(unit_root, "/rel/old/src")
    assert discovery._pinned_runtime_root(unit_root) == Path("/rel/old")
    # A re-pin of the unit changes the reading: the installed bytes decide, not a stale file.
    _wake_unit(unit_root, "/rel/new/src")
    assert discovery._pinned_runtime_root(unit_root) == Path("/rel/new")


def test_pinned_runtime_root_ignores_coordination_mode(tmp_path):
    import json
    from mishe_tauftauf import discovery

    unit_root = tmp_path / "units"
    _wake_unit(unit_root, "/rel/new/src")
    (tmp_path / "coordination-mode.json").write_text(json.dumps({"runtime": "/stale/snapshot"}))
    assert discovery._pinned_runtime_root(unit_root) == Path("/rel/new")


def test_pinned_runtime_root_unreadable_or_unpinned_is_none(tmp_path):
    from mishe_tauftauf import discovery

    unit_root = tmp_path / "units"
    assert discovery._pinned_runtime_root(unit_root) is None
    unit_root.mkdir()
    (unit_root / "mishe-wake@.service").write_text("[Service]\nExecStart=/bin/true\n")
    assert discovery._pinned_runtime_root(unit_root) is None
    _wake_unit(unit_root, "/rel/x/lib")  # PYTHONPATH that is not a src root
    assert discovery._pinned_runtime_root(unit_root) is None


from mishe_tauftauf.replacement_readings import (  # noqa: E402
    SESSION_SENSE_ID, judge_session_stability, probe_session_stability,
    resolve_declared_session,
)


def test_session_stability_unknown_when_declared_or_probe_unreadable():
    assert judge_session_stability(None, ["plant"], {"1": "contained:mishe-plants.slice"})["state"] == "unknown"
    assert judge_session_stability("plant", None, {"1": "contained:mishe-plants.slice"})["state"] == "unknown"
    assert judge_session_stability("plant", ["plant"], None)["state"] == "unknown"
    got = judge_session_stability("plant", ["plant"], {"1": None})
    assert got["state"] == "unknown" and "pids=1" in got["sample"]


def test_session_stability_drift_when_declared_session_missing():
    got = judge_session_stability("plant", ["other"], {"2": "contained:mishe-plants.slice"})
    assert got["state"] == "drift" and got["id"] == SESSION_SENSE_ID
    assert "session-missing=plant" in got["sample"]


def test_session_stability_drift_when_server_uncontained():
    got = judge_session_stability("plant", ["plant"], {"3": "uncontained:/user.slice/mishe.slice/run.scope", "4": "contained:mishe-plants.slice"})
    assert got["state"] == "drift" and got["unshielded"] == ["3"]


def test_session_stability_verified_when_present_and_contained():
    got = judge_session_stability("plant", ["plant"], {"3": "contained:mishe-plants.slice"})
    assert got["state"] == "verified" and got["unshielded"] == []


SOCK = "/tmp/tmux-1000/mishe-tauftauf/tmux-1000/default"


def test_probe_broken_tmux_is_unknown_not_calm():
    got = probe_session_stability("plant", SOCK, runner=lambda socket, args: None,
                                  shield_reader=lambda pid: "contained:mishe-plants.slice")
    assert got["state"] == "unknown"


def test_probe_no_server_running_is_drift_with_declared_session():
    def runner(socket, args):
        return _done(1, err="no server running on /tmp/tmux-1000/default\n")
    got = probe_session_stability("plant", SOCK, runner=runner, shield_reader=lambda pid: "contained:mishe-plants.slice")
    assert got["state"] == "drift" and "session-missing=plant" in got["sample"]


def test_probe_other_tmux_error_is_unknown():
    def runner(socket, args):
        return _done(1, err="error connecting to /tmp/tmux-1000/default (Permission denied)\n")
    assert probe_session_stability("plant", SOCK, runner=runner,
                                   shield_reader=lambda pid: "contained:mishe-plants.slice")["state"] == "unknown"


def test_probe_reads_placements_only_for_listed_session_servers():
    read = []

    def runner(socket, args):
        if args[-1] == "#{pid}":
            return _done(0, "11\n12\n")
        return _done(0, "plant\n")

    def shield(pid):
        read.append(pid)
        return "contained:mishe-plants.slice" if pid == "11" else "uncontained:/user.slice/mishe.slice/run.scope"

    got = probe_session_stability("plant", SOCK, runner=runner, shield_reader=shield)
    assert read == ["11", "12"]
    assert got["state"] == "drift" and got["unshielded"] == ["12"]


def test_probe_without_plant_socket_is_unknown_never_default_server():
    def runner(socket, args):
        raise AssertionError("must not probe any server without the plant's socket")
    assert probe_session_stability("plant", None, runner=runner)["state"] == "unknown"


def test_probe_passes_plant_socket_to_every_tmux_call():
    seen = []

    def runner(socket, args):
        seen.append(socket)
        return _done(0, "plant\n") if args[-1] != "#{pid}" else _done(0, "")

    probe_session_stability("plant", SOCK, runner=runner, shield_reader=lambda pid: "contained:mishe-plants.slice")
    assert seen == [SOCK, SOCK]


def test_session_stability_uncontained_is_drift_even_without_declared_session():
    assert judge_session_stability(None, ["plant"], {"5": "uncontained:/user.slice/mishe.slice/run.scope"})["state"] == "drift"
    assert judge_session_stability(None, ["plant"], {"5": "contained:mishe-plants.slice"})["state"] == "unknown"


def test_session_stability_empty_pid_mapping_beside_live_session_is_unknown_not_verified():
    # Reviewer repro: empty/malformed PID output must never establish containment.
    got = judge_session_stability("plant", ["plant"], {})
    assert got["state"] == "unknown" and got["id"] == SESSION_SENSE_ID
    assert judge_session_stability("plant", [], {"1": "contained:mishe-plants.slice"})["state"] == "unknown"


def test_session_stability_both_probes_absent_is_drift_not_unknown():
    got = judge_session_stability("plant", [], {})
    assert got["state"] == "drift" and "session-missing=plant" in got["sample"]


def test_probe_empty_or_junk_pid_output_is_unknown_not_verified():
    def runner_for(pid_out):
        def runner(socket, args):
            if args[-1] == "#{pid}":
                return _done(0, pid_out)
            return _done(0, "plant\n")
        return runner

    for pid_out in ("", "   \n", "garbage\n", "12 junk\n"):
        got = probe_session_stability("plant", SOCK, runner=runner_for(pid_out),
                                      shield_reader=lambda pid: "contained:mishe-plants.slice")
        assert got["state"] == "unknown", (pid_out, got)


def test_probe_server_vanishing_between_probes_is_unknown_not_drift_or_verified():
    def list_then_gone(socket, args):
        if args[-1] == "#{pid}":
            return _done(1, err="no server running on /tmp/tmux-1000/default\n")
        return _done(0, "plant\n")

    def gone_then_pids(socket, args):
        if args[-1] == "#{pid}":
            return _done(0, "11\n")
        return _done(1, err="no server running on /tmp/tmux-1000/default\n")

    contained = lambda pid: "contained:mishe-plants.slice"
    assert probe_session_stability("plant", SOCK, runner=list_then_gone, shield_reader=contained)["state"] == "unknown"
    assert probe_session_stability("plant", SOCK, runner=gone_then_pids, shield_reader=contained)["state"] == "unknown"


def test_resolve_declared_session_env_wins_over_listing():
    assert resolve_declared_session("plant", ["other"]) == "plant"
    assert resolve_declared_session(None, None) is None


def test_resolve_declared_session_names_unique_plant_session():
    assert resolve_declared_session(None, ["plant"]) == "plant"
    assert resolve_declared_session(None, []) is None
    assert resolve_declared_session(None, ["a", "b"]) is None


def test_probe_stability_resolves_declared_from_unique_listing():
    # The producer env carries no MISHE_SEED_SESSION; the plant's own
    # site-namespaced socket names the session the reading judges.
    def runner(socket, args):
        if args[-1] == "#{session_name}":
            return _done(0, "plant\n")
        return _done(0, "123\n")
    got = probe_session_stability(None, SOCK, runner=runner,
                                  shield_reader=lambda pid: "contained:mishe-plants.slice")
    assert got["state"] == "verified" and got["session"] == "plant"


def test_probe_stability_multi_session_listing_stays_unknown():
    def runner(socket, args):
        if args[-1] == "#{session_name}":
            return _done(0, "plant\nother\n")
        return _done(0, "123\n")
    got = probe_session_stability(None, SOCK, runner=runner,
                                  shield_reader=lambda pid: "contained:mishe-plants.slice")
    assert got["state"] == "unknown" and "declared session unreadable" in got["sample"]


def test_role_render_all_render_is_verified():
    from mishe_tauftauf.replacement_readings import read_role_render_coverage
    got = read_role_render_coverage(["genome", "senses"], lambda role: f"# {role}\nbody\n")
    assert got["state"] == "verified"
    assert got["sample"] == "rendered 2 declared roles: genome,senses"


def test_role_render_raise_or_empty_is_drift_with_cause():
    from mishe_tauftauf.replacement_readings import read_role_render_coverage

    def render(role):
        if role == "genome":
            raise ValueError("boom")
        return "  \n" if role == "senses" else "ok\n"

    got = read_role_render_coverage(["genome", "health", "senses"], render)
    assert got["state"] == "drift"
    assert got["missing"] == ["genome=ValueError", "senses=empty"]
    assert "2 of 3 declared roles did not render" in got["sample"]


def test_role_render_unreadable_or_empty_roles_is_unknown():
    from mishe_tauftauf.replacement_readings import read_role_render_coverage
    assert read_role_render_coverage(None, lambda r: "x")["state"] == "unknown"
    assert read_role_render_coverage([], lambda r: "x")["state"] == "unknown"


from mishe_tauftauf.replacement_readings import (  # noqa: E402
    SESSION_RESTARTS_ID, judge_session_restarts, parse_session_births, probe_session_restarts,
)

NOW = 1_000_000.0


def test_session_restarts_no_socket_or_probe_failure_is_unknown():
    assert probe_session_restarts(None, NOW)["state"] == "unknown"
    assert probe_session_restarts(SOCK, NOW, runner=lambda s, a: None)["state"] == "unknown"


def test_session_restarts_no_server_is_unknown_births_not_observable():
    got = probe_session_restarts(SOCK, NOW, runner=lambda s, a: _done(1, err="no server running on /x\n"))
    assert got["state"] == "unknown" and "not observable" in got["sample"]


def test_session_restarts_other_tmux_error_is_unknown():
    got = probe_session_restarts(SOCK, NOW, runner=lambda s, a: _done(1, err="Permission denied\n"))
    assert got["state"] == "unknown"


def test_probe_restarts_resolves_declared_from_unique_listing():
    def runner(socket, args):
        return _done(0, f"plant\t{int(NOW - 60)}\n")
    got = probe_session_restarts(SOCK, NOW, runner=runner)
    assert got["state"] == "verified" and got["session"] == "plant"


def test_probe_restarts_multi_session_listing_stays_unset():
    def runner(socket, args):
        return _done(0, f"a\t{int(NOW - 60)}\nb\t{int(NOW - 90)}\n")
    got = probe_session_restarts(SOCK, NOW, runner=runner)
    assert got["session"] == "unset"


def test_session_restarts_malformed_or_empty_output_is_unknown():
    assert parse_session_births("plant\tnotanumber\n") is None
    assert parse_session_births("plant\n") is None
    assert judge_session_restarts(None, NOW)["state"] == "unknown"
    assert judge_session_restarts([], NOW)["state"] == "unknown"


def test_session_restarts_quiet_window_is_verified_steady():
    old = [("plant", NOW - 3 * 3600)]
    got = judge_session_restarts(old, NOW, declared="plant")
    assert got["state"] == "verified" and got["births"] == 0 and got["verdict"] == "steady"
    assert got["id"] == SESSION_RESTARTS_ID


def test_session_restarts_births_at_or_under_max_stay_verified():
    births = [("a", NOW - 600), ("b", NOW - 1200)]
    got = judge_session_restarts(births, NOW)
    assert got["state"] == "verified" and got["births"] == 2


def test_session_restarts_over_max_is_drift_with_names():
    births = [("a", NOW - 60), ("b", NOW - 120), ("c", NOW - 180)]
    got = judge_session_restarts(births, NOW)
    assert got["state"] == "drift" and got["verdict"] == "churning"
    assert "sessions=a,b,c" in got["sample"]


def test_session_restarts_window_boundary_excludes_older_births():
    edge = [("a", NOW - 3600), ("b", NOW - 3601), ("c", NOW - 3602)]
    assert judge_session_restarts(edge, NOW)["births"] == 1


def test_session_restarts_future_birth_is_unknown_not_calm():
    assert judge_session_restarts([("a", NOW + 3600)], NOW)["state"] == "unknown"


def test_session_restarts_probe_parses_live_listing():
    out = f"plant\t{int(NOW - 60)}\nsenses\t{int(NOW - 90)}\n"
    got = probe_session_restarts(SOCK, NOW, declared="plant", runner=lambda s, a: _done(0, out))
    assert got["state"] == "verified" and got["births"] == 2
    assert got["coverage"].startswith("live sessions only")


from mishe_tauftauf.replacement_readings import (  # noqa: E402
    SERVER_SLICE_ID, judge_server_slice, probe_server_slice,
)

LIFE = "mishe-life-support@1827"
PLANTS = "mishe-self-development-current@2961199"


def test_server_slice_verified_when_each_server_in_its_slice():
    got = judge_server_slice(
        {PLANTS: "mishe-plants.slice", LIFE: "mishe-lifeline.slice"},
        {PLANTS: "contained:mishe-plants.slice", LIFE: "contained:mishe-lifeline.slice"})
    assert got["id"] == SERVER_SLICE_ID and got["state"] == "verified"


def test_server_slice_unreadable_or_absent_input_is_unknown_not_calm():
    assert judge_server_slice(None, {})["state"] == "unknown"
    assert judge_server_slice({}, {})["state"] == "unknown"
    assert judge_server_slice({LIFE: "mishe-lifeline.slice"}, None)["state"] == "unknown"
    got = judge_server_slice({LIFE: "mishe-lifeline.slice"}, {LIFE: None})
    assert got["state"] == "unknown"


def test_server_slice_missing_wrong_or_unbounded_server_is_drift():
    expected = {LIFE: "mishe-lifeline.slice"}
    assert judge_server_slice(expected, {})["state"] == "drift"
    assert judge_server_slice(expected, {LIFE: "contained:mishe-plants.slice"})["state"] == "drift"
    assert judge_server_slice(expected, {LIFE: "unbounded:mishe-lifeline.slice"})["state"] == "drift"
    assert judge_server_slice(expected, {LIFE: "uncontained:/system.slice"})["state"] == "drift"


def test_server_slice_probe_reads_each_pid_through_injected_containment():
    seen = {}

    def fake(pid):
        seen[pid] = True
        return "contained:mishe-lifeline.slice"

    got = probe_server_slice({LIFE: ("1827", "mishe-lifeline.slice")}, containment=fake)
    assert got["state"] == "verified" and seen == {"1827": True}
    assert probe_server_slice({}, containment=fake)["state"] == "unknown"


def test_server_slice_probe_empty_expected_reports_no_declared_servers_not_unreadable():
    # A live server with no declared workload sessions is "nothing to judge",
    # not "containment could not be read" (the reason text must say which).
    def fake(pid):
        raise AssertionError("no pid should be read when nothing is expected")

    got = probe_server_slice({}, containment=fake)
    assert got["state"] == "unknown"
    assert got["sample"] == "no expected servers declared"
    assert probe_server_slice(None, containment=fake)["sample"] == "server containment could not be read"


from mishe_tauftauf.proc_table import expected_from_listing
from mishe_tauftauf.replacement_readings import read_server_slice  # noqa: E402


def test_server_slice_expects_only_declared_workload_sessions():
    got = expected_from_listing("mishe-self-development-current\t1579\nplants\t1938\n")
    assert got == {"mishe-self-development-current@1579": ("1579", "mishe-plants.slice")}
    assert expected_from_listing("mishe-life-support\t1827\n") == {
        "mishe-life-support@1827": ("1827", "mishe-lifeline.slice")}


def test_server_slice_unreadable_listing_is_unknown():
    assert expected_from_listing("garbled line\n") is None
    assert read_server_slice(None)["state"] == "unknown"
    assert read_server_slice(SOCK, runner=lambda s, a: None)["state"] == "unknown"
    assert read_server_slice(SOCK, runner=lambda s, a: _done(1, "", "error connecting"))["state"] == "unknown"
    got = read_server_slice(SOCK, runner=lambda s, a: _done(1, "", "no server running on x"))
    assert got["state"] == "unknown" and "no expected servers" in got["sample"]


def test_server_slice_verified_and_drift_through_socket_listing():
    listing = lambda s, a: _done(0, "mishe-self-development-current\t1579\n")
    ok = read_server_slice(SOCK, runner=listing, containment=lambda pid: "contained:mishe-plants.slice")
    assert ok["id"] == "sense.host.server-slice" and ok["state"] == "verified"
    bad = read_server_slice(SOCK, runner=listing, containment=lambda pid: "uncontained:/user.slice/mishe.slice")
    assert bad["state"] == "drift" and "1579" in bad["sample"]
    unread = read_server_slice(SOCK, runner=listing, containment=lambda pid: None)
    assert unread["state"] == "unknown"


from mishe_tauftauf.replacement_readings import (  # noqa: E402
    judge_server_keeper, read_server_keeper, SERVER_KEEPER_ID,
)

KEEPER = "mishe-self-development-current-session-" + "a" * 32 + ".service"


def test_server_keeper_verified_when_each_server_held_by_own_keeper():
    out = judge_server_keeper({"mishe-self-development-current": ("2961199", KEEPER)})
    assert out["id"] == SERVER_KEEPER_ID
    assert out["state"] == "verified"
    assert "keeper=" + KEEPER in out["sample"]


def test_server_keeper_unreadable_or_absent_is_unknown_not_calm():
    assert judge_server_keeper(None)["state"] == "unknown"
    assert judge_server_keeper({})["state"] == "unknown"
    assert judge_server_keeper({"s": ("1", "")})["state"] == "unknown"


def test_server_keeper_foreign_unit_is_drift():
    test_unit = "mishe-tauftauf-test-1-" + "b" * 32 + "-session-" + "c" * 32 + ".service"
    out = judge_server_keeper({"mishe-self-development-current": ("2961199", test_unit)})
    assert out["state"] == "drift"
    assert "not held by own keeper" in out["sample"]


def test_server_keeper_read_through_socket_listing():
    class Done:
        def __init__(self, rc, out="", err=""):
            self.returncode, self.stdout, self.stderr = rc, out, err
    def runner(sock, args):
        return Done(0, f"mishe-self-development-current\t2961199\n")
    out = read_server_keeper("/tmp/s", runner=runner, cgroup_unit=lambda pid: KEEPER)
    assert out["state"] == "verified"
    out = read_server_keeper("/tmp/s", runner=runner, cgroup_unit=lambda pid: "app.scope")
    assert out["state"] == "drift"
    assert read_server_keeper("/tmp/s", runner=lambda s, a: None)["state"] == "unknown"
    bad = lambda s, a: Done(0, "no tab here\n")
    assert read_server_keeper("/tmp/s", runner=bad)["state"] == "unknown"
    assert read_server_keeper(None)["state"] == "unknown"


from mishe_tauftauf.replacement_readings import DRIFT_SITES_ID, judge_drift_across_sites, read_drift_across_sites
from mishe_tauftauf import unit_properties

OURS = "/home/mesh-home/mishe-tauftauf"
FOREIGN = "/home/mesh-home/android-body"


def _row(declared=(), live=(), readable=True):
    return {"declared": set(declared), "live": set(live), "readable": readable}


def test_single_site_units_verified_and_foreign_only_unit_is_drift():
    ok = judge_drift_across_sites({"senses.service": _row([OURS], [OURS])}, OURS)
    assert ok["state"] == "verified" and ok["id"] == DRIFT_SITES_ID
    foreign = judge_drift_across_sites({"light.service": _row([FOREIGN], [FOREIGN]),
                                        "senses.service": _row([OURS], [OURS])}, OURS)
    assert foreign["state"] == "drift" and "light.service foreign-site" in foreign["sample"]


def test_unit_declaring_one_site_and_running_another_is_drift():
    row = judge_drift_across_sites({"life.service": _row([FOREIGN], [OURS])}, OURS)
    assert row["state"] == "drift" and "spans sites" in row["sample"]


def test_unreadable_or_absent_input_is_unknown_never_calm():
    assert judge_drift_across_sites(None, OURS)["state"] == "unknown"
    assert judge_drift_across_sites({}, OURS)["state"] == "unknown"
    assert judge_drift_across_sites({"x.service": _row(readable=False)}, OURS)["state"] == "unknown"
    assert judge_drift_across_sites({"x.service": _row()}, OURS)["state"] == "unknown"  # no site named
    mixed = judge_drift_across_sites({"x.service": _row(readable=False),
                                      "y.service": _row([FOREIGN], [FOREIGN])}, OURS)
    assert mixed["state"] == "drift"  # a drift fact outranks an unreadable neighbour


def test_site_home_and_cmdline_decoding_keeps_only_homes():
    assert unit_properties.site_home(f"{FOREIGN}/.mishe-tauftauf/body/x.py") == FOREIGN
    assert unit_properties.site_home("/usr/bin/python3") is None
    raw = b"python3\0/home/mesh-home/android-body/.mishe-tauftauf/a.py\0--token\0SECRET\0"
    assert unit_properties.cmdline_site_homes(raw) == {FOREIGN}
    assert unit_properties.mishe_slice_unit(
        "/user.slice/user@1000.service/mishe.slice/mishe-plants.slice/x.service") == "x.service"
    assert unit_properties.mishe_slice_unit("/user.slice/user@1000.service/app.slice/y.service") is None


def test_probe_groups_processes_by_mishe_slice_unit(tmp_path):
    cgroups = {}

    def proc(pid, cgroup, cmdline, cwd):
        d = tmp_path / pid
        d.mkdir()
        cgroups[pid] = cgroup
        (d / "cmdline").write_bytes(cmdline)
        (d / "cwd").symlink_to(cwd)
    proc("100", "/mishe.slice/mishe-plants.slice/senses.service", b"python3\0-m\0x\0", OURS)
    proc("101", "/mishe.slice/mishe-plants.slice/light.service", b"python3\0/home/mesh-home/android-body/.mishe-tauftauf/l.py\0", "/")
    declared = {"senses.service": f"ExecStart={{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 {OURS}/.mishe-tauftauf/s.py }}",
                "light.service": f"ExecStart={{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 {FOREIGN}/.mishe-tauftauf/l.py }}"}
    result = read_drift_across_sites(OURS, proc_root=tmp_path, declared=lambda unit: declared.get(unit),
                                     cgroup_of=lambda pid: cgroups.get(pid))
    assert result["state"] == "drift" and "light.service" in result["sample"]
    assert "--token" not in result["sample"] and "SECRET" not in result["sample"]
    gone = read_drift_across_sites(OURS, proc_root=tmp_path, declared=lambda unit: None,
                                   cgroup_of=lambda pid: cgroups.get(pid))
    assert gone["state"] == "unknown"
