"""Stage bundled native sources beside caller-provisioned OMP dependencies."""

from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator


@contextmanager
def native_worker_command(
    *, bun: Path, node_modules: Path, staging_parent: Path
) -> Iterator[list[str]]:
    """Yield a worker command for use inside a nested NativeSession lifetime.

    The caller supplies trusted, pinned Bun and OMP dependencies and must keep
    them unchanged for the entire session. Nothing is installed or downloaded.
    staging_parent must be an existing owned directory. Source staging is removed
    on exit; close/reap the NativeSession before leaving this context.
    """
    bun = bun.resolve(strict=True)
    node_modules = node_modules.resolve(strict=True)
    staging_parent = staging_parent.resolve(strict=True)
    if not bun.is_file():
        raise ValueError("bun must name an executable file")
    if not node_modules.is_dir() or not staging_parent.is_dir():
        raise ValueError("node_modules and staging_parent must be directories")
    source = files("mishe_tauftauf").joinpath("native")
    with TemporaryDirectory(prefix="mishe-native-", dir=staging_parent) as tmp:
        stage = Path(tmp)
        for name in ("native-loop-worker.ts", "native-session.ts", "native-bootstrap.ts",
                     "native-checkpoint.ts"):
            (stage / name).write_bytes(source.joinpath(name).read_bytes())
        (stage / "node_modules").symlink_to(node_modules, target_is_directory=True)
        yield [str(bun), "--no-install", "--no-env-file", str(stage / "native-loop-worker.ts")]
