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
        return 1, 0

    monkeypatch.setattr(cam, "_capture_webcam", fake)
    result = cam.run("webcam", None, "/dev/video0", None)
    assert result["status"] == "ok" and "frame" not in result
    assert not seen[0].exists() and not seen[0].parent.exists()


@pytest.fixture(autouse=True)
def _no_retry_pause(monkeypatch):
    monkeypatch.setattr(cam, "WEBCAM_RETRY_PAUSE_S", 0)


def test_fswebcam_exit_zero_without_frame_is_retried_then_unavailable(tmp_path, monkeypatch):
    calls = []

    def silent(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(cam.subprocess, "run", silent)
    monkeypatch.setattr(cam.shutil, "which", lambda name: f"/usr/bin/{name}")  # host-independent: no ffmpeg needed
    out = cam.run("webcam", None, "/dev/video0", None)
    assert out["status"] == "unavailable" and "grid" not in out
    assert "wrote no frame" in out["reason"]
    assert len(calls) == cam.WEBCAM_ATTEMPTS


def test_fswebcam_second_attempt_frame_is_used(tmp_path, monkeypatch):
    attempts = []

    def flaky(args, **kwargs):
        if args[0] != "fswebcam":
            return subprocess.CompletedProcess(args, 1, stdout=b"", stderr=b"")  # signature step: fails like a bad frame
        attempts.append(args)
        if len(attempts) == 1:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        Path(args[-1]).write_bytes(b"\xff\xd8 not a real jpeg")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(cam.subprocess, "run", flaky)
    monkeypatch.setattr(cam.shutil, "which", lambda name: f"/usr/bin/{name}")  # host-independent: no ffmpeg needed
    out = cam.run("webcam", None, "/dev/video0", None)
    assert len(attempts) == 2
    # the bytes are not a decodable image, so the signature step reports unavailable, not a fake grid
    assert out["status"] == "unavailable" and "grid" not in out


def test_fswebcam_busy_device_then_frame_is_used(tmp_path, monkeypatch):
    """Observed 2026-11-07: a busy V4L2 input exits 0 with no frame; a later attempt after the pause succeeds."""
    attempts, pauses = [], []

    def busy_then_ok(args, **kwargs):
        if args[0] != "fswebcam":
            return subprocess.CompletedProcess(args, 1, stdout=b"", stderr=b"")  # signature step: fails like a bad frame
        attempts.append(args)
        if len(attempts) < 3:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="VIDIOC_S_INPUT: Device or resource busy")
        Path(args[-1]).write_bytes(b"\xff\xd8 not a real jpeg")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(cam, "WEBCAM_RETRY_PAUSE_S", 2.5)
    monkeypatch.setattr(cam.time, "sleep", lambda s: pauses.append(s))
    monkeypatch.setattr(cam.subprocess, "run", busy_then_ok)
    monkeypatch.setattr(cam.shutil, "which", lambda name: f"/usr/bin/{name}")
    out = cam.run("webcam", None, "/dev/video0", None)
    assert len(attempts) == 3 and pauses == [2.5, 2.5]
    assert out["status"] == "unavailable" and "grid" not in out  # frame bytes not decodable; no invented grid


def test_busy_then_frame_record_counts_attempts(tmp_path, monkeypatch):
    """Observed 2026-11-07/08: a retried busy input must be visible in the record, since success is otherwise silent."""
    attempts = []

    def busy_then_ok(args, **kwargs):
        if args[0] != "fswebcam":
            return subprocess.CompletedProcess(args, 1, stdout=b"", stderr=b"")
        attempts.append(args)
        if len(attempts) < 3:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="VIDIOC_S_INPUT: Device or resource busy")
        Path(args[-1]).write_bytes(b"\xff\xd8 frame")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(cam.subprocess, "run", busy_then_ok)
    monkeypatch.setattr(cam.time, "sleep", lambda s: None)
    monkeypatch.setattr(cam.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cam, "signature", lambda image: {"grid": [0.0]})
    out = cam.run("webcam", None, "/dev/video0", None)
    assert out["status"] == "ok"
    assert out["capture_attempts"] == 3 and out["busy_attempts"] == 2


def test_clean_first_attempt_record_has_no_busy(tmp_path, monkeypatch):
    def clean(args, **kwargs):
        if args[0] != "fswebcam":
            return subprocess.CompletedProcess(args, 1, stdout=b"", stderr=b"")
        Path(args[-1]).write_bytes(b"\xff\xd8 frame")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(cam.subprocess, "run", clean)
    monkeypatch.setattr(cam.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(cam, "signature", lambda image: {"grid": [0.0]})
    out = cam.run("webcam", None, "/dev/video0", None)
    assert out["capture_attempts"] == 1 and out["busy_attempts"] == 0
