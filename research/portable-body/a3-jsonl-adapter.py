#!/usr/bin/env python3
"""A3 JSONL Host-Facing Adapter

Converts A3 shell prototype's key=value text journal records to canonical
JSONL control records per canonical-wire-profile-20261003.md.

Usage:
    a3-jsonl-adapter.py <a3-journal-file> [--writer-id ID] [--host-id ID]

Reads A3 journal records (key=value format) and outputs canonical JSONL
control records to stdout. Each A3 record maps to one JSONL control record.

A3 journal format:
    op=<id> cap=<name> <args> status=<status> [result=<value>]

Canonical JSONL format:
    {"type":"intent","operation_id":"<id>","capability":"<name>","request":{...},"writer_id":"<id>","host_id":"<id>","timestamp":"<ts>"}
    {"type":"outcome","operation_id":"<id>","capability":"<name>","request":{...},"writer_id":"<id>","host_id":"<id>","timestamp":"<ts>","status":"<status>","result":...}

Status mapping (A3 → profile):
    ok          → completed
    partial     → indeterminate
    error       → failed
    unsupported → unsupported
    denied      → failed
    unavailable → failed
    unknown     → indeterminate

Required profile fields added:
    writer_id: caller identity (from --writer-id or "body-research")
    host_id: host identity (from --host-id or "local")
    timestamp: ISO 8601 UTC timestamp
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


def parse_a3_record(line):
    """Parse one A3 key=value journal record into a dict.

    A3 format: <type> op=<id> cap=<name> [args...] [status=<status>] [result=<value>]
    where <type> is 'intent' or 'outcome'.
    Args are space-separated key=value or key="value" pairs.
    cap may be missing on outcome records.
    """
    line = line.strip()
    if not line:
        return None

    # Extract record type (intent or outcome)
    m = re.match(r'^(intent|outcome)\s+', line)
    if not m:
        return None
    record_type = m.group(1)
    rest = line[m.end():]

    # Extract op
    m = re.search(r'op=(\S+)', rest)
    if not m:
        return None
    op_id = m.group(1)

    # Extract cap (optional on outcome records)
    cap = None
    m = re.search(r'cap=(\S+)', rest)
    if m:
        cap = m.group(1)

    # Extract status (only on outcome records)
    status = None
    m = re.search(r'status=(\S+)', rest)
    if m:
        status = m.group(1)

    # Extract result (optional, may contain spaces)
    result = None
    m = re.search(r'result=(.*)$', rest)
    if m:
        result = m.group(1).strip()
        # Remove surrounding quotes if present
        if result.startswith('"') and result.endswith('"'):
            result = result[1:-1]

    # Extract args (key=value or key="value" pairs)
    args = {}
    for m in re.finditer(r'(\w+)=(?:"([^"]*)"|(\S+))', rest):
        key = m.group(1)
        if key not in ('op', 'cap', 'status', 'result', 'v'):
            args[key] = m.group(2) if m.group(2) is not None else m.group(3)

    return {
        'type': record_type,
        'op': op_id,
        'cap': cap,
        'args': args,
        'status': status,
        'result': result,
    }


def map_status(a3_status):
    """Map A3 status to canonical profile outcome."""
    mapping = {
        'ok': 'completed',
        'partial': 'indeterminate',
        'error': 'failed',
        'unsupported': 'unsupported',
        'denied': 'failed',
        'unavailable': 'failed',
        'unknown': 'indeterminate',
    }
    return mapping.get(a3_status, 'indeterminate')


def to_jsonl_record(a3_record, writer_id, host_id):
    """Convert one A3 record to a canonical JSONL control record."""
    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    record_type = 'intent' if a3_record['type'] == 'intent' else 'outcome'

    base = {
        'type': record_type,
        'operation_id': a3_record['op'],
        'capability': a3_record['cap'],
        'request': a3_record['args'],
        'writer_id': writer_id,
        'host_id': host_id,
        'timestamp': now,
    }

    if record_type == 'outcome':
        base['status'] = map_status(a3_record['status'] or 'unknown')
        if a3_record['result'] is not None:
            base['result'] = a3_record['result']

    return json.dumps(base, ensure_ascii=False, separators=(',', ':'))


def main():
    if len(sys.argv) < 2:
        print("Usage: a3-jsonl-adapter.py <a3-journal-file> [--writer-id ID] [--host-id ID]", file=sys.stderr)
        sys.exit(1)

    journal_file = sys.argv[1]
    writer_id = 'body-research'
    host_id = 'local'

    # Parse optional args
    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == '--writer-id' and i + 1 < len(sys.argv):
            writer_id = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == '--host-id' and i + 1 < len(sys.argv):
            host_id = sys.argv[i + 1]
            i += 2
        else:
            i += 1

    journal_path = Path(journal_file)
    if not journal_path.exists():
        print(f"Error: journal file not found: {journal_file}", file=sys.stderr)
        sys.exit(1)

    # Track last cap seen per op_id for outcome records without cap
    op_caps = {}
    with open(journal_path, 'r', encoding='utf-8') as f:
        for line in f:
            record = parse_a3_record(line)
            if record:
                # If cap is missing on outcome, use cap from preceding intent
                if record['cap'] is None and record['op'] in op_caps:
                    record['cap'] = op_caps[record['op']]
                if record['cap'] is not None:
                    op_caps[record['op']] = record['cap']
                    jsonl = to_jsonl_record(record, writer_id, host_id)
                    print(jsonl)


if __name__ == '__main__':
    main()
