"""Camera signature: derived numbers only, honest failure, raw frame removed."""
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "camera_signature", Path(__file__).resolve().parents[1] / "tools" / "camera_signature.py")
cam = importlib.util.module_from_spec(SPEC)
sys.modules["camera_signature"] = cam
SPEC.loader.exec_module(cam)

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg missing")


def _frame(path: Path, color: str) -> Path:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c={color}:s=64x48:d=1", "-frames:v", "1", str(path)], check=True)
    return path


@needs_ffmpeg
def test_dark_and_bright_frames_differ(tmp_path):
    dark = cam.run("file", None, "", _frame(tmp_path / "d.png", "black"))
    bright = cam.run("file", None, "", _frame(tmp_path / "b.png", "white"))
    assert dark["status"] == bright["status"] == "ok"
    assert len(dark["grid"]) == cam.GRID_W * cam.GRID_H
    assert dark["mean_luma"] < 20 < 230 < bright["mean_luma"]
    assert cam.grid_distance(dark["grid"], bright["grid"]) > 150
    assert cam.grid_distance(dark["grid"], dark["grid"]) == 0


def test_missing_input_is_unavailable_not_invented(tmp_path):
    out = cam.run("file", None, "", tmp_path / "absent.jpg")
    assert out["status"] == "unavailable" and "grid" not in out
    assert cam.run("mac", None, "", None)["status"] == "unavailable"


@needs_ffmpeg
def test_capture_temp_frame_is_deleted(tmp_path, monkeypatch):
    seen = []

    def fake(out, device):
        seen.append(out)
        _frame(out.with_suffix(".png"), "gray").rename(out)

    monkeypatch.setattr(cam, "_capture_webcam", fake)
    result = cam.run("webcam", None, "/dev/video0", None)
    assert result["status"] == "ok" and "frame" not in result
    assert not seen[0].exists() and not seen[0].parent.exists()
