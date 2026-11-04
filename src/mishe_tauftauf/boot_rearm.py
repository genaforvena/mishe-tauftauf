"""One explicitly authorized next-boot rearm, never recovery of later incidents.

Standalone root-owned adapter for the boot authorization and installed guard's
JSON/procfs/systemd interfaces. It does not import a user-writable package.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time

START_REASON = 'sentinel started/restarted: explicit rearm required'


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def eligible(request, sample, *, invocation, oom):
    """None only for this requested, unchanged, healthy new boot; else reason."""
    try:
        if not re.fullmatch(r'[a-z0-9-]{1,64}', request['id']):
            return 'invalid authorization identity'
        if not sample['boot'] or sample['boot'] == request['old_boot']:
            return 'authorization is for the next boot only'
        if not _number(request['expires']) or not sample['now'] <= request['expires']:
            return 'authorization expired'
        if sample['emergency'] or sample['restoring']:
            return 'emergency or restoration in progress'
        if sample['guard_hash'] != request['guard_hash'] or sample['config_hash'] != request['config_hash']:
            return 'guard/config bytes changed'
        if not invocation or sample['invocation'] != invocation or sample['restarts'] != 0:
            return 'sentinel restarted or identity unavailable'
        if sample['latch']['reason'] != START_REASON:
            return 'unexpected trip; boot authorization does not cover it'
        status = sample['status']
        if status['state'] != 'TRIPPED' or not 0 <= sample['now'] - status['at'] <= 20:
            return 'sentinel missing, stale or not in expected boot-latched state'
        since = status['clear_since']
        if not _number(since) or not 120 <= sample['monotonic'] - since <= sample['monotonic']:
            return '120s continuously healthy interval incomplete'
        obs = status['observation']
        if oom != 0 or obs['oom_kill'] != 0:
            return 'OOM observed in this boot; investigate instead of rearming'
        if not all(obs.get(k) is True for k in ('limits_ok', 'ssh_available', 'host_io_available')):
            return 'limits or functional probes failed'
        numeric = ('memory', 'maximum', 'tasks', 'task_max', 'available', 'disk_free',
                   'memory_pressure', 'io_pressure')
        if not all(_number(obs[k]) and obs[k] >= 0 for k in numeric):
            return 'malformed resource observation'
        if not (obs['memory'] < obs['maximum'] * .7 and obs['tasks'] < obs['task_max'] * .7
                and obs['available'] >= 6 * 2**30 and obs['disk_free'] >= 12 * 2**30
                and obs['memory_pressure'] < 1 and obs['io_pressure'] < 10
                and status.get('pressure_warning') is False):
            return 'resource headroom or pressure not clear'
        return None
    except (KeyError, TypeError, ValueError, AttributeError):
        return 'missing or malformed boot evidence'


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def consume(path: Path, expected: dict):
    """Exclusive durable consumed record before rearm; no reusable permission."""
    if read(path) != expected:
        raise ValueError('authorization changed before consumption')
    saved = path.with_name(f'consumed-{expected["id"]}.json')
    os.link(path, saved)  # refuses duplicates; preserves original bytes and mode
    path.unlink()
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return saved


def snapshot(args):
    shown = subprocess.check_output(['systemctl', 'show', 'mishe-containment.service',
                                    '-p', 'InvocationID', '-p', 'NRestarts'], text=True, timeout=5)
    fields = dict(line.split('=', 1) for line in shown.splitlines() if '=' in line)
    config = read(args.config)
    events = dict(line.split() for line in (Path(config['cgroup']) / 'memory.events').read_text().splitlines())
    oom = int(events['oom_kill'])
    if oom < 0:
        raise ValueError('invalid kernel OOM counter')
    return dict(boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                now=time.time(), monotonic=time.monotonic(), guard_hash=digest(args.guard),
                config_hash=digest(args.config), invocation=fields.get('InvocationID'),
                restarts=int(fields.get('NRestarts', '-1')), emergency=(args.state / 'emergency').exists(),
                restoring=(args.state / 'restoring').exists(), latch=read(args.state / 'tripped'),
                status=read(args.run / 'status.json'), oom=oom)


def write(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as handle:
        json.dump(value, handle, indent=2); handle.flush(); os.fsync(handle.fileno())
    temporary.chmod(0o644)
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', type=Path, required=True)
    parser.add_argument('--state', type=Path, default=Path('/var/lib/mishe-containment'))
    parser.add_argument('--run', type=Path, default=Path('/run/mishe-containment'))
    parser.add_argument('--guard', type=Path, default=Path('/usr/local/lib/mishe-containment/guard.py'))
    parser.add_argument('--config', type=Path, default=Path('/etc/mishe-containment.json'))
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    request = read(args.request)
    if Path('/proc/sys/kernel/random/boot_id').read_text().strip() == request.get('old_boot'):
        print('authorization is for the next boot only', flush=True)
        return 3
    deadline = time.monotonic() + 900
    invocation = None
    while True:
        try:
            sample = snapshot(args)
            invocation = invocation or sample['invocation']
            reason = eligible(request, sample, invocation=invocation, oom=sample['oom'])
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            reason = f'observation UNKNOWN: {exc}'
        print(reason or 'authorized boot eligible', flush=True)
        if args.check:
            return 3 if reason else 0
        if reason is None:
            break
        fatal = ('next boot only', 'expired', 'unexpected trip', 'OOM observed', 'bytes changed',
                 'sentinel restarted', 'emergency or restoration')
        if any(part in reason for part in fatal) or time.monotonic() >= deadline:
            return 3
        time.sleep(3)
    consume(args.request, request)
    result = subprocess.run(['/usr/bin/python3', str(args.guard), 'rearm'], timeout=20)
    if result.returncode:
        return result.returncode
    for _ in range(10):
        status = read(args.run / 'status.json')
        if ((args.state / 'tripped').exists() or (args.state / 'emergency').exists()
                or not 0 <= time.time() - status['at'] <= 20):
            return 3
        if status['state'] == 'READY' and status['mode'] == 'NORMAL':
            write(args.run / 'authorized-boot-ready.json', dict(
                id=request['id'], boot=sample['boot'], at=time.time(), request=request))
            return 0
        time.sleep(1)
    return 3


if __name__ == '__main__':
    raise SystemExit(main())
