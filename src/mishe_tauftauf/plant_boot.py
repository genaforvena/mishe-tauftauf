"""Owned plant boot presentation and explicit boot-receipt startup adapter."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time

from . import duties, producers, roles, units
from .plant_tmux import setup
from .state_files import read_json, write_json

def presentation(home):
    config = read_json(Path(home) / 'presentation.json')
    if not config or set(config['active']) != roles.declared(home):
        raise ValueError('presentation active areas must match the local charters')
    if set(config['active']) & set(config['resting']):
        raise ValueError('resting projects cannot be active areas')
    return config


def layout(home, artifact, *, outer, inner, command=None, inner_setup=True):
    home, artifact = Path(home), Path(artifact)
    config = presentation(home)
    env = {k: v for k, v in os.environ.items() if k not in ('TMUX', 'TMUX_TMPDIR')}
    def tmux(*args, check=True):
        return subprocess.run(['tmux', '-S', outer, *args], env=env, text=True,
                              capture_output=True, check=check).stdout.strip()
    if inner_setup:
        Path(inner).parent.mkdir(parents=True, exist_ok=True)
        setup(inner, config['session'], home, config['active'] + config['resting'], command=command,
              view_command=[sys.executable, str(artifact / 'view-host.py')])
    if 'plants' not in tmux('list-sessions', '-F', '#{session_name}', check=False).splitlines():
        tmux('new-session', '-d', '-s', 'plants', '-n', 'op', shlex.join(command or ['/bin/bash']))
    adapter = str(Path.home() / '.local/bin/mishe-tmux-views')
    commands = {
        'op': command or ['/bin/bash'],
        'budget': command or ['sh', str(home / 'operator/budget/budget_pane.sh')],
        'bash': command or ['/bin/bash'],
        'mishe': command or ['/usr/bin/python3', adapter, 'attach', inner, config['session']],
        'android-body': command or ['/usr/bin/python3', adapter, 'attach',
                                   f'/tmp/tmux-{os.getuid()}/android-body/tmux-{os.getuid()}/default', 'android-body']}
    for name, argv in commands.items():
        if name not in tmux('list-windows', '-t', '=plants', '-F', '#{window_name}').splitlines():
            tmux('new-window', '-d', '-t', 'plants:', '-n', name, shlex.join(argv))
    return tmux('list-panes', '-s', '-t', 'plants', '-F', '#{window_name}|#{pane_id}')


def boot_start(home: Path, artifact: Path, request_id: str, *, timeout=1000):
    """Control-plane only. Guard admission and original unknown effects remain."""
    runtime = artifact / 'runtime'
    evidence = artifact / 'reboot'
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    env = dict(os.environ, MISHE_SEED_HOME=str(home), MISHE_REPO=str(home.parent),
               PYTHONPATH=str(runtime / 'src'))
    cli = [sys.executable, '-m', 'mishe_tauftauf', '--home', str(home)]
    def run(*args):
        return subprocess.run([*cli, *args], env=env, check=True, timeout=120)
    run('hold', 'on', '--reason', 'authorized reboot: waiting for current-boot guard admission')
    write_json(evidence / 'boot-stage.json', {'boot': boot, 'state': 'UNKNOWN', 'reason': 'awaiting guarded boot receipt'})
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        receipt = read_json(Path('/run/mishe-containment/authorized-boot-ready.json'))
        if receipt and receipt.get('id') == request_id and receipt.get('boot') == boot:
            break
        time.sleep(3)
    else:
        raise RuntimeError('no current authorized boot receipt; hold preserved')
    manifest = evidence / 'runtime-manifest.json'
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != receipt['request']['runtime_manifest_hash']:
        raise RuntimeError('runtime manifest changed after reboot authorization')
    for name, digest in read_json(manifest).items():
        if hashlib.sha256((runtime / name).read_bytes()).hexdigest() != digest:
            raise RuntimeError(f'authorized runtime bytes changed: {name}')
    if roles.declared(home) != frozenset(receipt['request']['roles']):
        raise RuntimeError('active roster differs from authorized areas')
    presentation(home)
    if subprocess.check_output(['systemctl', '--user', 'show', 'mishe-plants.slice',
                                '-p', 'FreezerState', '--value'], text=True).strip() != 'running':
        raise RuntimeError('workload frozen after reboot; do not thaw implicitly')
    subprocess.run(['/usr/bin/python3', '/usr/local/lib/mishe-containment/guard.py', 'admit'], check=True, timeout=10)
    cfg = units.Config(home=home, repo=home.parent, python=sys.executable,
                       pythonpath=str(runtime / 'src'), unit_dir=Path.home() / '.config/systemd/user')
    declared = producers.load(home, python=sys.executable, declared=duties.declared(home.parent, home))
    declared += producers.load(home, python=sys.executable)
    report = units.doctor(cfg, roles.declared(home), producers=declared)
    if report.state != 'GREEN':
        raise RuntimeError(f'exact unit check failed: {report.problems}')
    run('sync', '--repo', str(home.parent), '--pythonpath', str(runtime / 'src'))
    run('arm')
    run('hold', 'off')
    write_json(evidence / 'boot-stage.json', dict(boot=boot, state='STARTED', at=time.time(),
               reason='guard admitted; reactive triggers enabled; first real wake acceptance pending'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['ui', 'inner', 'start'])
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--artifact', type=Path, required=True)
    parser.add_argument('--request-id')
    args = parser.parse_args()
    base = f'/tmp/tmux-{os.getuid()}'
    inner, outer = f'{base}/mishe-tauftauf/tmux-{os.getuid()}/default', f'{base}/default'
    if args.mode == 'start':
        try:
            boot_start(args.home, args.artifact, args.request_id)
        except Exception as exc:
            write_json(args.artifact / 'reboot/boot-stage.json', dict(state='UNKNOWN', at=time.time(), reason=str(exc)))
            raise
    elif args.mode == 'inner':
        config = presentation(args.home)
        Path(inner).parent.mkdir(parents=True, exist_ok=True)
        print(setup(inner, config['session'], args.home, config['active'] + config['resting'],
                    view_command=[sys.executable, str(args.artifact / 'view-host.py')]))
    else:
        subprocess.run(['systemd-run', '--user', '--scope', '--quiet', '--slice=mishe.slice',
                        '--property=MemoryMax=512M', '--property=TasksMax=128', '--property=CPUQuota=50%',
                        sys.executable, '-m', 'mishe_tauftauf.plant_boot', 'inner',
                        '--home', str(args.home), '--artifact', str(args.artifact)], check=True, timeout=30)
        print(layout(args.home, args.artifact, outer=outer, inner=inner, inner_setup=False))


if __name__ == '__main__':
    main()
