from datetime import datetime, timezone, timedelta
import json


def setup_wall(home):
    (home / "coordination-mode.json").write_text(json.dumps({"mode": "wall", "until": (datetime.now(timezone.utc)+timedelta(hours=10)).isoformat()}))


def _skeleton_line(pane_text):
    lines = [line for line in pane_text.splitlines() if line.startswith("WALL SKELETON:")]
    assert len(lines) == 1, pane_text
    return lines[0]


def test_wall_skeleton_reports_all_three_common_headings(tmp_path):
    setup_wall(tmp_path)
    from mishe_tauftauf import wall

    (tmp_path / "walls").mkdir(exist_ok=True)
    (tmp_path / "walls" / "probe.md").write_text(
        "# probe\n\n## Plate\n- x\n\n## Blockers\n- none\n\n## Next action\n- y\n"
    )
    assert _skeleton_line(wall.mind_block(tmp_path, "probe")) == "WALL SKELETON: plate=yes blockers=yes next=yes"


def test_wall_skeleton_names_missing_headings_without_hiding_them(tmp_path):
    setup_wall(tmp_path)
    from mishe_tauftauf import wall

    (tmp_path / "walls").mkdir(exist_ok=True)
    (tmp_path / "walls" / "probe.md").write_text("# probe\n\n## Next action\n- y\n")
    assert _skeleton_line(wall.mind_block(tmp_path, "probe")) == "WALL SKELETON: plate=no blockers=no next=yes"


def test_wall_skeleton_accepts_singular_blocker_and_any_case(tmp_path):
    setup_wall(tmp_path)
    from mishe_tauftauf import wall

    (tmp_path / "walls").mkdir(exist_ok=True)
    (tmp_path / "walls" / "probe.md").write_text("### PLATE\n\n## blocker\n\n# Next\n")
    assert _skeleton_line(wall.mind_block(tmp_path, "probe")) == "WALL SKELETON: plate=yes blockers=yes next=yes"


def test_wall_skeleton_is_unknown_without_a_wall_file(tmp_path):
    setup_wall(tmp_path)
    from mishe_tauftauf import wall

    assert _skeleton_line(wall.mind_block(tmp_path, "absent")) == "WALL SKELETON: UNKNOWN (no file)"


def test_wall_skeleton_does_not_match_body_text_that_is_not_a_heading(tmp_path):
    setup_wall(tmp_path)
    from mishe_tauftauf import wall

    (tmp_path / "walls").mkdir(exist_ok=True)
    (tmp_path / "walls" / "probe.md").write_text("The plate is full; next we blockers.\n- next step\n")
    assert _skeleton_line(wall.mind_block(tmp_path, "probe")) == "WALL SKELETON: plate=no blockers=no next=no"
