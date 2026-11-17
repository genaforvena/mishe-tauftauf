"""The scoped step-7 verify must never call a tree clean without comparing content.

`tools/root-migration-verify-scoped.sh` carried a hole found on 2026-11-17 while
reviewing the root-drive flip: the byte cap broke out of the sampling loop *before*
writing the first sampled path, so a first sampled file larger than the cap left a
zero-file sample, `nsampled=0` read as clean, and a size+mtime-preserving
corruption of that file passed as VERIFIED. The tests below run the real script on
synthetic pairs and pin both the regression and the shape's three claims.
"""
import os
import shutil
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "tools" / "root-migration-verify-scoped.sh"
SINCE = "2026-11-17T08:00:00Z"
OLD = "2026-11-17 07:00:00Z"


def pair(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    for tree in (src, dst):
        (tree / "t").mkdir(parents=True)
    return src, dst


def put(root, name, payload, *, size=None):
    path = root / "t" / name
    path.write_bytes(payload if size is None else payload[:1] * size)
    subprocess.run(["touch", "-d", OLD, str(path)], check=True)
    return path


def verify(tmp_path, src, dst, *args, work=None):
    work = work if work is not None else tmp_path / "work"
    env = {**os.environ, "MIGRATION_SRC": str(src), "MIGRATION_DST": str(dst),
           "MIGRATION_WORK": str(work)}
    return subprocess.run(["bash", str(SCRIPT), "--since", SINCE, *args, "t"],
                          env=env, capture_output=True, text=True)


def test_clean_pair_verifies(tmp_path):
    src, dst = pair(tmp_path)
    put(src, "payload.bin", b"A", size=200)
    put(src, "small.txt", b"C", size=50)
    shutil.copy2(src / "t" / "payload.bin", dst / "t" / "payload.bin")
    shutil.copy2(src / "t" / "small.txt", dst / "t" / "small.txt")
    # A real `rsync -a` copy preserves directory mtimes too. Copying files into an
    # existing directory bumps that directory's mtime, so without this the size+mtime
    # clause fails whenever the copy straddles a second boundary (CI 2026-11-17).
    shutil.copystat(src / "t", dst / "t")
    shutil.copystat(src, dst)

    done = verify(tmp_path, src, dst)

    assert done.returncode == 0, done.stdout + done.stderr
    assert "VERIFIED t" in done.stdout


def test_size_and_mtime_preserving_corruption_is_caught_when_the_sample_would_be_empty(tmp_path):
    """Regression: the first sampled file exceeds the cap; the sample must still exist."""
    src, dst = pair(tmp_path)
    put(src, "payload.bin", b"A", size=200)   # sorted first, larger than the cap
    put(src, "small.txt", b"C", size=50)
    put(dst, "payload.bin", b"B", size=200)   # same size, same mtime, different bytes
    shutil.copy2(src / "t" / "small.txt", dst / "t" / "small.txt")

    done = verify(tmp_path, src, dst, "--sample-bytes", "100")

    assert done.returncode == 1, done.stdout + done.stderr
    assert "NOT-CLEAN t" in done.stdout
    assert "payload.bin" in done.stdout
    sample = tmp_path / "work" / "verify-t.sample"
    assert sample.read_text().strip(), "the sample must not be empty while unchanged files exist"


def test_corruption_in_the_changed_set_is_caught(tmp_path):
    src, dst = pair(tmp_path)
    put(src, "newer.bin", b"A", size=200)
    put(dst, "newer.bin", b"B", size=200)
    newer = "2026-11-17 08:30:00Z"
    for root in (src, dst):
        subprocess.run(["touch", "-d", newer, str(root / "t" / "newer.bin")], check=True)

    done = verify(tmp_path, src, dst)

    assert done.returncode == 1, done.stdout + done.stderr
    assert "NOT-CLEAN t" in done.stdout
    assert "newer.bin" in done.stdout


def test_an_extra_destination_file_is_not_clean(tmp_path):
    src, dst = pair(tmp_path)
    put(src, "only-here.bin", b"A", size=200)
    shutil.copy2(src / "t" / "only-here.bin", dst / "t" / "only-here.bin")
    put(dst, "extra.txt", b"E", size=10)

    done = verify(tmp_path, src, dst)

    assert done.returncode == 1, done.stdout + done.stderr
    assert "NOT-CLEAN t" in done.stdout


def test_an_unusable_work_directory_fails_closed(tmp_path):
    """The evidence files cannot be written: exit 2, never a verdict.

    Until 2026-11-17 every write into a missing MIGRATION_WORK failed, the
    comparisons ran on empty strings and the tree still printed VERIFIED.
    """
    src, dst = pair(tmp_path)
    put(src, "payload.bin", b"A", size=200)
    shutil.copy2(src / "t" / "payload.bin", dst / "t" / "payload.bin")
    readonly = tmp_path / "readonly"
    readonly.mkdir()
    readonly.chmod(0o500)
    try:
        done = verify(tmp_path, src, dst, work=readonly / "work")
    finally:
        readonly.chmod(0o700)

    assert done.returncode == 2, done.stdout + done.stderr
    assert "VERIFIED" not in done.stdout
    assert "cannot create work dir" in done.stderr


def test_a_missing_work_directory_is_created(tmp_path):
    src, dst = pair(tmp_path)
    put(src, "payload.bin", b"A", size=200)
    shutil.copy2(src / "t" / "payload.bin", dst / "t" / "payload.bin")
    work = tmp_path / "fresh" / "work"

    done = verify(tmp_path, src, dst, work=work)

    assert done.returncode == 0, done.stdout + done.stderr
    assert "VERIFIED t" in done.stdout
    assert (work / "verify-t.sample").is_file()
