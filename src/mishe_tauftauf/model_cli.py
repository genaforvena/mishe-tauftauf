"""Model command registration and backwards-compatible installed CLI dispatcher."""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

def cmd_model(args) -> int:
    from . import model_change
    if args.model_command == 'replace':
        import math
        if not math.isfinite(args.wait) or not 0 <= args.wait <= 60:
            raise ValueError('--wait must be finite and between 0 and 60')
        report = model_change.replace(args.home, args.source_model, args.target_model,
            selected=args.role, effort=args.effort, dry_run=args.dry_run, fresh_start=args.fresh_start)
    elif args.model_command == 'status':
        report = model_change.status(args.home, args.change)
    elif args.model_command == 'revert':
        report = model_change.revert(args.home, args.change)
    elif args.model_command == 'watch':
        if args.check:
            report = model_change.check_watch(args.home)
        elif args.once:
            report = model_change.watch_once(args.home)
        else:
            model_change.watch(args.home, args.interval)
            return 0
    else:
        values = model_change.inventory(args.home)
        report = {'state': 'inventory', 'roles': {role: ({'model': value.model, 'effort': value.effort, 'kind': value.kind}
            if not isinstance(value, str) else {'state': 'unknown', 'reason': value}) for role, value in values.items()}}
    if args.model_command == 'replace' and not args.dry_run and args.wait:
        deadline = time.monotonic() + args.wait
        while report['state'] == 'pending' and time.monotonic() < deadline:
            time.sleep(min(1, max(0, deadline - time.monotonic())))
            report = model_change.advance(args.home, report['id'])
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report['state'] in ('verified', 'delivered', 'reverted', 'preview', 'inventory', 'ok') else 3


def register(sub):
    model = sub.add_parser('model', help='Replace models across registered minds with observable activation').add_subparsers(dest='model_command', required=True)
    p = model.add_parser('list'); p.set_defaults(func=cmd_model)
    p = model.add_parser('replace')
    p.add_argument('source_model'); p.add_argument('target_model')
    p.add_argument('--role', action='append', help='Limit to a registered mind (repeatable)')
    p.add_argument('--effort', choices=('preserve', 'compatible', 'highest'), default='preserve')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--fresh-start', action='store_true', help='Explicitly abandon current wakes, preserve evidence and start fresh; prior effects stay UNKNOWN')
    p.add_argument('--wait', type=float, default=0, help='Bounded wait for resident adoption in seconds (0..60)')
    p.set_defaults(func=cmd_model)
    for name in ('status', 'revert'):
        p = model.add_parser(name); p.add_argument('change'); p.set_defaults(func=cmd_model)
    p = model.add_parser('watch')
    mode = p.add_mutually_exclusive_group(); mode.add_argument('--once', action='store_true'); mode.add_argument('--check', action='store_true')
    p.add_argument('--interval', type=float, default=30); p.set_defaults(func=cmd_model)


def main(argv=None):
    from . import cli
    from .observations import validate_home, validate_home_binding
    from .feed import FeedError
    root = cli.parser()
    sub = root._subparsers._group_actions[0]
    if 'model' not in sub.choices:
        register(sub)
    args = root.parse_args(argv)
    if args.func is not cmd_model:
        return cli.main(argv)
    try:
        args.home = args.home.resolve()
        validate_home_binding(args.home)
        validate_home(args.home)
        return cmd_model(args)
    except (OSError, ValueError, RuntimeError, FeedError) as exc:
        print(f'mishe: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
