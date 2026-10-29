"""Literal adapters for model-change launchers, OMP catalog/JSON and procfs.

Only export-plus-exec launchers are supported. Unknown shell programs are data,
never executed to discover configuration. Provider output and secrets are never
included in public errors. Receipt JSON is decoded and validated here too.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import time

from . import omp_pane

EFFORTS = ('off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')


@dataclass
class Launcher:
    text: str
    argv: list[str]
    environment: dict[str, str]
    model: str
    effort: str | None
    kind: str
    command_line: int
    selector_line: int | None = None


def identity(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9._:@+-]+/[A-Za-z0-9._/:@+-]+', value):
        raise ValueError('an exact provider/model identity is required')
    return value


def _single(argv, name):
    count = sum(word == name or word.startswith(name + '=') for word in argv)
    value = omp_pane.option(argv, name)
    if count > 1 or (count and (not value or value.startswith('-'))):
        raise ValueError(f'ambiguous {name}')
    return value


def launcher(text):
    commands = omp_pane._launcher_commands(text)
    if not commands:
        raise ValueError('unsupported shell launcher; use literal export and exec')
    env, argv = {}, None
    for command in commands:
        if command[0] == 'export' and argv is None:
            for word in command[1:]:
                name, sep, value = word.partition('=')
                if not sep or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name) or name in env:
                    raise ValueError('ambiguous environment assignment')
                if name in ('PATH', 'LD_PRELOAD', 'LD_LIBRARY_PATH') or any(x in name for x in ('TOKEN', 'API_KEY', 'SECRET')):
                    raise ValueError('launcher has lookup overrides or inline secrets; use credential references')
                env[name] = value
        elif command[0] == 'exec' and argv is None and len(command) > 1:
            argv = command[1:]
        else:
            raise ValueError('unsupported command or code following exec')
    if argv is None:
        raise ValueError('literal exec required')
    program = Path(argv[0]).name
    native = '-m' in argv and 'mishe_tauftauf.native_resident' in argv
    legacy = 'MISHE_NATIVE_SELECTOR' in env
    if program != 'omp' and not native and not legacy:
        raise ValueError('custom engine wrapper is UNKNOWN; no supported model adapter')
    if legacy and _single(argv, '--model'):
        raise ValueError('ambiguous native selector')
    chosen = env['MISHE_NATIVE_SELECTOR'] if legacy else _single(argv, '--model')
    identity(chosen)
    effort = _single(argv, '--reasoning' if native else '--thinking')
    if effort is not None and effort not in EFFORTS:
        raise ValueError('unsupported effort spelling')
    # Preserve comments/exports and change only the one logical exec line.
    # Multiline shell continuations are intentionally not rewritten.
    lines = text.splitlines()
    positions = [i for i, line in enumerate(lines) if line.lstrip().startswith('exec ')]
    if len(positions) != 1 or any(line.endswith('\\') for line in lines):
        raise ValueError('model rewrite requires one literal exec line')
    selector = next((i for i, line in enumerate(lines) if line.lstrip().startswith('export MISHE_NATIVE_SELECTOR=')), None)
    if legacy and selector is None:
        raise ValueError('native selector needs a dedicated export')
    return Launcher(text, argv, env, chosen, effort, 'native' if native or legacy else 'omp', positions[0], selector)


def change_option(argv, name, value):
    result = list(argv)
    for i, word in enumerate(result):
        if word == name:
            result[i + 1] = value
            return result
        if word.startswith(name + '='):
            result[i] = name + '=' + value
            return result
    result.extend((name, value))
    return result


def rewrite(config, target, effort):
    identity(target)
    lines, argv = config.text.splitlines(), config.argv
    if config.selector_line is not None:
        lines[config.selector_line] = 'export MISHE_NATIVE_SELECTOR=' + shlex.quote(target)
    else:
        argv = change_option(argv, '--model', target)
    if effort is not None:
        name = '--reasoning' if 'mishe_tauftauf.native_resident' in argv else '--thinking'
        argv = change_option(argv, name, effort)
    lines[config.command_line] = 'exec ' + shlex.join(argv)
    return '\n'.join(lines) + '\n'


def run(command, *, env=None, cwd=None, timeout=40):
    """Bound the entire group, including descendants retaining output pipes."""
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as error:
        p = subprocess.Popen(command, env=env, cwd=cwd, stdout=output, stderr=error,
                             stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            p.wait(timeout=timeout)
            output.seek(0)
            data = output.read(4 * 1024 * 1024 + 1)
            if p.returncode or len(data) > 4 * 1024 * 1024:
                raise ValueError('model runtime command failed; no configuration changed')
            return data.decode('utf-8')
        except subprocess.TimeoutExpired:
            raise ValueError('model runtime command timed out') from None
        finally:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            p.wait()


def runtime(config):
    env = dict(os.environ)
    env.update(config.environment)
    if config.kind == 'omp':
        binary = shutil.which(config.argv[0], path=env.get('PATH'))
        if not binary:
            raise ValueError('OMP executable unavailable')
    else:
        # Legacy native runners may have different provider packages. Do not
        # claim that an external OMP probe validates those internal packages.
        raise ValueError('native runner requires its own provider adapter; selection remains UNKNOWN')
    options = []
    for i, word in enumerate(config.argv):
        if word == '--config' and i + 1 < len(config.argv):
            options.extend((word, config.argv[i + 1]))
        elif word.startswith('--config='):
            options.append(word)
    for name in ('--profile', '--api-key', '--provider', '--continue', '--resume', '--session'):
        if any(word == name or word.startswith(name + '=') for word in config.argv):
            raise ValueError(f'{name} needs a dedicated model-change adapter')
    cwd = omp_pane.option(config.argv, '--cwd') or os.getcwd()
    return binary, env, options, cwd


def preflight(config, target):
    binary, env, options, cwd = runtime(config)
    provider, _, model = identity(target).partition('/')
    raw = run([binary, 'models', provider, '--json', '--no-extensions', *options], env=env, cwd=cwd)
    try:
        rows = json.loads(raw)['models']
        matches = [r for r in rows if r.get('provider') == provider and r.get('id') == model]
        if len(matches) != 1 or not isinstance(matches[0].get('thinking'), list):
            raise ValueError('exact target unavailable')
        efforts = matches[0]['thinking']
        if any(x not in EFFORTS for x in efforts):
            raise ValueError('unknown catalog effort')
        return {'thinking': efforts}
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError('exact model/catalog unavailable in this runtime') from None


def probe(config, target, effort):
    binary, env, options, cwd = runtime(config)
    args = [binary, '--model', target, '--mode', 'json', '--print', '--no-session',
            '--no-tools', '--no-extensions', '--no-skills', '--no-rules', '--no-title',
            '--no-lsp', '--no-prewalk', '--max-time', '30', *options]
    if effort:
        args.extend(('--thinking', effort))
    args.append('Reply exactly MISHE_MODEL_OK.')
    raw = run(args, env=env, cwd=cwd)
    provider, _, model = target.partition('/')
    try:
        for line in reversed(raw.splitlines()):
            row = json.loads(line)
            message = row.get('message', {})
            if row.get('type') == 'message_end' and message.get('role') == 'assistant':
                if (message.get('provider') == provider and message.get('model') == model
                        and message.get('stopReason') in ('stop', 'endTurn')
                        and any(c.get('type') == 'text' and 'MISHE_MODEL_OK' in c.get('text', '') for c in message.get('content', []))):
                    return {'model': target, 'result': 'ok'}
    except (ValueError, TypeError, AttributeError):
        pass
    raise ValueError('target provider probe did not return an attributable successful completion')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def save(path, value):
    atomic(path, (json.dumps(value, sort_keys=True, indent=2) + '\n').encode(), 0o600)


def atomic(path, data, mode=0o755):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.model-')
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def receipt(home, change):
    if not re.fullmatch('[0-9a-f]{32}', change):
        raise ValueError('invalid model change id')
    root = home / 'model-changes' / change
    if root.resolve() != root:
        raise ValueError('model change store cannot be a symlink')
    value = load(root / 'receipt.json')
    if not isinstance(value, dict) or value.get('version') != 1 or value.get('home') != str(home) or value.get('id') != change or not isinstance(value.get('roles'), dict):
        raise ValueError('invalid model change receipt')
    identity(value.get('source'))
    identity(value.get('target'))
    if (type(value.get('created')) not in (float, int) or not math.isfinite(value['created'])
            or not isinstance(value.get('unknown'), dict)):
        raise ValueError('invalid model change metadata')
    for role, row in value['roles'].items():
        from .observations import validate_slug
        validate_slug(role)
        if (root / role).resolve() != root / role or not isinstance(row, dict) or type(row.get('mode')) is not int or not 0 <= row['mode'] <= 0o777:
            raise ValueError('invalid model role snapshot')
        for name in ('before', 'after'):
            p = root / role / name
            if p.is_symlink() or digest(p.read_bytes()) != row[name]:
                raise ValueError('model change snapshot damaged')
    return root, value


def process(home, role):
    from . import seed
    from .tmux import owned_socket
    session = seed.recorded_session(home)
    socket = owned_socket(home, session) if session else None
    if not socket:
        raise ValueError('owned live session unavailable')
    # Gate the display-message: it silently falls back to the session's first
    # window for a missing window, so a wrong-but-live PID would pass the
    # dead==0 check and misattribute the model. has-session errors instead.
    gate = seed._tmux('has-session', '-t', f'{session}:{role}', check=False, socket=socket)
    if gate.returncode != 0:
        raise ValueError('mind window missing')
    result = seed._tmux('display-message', '-p', '-t', f'{session}:{role}.1', '#{pane_pid} #{pane_dead}', socket=socket)
    fields = result.stdout.decode().split()
    if len(fields) != 2 or not fields[0].isdigit() or fields[1] != '0':
        raise ValueError('mind process missing or dead')
    pid = int(fields[0])
    def start():
        return Path(f'/proc/{pid}/stat').read_text().rpartition(')')[2].split()[19]
    born = start()
    uptime = float(Path('/proc/uptime').read_text().split()[0])
    started = time.time() - uptime + int(born) / os.sysconf('SC_CLK_TCK')
    argv = omp_pane.command_line(pid)
    env = dict(item.partition('=')[::2] for item in Path(f'/proc/{pid}/environ').read_text().split('\0') if '=' in item)
    if not argv or start() != born:
        raise ValueError('process identity changed')
    return session, {'pid': pid, 'start': born, 'started': started, 'model': omp_pane.model(argv), 'marker': env.get('MISHE_MODEL_CHANGE')}

def owned_socket(home, session):
    from .tmux import owned_socket as find
    socket = find(home, session)
    if not socket:
        raise ValueError('owned live socket unavailable')
    return socket


def idle(home, session, role):
    from . import seed
    socket = owned_socket(home, session)
    target = f'{session}:{role}.1'
    first = seed._tmux('capture-pane', '-p', '-t', target, socket=socket, check=False)
    if first.returncode or seed._omp_idle_mode(first.stdout.decode('utf-8', 'replace')) is None:
        return False
    time.sleep(0.5)
    second = seed._tmux('capture-pane', '-p', '-t', target, socket=socket, check=False)
    return second.returncode == 0 and first.stdout == second.stdout


def is_ancestor(pid):
    current = os.getpid()
    while current > 1:
        if current == pid:
            return True
        try:
            current = int(Path(f'/proc/{current}/stat').read_text().rpartition(')')[2].split()[1])
        except (OSError, ValueError, IndexError):
            raise ValueError('caller ancestry unavailable') from None
    return False


def account_store_for_role(home, role):
    """Return the credential-store hint a role's launcher currently selects, or None.

    Reads the launcher env ``PI_CODING_AGENT_DIR`` (and ``MISHE_ENGINE_ALIAS`` as
    fallback alias) without touching credential data. Returns a store path string
    or ``None`` when unavailable or unreadable. Never raises for valid homes.
    """
    try:
        from pathlib import Path as _P
        text = _P(home).joinpath('minds', role).read_text(encoding='utf-8')
        cfg = launcher(text)
        store = cfg.environment.get('PI_CODING_AGENT_DIR')
        if store is not None:
            return store
        alias = cfg.environment.get('MISHE_ENGINE_ALIAS')
        if alias is None:
            return None
        # Resolve alias to store via registry when possible
        try:
            from . import engine_boundary as _eb
            _, reg = _eb.load_registry(_P(home))
            ent = reg.get(alias)
            if ent is not None:
                return ent['store']
            return alias
        except Exception:
            return alias
    except Exception:
        return None


def _instrument_alias(alias):
    if alias is None:
        return None
    if not isinstance(alias, str) or not alias:
        raise ValueError('alias must be non-empty string')
    return alias


def instrument(config, change, directory, alias=None):
    if config.kind != 'omp':
        raise ValueError('native process evidence adapter unavailable')
    lines = config.text.splitlines()
    argv = change_option(config.argv, '--session-dir', str(directory / 'sessions'))
    lines[config.command_line] = 'exec ' + shlex.join(argv)
    lines.insert(config.command_line, 'export MISHE_MODEL_CHANGE=' + shlex.quote(change))
    # Carry account alias for attribution without changing credential material.
    # ``MISHE_ENGINE_ALIAS`` is a non-secret hint for observe(); the durable
    # account switch remains the PI_CODING_AGENT_DIR launcher env (see
    # engine_rotation doc). When alias is given, export it alongside the
    # change marker.
    if alias is not None:
        alias = _instrument_alias(alias)
        lines.insert(config.command_line + 1, 'export MISHE_ENGINE_ALIAS=' + shlex.quote(alias))
    return '\n'.join(lines) + '\n'


def observe(home, role, change, target, directory):
    try:
        _, proc = process(home, role)
        if proc['model'] != target:
            return {'state': 'queued', 'reason': 'running selection differs', 'process': proc}
        if proc['marker'] != change:
            return {'state': 'adopted', 'reason': 'model retained after canonical rotation; current provider completion not bound to this change', 'process': proc}
        state = completion(directory, target, proc['started'])
        return {'state': state, 'reason': 'resident provider completion observed' if state == 'verified' else 'resident provider failure' if state == 'failed' else 'waiting for resident provider completion', 'process': proc}
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError) as exc:
        return {'state': 'unknown', 'reason': str(exc)}


def completion(directory, target, started):
    # Each activated process gets its own session directory, so another
    # role, old context or the synthetic preflight cannot satisfy this.
    provider, _, model = target.partition('/')
    completed = False
    logs = list((directory / 'sessions').rglob('*.jsonl'))
    if len(logs) > 100:
        raise ValueError('session evidence exceeds bounded inventory')
    for path in logs:
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('session evidence exceeds read bound')
        for line in path.read_text().splitlines():
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get('message', {}), dict):
                raise ValueError('malformed session record shape')
            msg = row.get('message', {})
            if row.get('type') == 'message' and msg.get('role') == 'assistant' and msg.get('provider') == provider and msg.get('model') == model:
                stamp = msg.get('timestamp')
                if type(stamp) not in (float, int) or not math.isfinite(stamp) or stamp > (time.time() + 5) * 1000:
                    raise ValueError('provider completion timestamp unavailable')
                if stamp / 1000 < started:
                    continue
                if msg.get('stopReason') in ('error', 'aborted'):
                    return 'failed'
                if msg.get('stopReason') in ('stop', 'toolUse', 'endTurn'):
                    completed = True
    return 'verified' if completed else 'activated'
