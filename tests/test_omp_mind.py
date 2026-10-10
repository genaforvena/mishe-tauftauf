from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


class OmpMindTests(unittest.TestCase):
    def test_configured_model_reaches_one_shot_invocation(self):
        adapter = Path(__file__).resolve().parents[1] / "src/mishe_tauftauf/omp_mind.py"
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            omp = tmp / "omp"
            args_file = tmp / "args.json"
            omp.write_text(
                "#!/usr/bin/env python3\n"
                "import json, pathlib, sys\n"
                f"pathlib.Path({str(args_file)!r}).write_text(json.dumps(sys.argv[1:]))\n",
                encoding="utf-8",
            )
            omp.chmod(0o755)
            env = os.environ.copy()
            env.update({
                "PATH": str(tmp) + os.pathsep + env.get("PATH", ""),
                "MISHE_TAUFTAUF_WORKSPACE": str(tmp),
                "MISHE_TAUFTAUF_MODEL": "opencode-go/longcat-2.5-preview-free",
            })
            result = subprocess.run(
                [sys.executable, str(adapter)],
                input="bounded task\n",
                text=True,
                capture_output=True,
                env=env,
                timeout=15,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            args = json.loads(args_file.read_text(encoding="utf-8"))
            self.assertEqual(args.count("--model"), 1)
            self.assertEqual(args[args.index("--model") + 1], "opencode-go/longcat-2.5-preview-free")
            self.assertTrue({"--print", "--no-session", "--no-extensions", "--cwd"}.issubset(args))

    def test_hung_omp_child_returns_timeout_status(self):
        adapter = Path(__file__).resolve().parents[1] / "src/mishe_tauftauf/omp_mind.py"
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            omp = tmp / "omp"
            omp.write_text(
                "#!/usr/bin/env python3\n"
                "import time\n"
                "time.sleep(5)\n",
                encoding="utf-8",
            )
            omp.chmod(0o755)
            env = os.environ.copy()
            env.update({
                "PATH": str(tmp) + os.pathsep + env.get("PATH", ""),
                "MISHE_TAUFTAUF_WORKSPACE": str(tmp),
                "MISHE_TAUFTAUF_MAX_SECONDS": "0.1",
            })

            start = time.monotonic()
            result = subprocess.run(
                [sys.executable, str(adapter)],
                input="bounded task\n",
                text=True,
                capture_output=True,
                env=env,
                timeout=5,
            )

            self.assertEqual(result.returncode, 124, result.stderr)
            self.assertLess(time.monotonic() - start, 1.5)

    def test_invalid_timeout_fails_closed_before_launch(self):
        adapter = Path(__file__).resolve().parents[1] / "src/mishe_tauftauf/omp_mind.py"
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            marker = tmp / "launched"
            omp = tmp / "omp"
            omp.write_text(
                "#!/usr/bin/env python3\n"
                f"from pathlib import Path\nPath({str(marker)!r}).touch()\n",
                encoding="utf-8",
            )
            omp.chmod(0o755)
            env = os.environ.copy()
            env.update({
                "PATH": str(tmp) + os.pathsep + env.get("PATH", ""),
                "MISHE_TAUFTAUF_WORKSPACE": str(tmp),
                "MISHE_TAUFTAUF_MAX_SECONDS": "nan",
            })

            result = subprocess.run(
                [sys.executable, str(adapter)],
                input="bounded task\n",
                text=True,
                capture_output=True,
                env=env,
                timeout=5,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("finite positive", result.stderr)
            self.assertFalse(marker.exists())

    def test_timeout_stops_omp_descendants_and_removes_prompt(self):
        adapter = Path(__file__).resolve().parents[1] / "src/mishe_tauftauf/omp_mind.py"
        with tempfile.TemporaryDirectory() as directory:
            tmp = Path(directory)
            pid_file = tmp / "child.pid"
            omp = tmp / "omp"
            omp.write_text(
                "#!/usr/bin/env python3\n"
                "import os, pathlib, subprocess, sys, time\n"
                "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], "
                "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
                "pathlib.Path(os.environ['MISHE_TEST_CHILD_PID']).write_text(str(child.pid))\n"
                "time.sleep(30)\n",
                encoding="utf-8",
            )
            omp.chmod(0o755)
            env = os.environ.copy()
            env.update({
                "PATH": str(tmp) + os.pathsep + env.get("PATH", ""),
                "TMPDIR": str(tmp),
                "MISHE_TAUFTAUF_WORKSPACE": str(tmp),
                # Allow interpreter startup on a saturated host before killing descendants.
                "MISHE_TAUFTAUF_MAX_SECONDS": "2",
                "MISHE_TEST_CHILD_PID": str(pid_file),
            })
            try:
                result = subprocess.run([sys.executable, str(adapter)], input="bounded task\n",
                                        text=True, capture_output=True, env=env, timeout=8)
                self.assertEqual(result.returncode, 124, result.stderr)
                child_pid = int(pid_file.read_text())
                time.sleep(0.1)
                stat = Path(f"/proc/{child_pid}/stat")
                self.assertFalse(stat.exists() and stat.read_text().split()[2] != "Z")
                self.assertEqual(list(tmp.glob("mishe-tauftauf-omp-*")), [])
            finally:
                if pid_file.exists():
                    child_pid = int(pid_file.read_text())
                    stat = Path(f"/proc/{child_pid}/stat")
                    if stat.exists() and stat.read_text().split()[2] != "Z":
                        os.kill(child_pid, signal.SIGKILL)



if __name__ == "__main__":
    unittest.main()
