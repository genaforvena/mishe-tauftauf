"""Pure parts of the household sensing machinery (no house data, no devices)."""
import importlib.util
import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"


def load(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_word_match_and_hint_variants():
    cal = load("hint_calibration")
    assert cal.word_match("Миша, это проверка", "Миша - это проверка.") == 1.0
    assert cal.word_match("Мишка, где сейчас Бруно?", "Мишка здесь сейчас в ровно") < 0.7
    assert cal.hint_ok("Миша, стоб", ["миша", "мише", "мишка"])
    assert not cal.hint_ok("Маша, стоп", ["миша", "мише", "мишка"])
    assert cal.stop_ok("Миша, стоб", ["стоп", "хватит"])
    assert not cal.stop_ok("Миша, это дом", ["стоп", "хватит"])
    # a stop heard by mistake ("стол") only makes Mishe stop listening: the safe direction
    assert cal.stop_ok("Миша, это стол", ["стоп", "хватит"])
    assert not cal.hint_ok("", ["миша"])


def test_edit_distance():
    cal = load("hint_calibration")
    assert cal.edit_distance("стоп", "стоб") == 1
    assert cal.wer("a b c d", "a x c d") == 0.25
    assert cal.edit_distance("", "abc") == 3


def test_unavailable_tools_are_reported_not_invented(tmp_path):
    cal = load("hint_calibration")
    row = cal.run_phrase(str(tmp_path / "nope"), "v", "w", "m", "p", "x", ["x"])
    assert row["status"] == "unavailable" and "heard" not in row


def test_household_view_reads_rooms_from_site_config(tmp_path, monkeypatch):
    site = tmp_path / "site"
    (site / "spatial/work/household").mkdir(parents=True)
    (site / "spatial/work/household/rooms.json").write_text(json.dumps(
        [{"name": "kitchen", "source": "webcam", "every": 30}]))
    monkeypatch.setenv("MISHE_TAUFTAUF_HOME", str(site))
    view = load("household_view")
    assert list(view.CAMERAS) == ["kitchen"] and view.CAMERAS["kitchen"]["every"] == 30
    assert view.spark([]) == "(no samples yet)"
    assert len(view.ascii_grid([0] * 48, 8, 6)) == 6


def test_sound_panel_is_honest_about_missing_and_stale_audio(tmp_path, monkeypatch):
    site = tmp_path / "site"
    (site / "spatial/work/household").mkdir(parents=True)
    monkeypatch.setenv("MISHE_TAUFTAUF_HOME", str(site))
    view = load("household_view")
    assert "UNKNOWN" in view.sound_lines(1000.0)[0]
    log = site / "spatial/work/household/audio.jsonl"
    log.write_text(json.dumps({"room": "r", "source": "m", "rms": 500, "status": "NO_SPEECH", "t": 100.0}) + "\n"
                   + json.dumps({"room": "r", "source": "m", "rms": 900, "status": "HINT", "t": 990.0,
                                 "text": "hello"}) + "\n")
    lines = "\n".join(view.sound_lines(1000.0))
    assert "HINT" in lines and "hello" in lines and "STALE" not in lines
    assert "STALE" in "\n".join(view.sound_lines(5000.0))
