"""Capture one camera frame and keep only a coarse scene signature.

The raw frame is written to a private temporary directory and deleted before the
program returns; only derived numbers leave this module: mean brightness and an
8x6 grid of brightness cells. That is enough to tell rooms and lighting apart
and to line up with a spatial label, and not enough to recognise a person.

Sources:
  webcam  a local V4L2 camera through fswebcam (default /dev/video0)
  mac     a macOS camera over ssh, using tools/mac_camera_snap.swift
          (macOS 10.13 has no imagesnap or ffmpeg; swiftc is enough)

Usage:
  python tools/camera_signature.py webcam [DEVICE]
  python tools/camera_signature.py mac USER@HOST
  python tools/camera_signature.py file FRAME.jpg     (derive only; keeps the file)

One JSON object per run goes to stdout. A failed capture prints a JSON object
with "status": "unavailable" and a reason; it never reports a fabricated
signature. An unreachable Mac also carries "reason_code": "mac_unreachable".
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

GRID_W = 8
GRID_H = 6
SWIFT_SOURCE = Path(__file__).with_name("mac_camera_snap.swift")


def signature(frame: Path) -> dict:
    """Brightness signature of an image file, using ffmpeg to downscale."""
    proc = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-i", str(frame),
         "-vf", f"scale={GRID_W}:{GRID_H}:flags=area,format=gray",
         "-frames:v", "1", "-f", "rawvideo", "-"],
        capture_output=True, timeout=30)
    cells = proc.stdout
    if proc.returncode != 0 or len(cells) != GRID_W * GRID_H:
        raise RuntimeError(f"ffmpeg could not derive a {GRID_W}x{GRID_H} grid "
                           f"(rc={proc.returncode}, bytes={len(cells)})")
    grid = list(cells)
    return {"grid": grid, "grid_w": GRID_W, "grid_h": GRID_H,
            "mean_luma": round(sum(grid) / len(grid), 2)}


def grid_distance(a: list[int], b: list[int]) -> float:
    """Mean absolute brightness difference between two signatures (0-255)."""
    if len(a) != len(b) or not a:
        raise ValueError("signatures differ in size")
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


WEBCAM_ATTEMPTS = 3
# Observed 2026-11-07: fswebcam exits 0 with "VIDIOC_S_INPUT: Device or resource busy"
# when the V4L2 device is still held just after a previous capture. A short pause lets it
# release; retries are bounded so a stuck device still reports unavailable.
WEBCAM_RETRY_PAUSE_S = 3.0


def _capture_webcam(out: Path, device: str, telemetry: dict) -> None:
    """One frame from fswebcam. fswebcam can exit 0 without writing a file
    (observed 1 in 30 on /dev/video0), so the file is checked, not the exit code.
    A nonzero exit is treated like a missing frame: check=False keeps the busy
    stderr count and the retry reachable. A missing frame is retried after a short
    pause before being reported as unavailable.
    telemetry is updated in place after every attempt (capture_attempts,
    busy_attempts), so a failed capture still carries its counts in the record."""
    last = ""
    busy = 0
    for attempt in range(WEBCAM_ATTEMPTS):
        if attempt:
            time.sleep(WEBCAM_RETRY_PAUSE_S)
        run = subprocess.run(["fswebcam", "-d", device, "-q", "-r", "640x480",
                              "--no-banner", "-S", "15", str(out)],
                             check=False, capture_output=True, text=True, timeout=60)
        stderr = run.stderr or ""
        if "Device or resource busy" in stderr:
            busy += 1
        telemetry.update(capture_attempts=attempt + 1, busy_attempts=busy)
        if out.is_file() and out.stat().st_size > 0:
            return
        last = stderr.strip()[-160:] or f"fswebcam exit {run.returncode}"
    if busy == WEBCAM_ATTEMPTS:
        # Every attempt found the input held: the input is missing, not a failed capture.
        raise DeviceBusy(f"{out.name}: V4L2 input busy on all {WEBCAM_ATTEMPTS} attempts: {last}")
    raise RuntimeError(f"fswebcam wrote no frame to {out.name} after {WEBCAM_ATTEMPTS} attempts"
                       + (f": {last}" if last else ""))


class DeviceUnreachable(RuntimeError):
    """The Mac did not answer: a failed or timed-out ssh/scp step, or ssh's own exit 255."""


class DeviceBusy(RuntimeError):
    """The local webcam input stayed held by another process across every bounded attempt."""


def _capture_mac(out: Path, host: str) -> None:
    if not SWIFT_SOURCE.is_file():
        raise RuntimeError(f"missing {SWIFT_SOURCE}")
    ssh = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", host]
    remote = f"/tmp/mishe-cam-{int(time.time())}"
    try:
        try:
            subprocess.run(["scp", "-q", "-o", "BatchMode=yes", str(SWIFT_SOURCE),
                            f"{host}:{remote}.swift"], check=True, timeout=30)
            run = subprocess.run(
                [*ssh, f"cd /tmp && swiftc {remote}.swift -o {remote} && {remote} {remote}.jpg"],
                capture_output=True, text=True, timeout=180)
            if run.returncode == 255:  # ssh's own failure code: no connection to the Mac
                raise DeviceUnreachable("ssh exited 255")
            if run.returncode != 0:
                raise RuntimeError(f"remote capture failed rc={run.returncode}")
            subprocess.run(["scp", "-q", "-o", "BatchMode=yes", f"{host}:{remote}.jpg", str(out)],
                           check=True, timeout=60)
        except subprocess.SubprocessError as exc:
            raise DeviceUnreachable(f"{type(exc).__name__}: {exc}") from exc
    finally:
        try:  # best-effort cleanup; a timed-out rm must not mask the capture's own failure code
            subprocess.run([*ssh, f"rm -f {remote} {remote}.swift {remote}.jpg"],
                           capture_output=True, timeout=30)
        except subprocess.SubprocessError:
            pass


def run(source: str, host: str | None, device: str, frame: Path | None) -> dict:
    record: dict = {"captured_at_epoch_s": round(time.time(), 1), "source": source}
    if shutil.which("ffmpeg") is None:
        return {**record, "status": "unavailable", "reason": "ffmpeg missing"}
    tmp = Path(tempfile.mkdtemp(prefix="camsig-"))
    capture: dict = {}  # filled by the capture step; kept on failure so the record shows what was tried
    try:
        if source == "file":
            if frame is None:
                return {**record, "status": "unavailable", "reason": "no frame given"}
            image = frame
        else:
            image = tmp / "frame.jpg"
            if source == "webcam":
                _capture_webcam(image, device, capture)
            elif source == "mac":
                if not host:
                    return {**record, "status": "unavailable", "reason": "mac needs USER@HOST"}
                _capture_mac(image, host)
        return {**record, "status": "ok", **capture, **signature(image)}
    except DeviceUnreachable as exc:
        return {**record, **capture, "status": "unavailable", "reason_code": "mac_unreachable",
                "reason": f"{type(exc).__name__}: {exc}"[:300]}
    except DeviceBusy as exc:
        return {**record, **capture, "status": "unavailable", "reason_code": "device_busy",
                "reason": f"{type(exc).__name__}: {exc}"[:300]}
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        return {**record, **capture, "status": "unavailable",
                "reason": f"{type(exc).__name__}: {exc}"[:300]}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str]) -> int:
    """Fixed positions: SOURCE [TARGET]; TARGET is a host, a device or a file."""
    source = argv[0] if argv else ""
    target = argv[1] if len(argv) > 1 else None
    if source not in ("webcam", "mac", "file"):
        print(json.dumps({"status": "unavailable", "reason": "usage: SOURCE [TARGET]"}))
        return 2
    result = run(source,
                 target if source == "mac" else None,
                 target if source == "webcam" and target else "/dev/video0",
                 Path(target) if source == "file" and target else None)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
