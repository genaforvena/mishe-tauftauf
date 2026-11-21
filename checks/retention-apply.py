#!/usr/bin/env python3
"""Retention duty: plan one output class, optionally archive-and-remove.

The class is a directory of generated output under the site home.  A file is a
deletion candidate only when it is old enough and its basename appears nowhere
in the citation corpus (patch records, release records, signal files and signal
details).  ``--apply`` archives every candidate, verifies each archived size,
then unlinks; a file that fails verification is kept.

Without ``--apply`` the duty only plans and prints the summary, so it can run as
a read-only check first.  Exit codes: 0 plan/applied, 2 UNKNOWN (bad arguments
or an unreadable class).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mishe_tauftauf.site_retention import apply_plan, corpus_basenames, plan_class

CORPUS = ("patches", "work/releases", "signals")
CORPUS_SUFFIXES = (".json", ".detail")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--class", dest="class_name", required=True,
                        help="site-relative directory of the output class")
    parser.add_argument("--min-age-hours", type=float, default=48.0)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--archive", type=Path, default=None)
    args = parser.parse_args(argv)

    home = args.home
    # features/ is the long-lived household record (docs/household-features.md): never a retention class,
    # whatever spelling reaches here (a, a/, ./a, a/../a, an absolute path or a symlink resolving into it).
    # An ancestor of features/ (".", the home itself) is refused too: plan_class rglobs it and would plan the store.
    protected = (home / "features").resolve()
    class_dir = home / args.class_name
    resolved = class_dir.resolve()
    if resolved == protected or protected in resolved.parents or resolved in protected.parents:
        print(json.dumps({"state": "unknown", "class": args.class_name,
                          "reason": "features/ is the long-lived household record and is not a retention class"}))
        return 2
    if not class_dir.is_dir():
        print(json.dumps({"state": "unknown", "reason": f"no class dir {class_dir}"}))
        return 2

    corpus = corpus_basenames([home / name for name in CORPUS], CORPUS_SUFFIXES)
    plan = plan_class(class_dir, corpus, now=time.time(), min_age_s=args.min_age_hours * 3600)
    out = {"state": "planned", "class": args.class_name, "corpus_basenames": len(corpus)}
    out.update(plan.summary())
    if args.apply:
        archive = args.archive or (home / "archive" / f"retention-{class_dir.name}.tar.gz")
        archive.parent.mkdir(parents=True, exist_ok=True)
        if args.class_name == "work/releases":
            # A review merge and the retry re-arm scan read-modify-write these records;
            # unlink under the same lock so neither decides on a file that just vanished.
            from mishe_tauftauf.release_promotion import DeliveryBusy, review_records_lock
            try:
                with review_records_lock(home):
                    out["apply"] = apply_plan(plan, class_dir, archive)
            except DeliveryBusy:
                print(json.dumps({"state": "unknown", "class": args.class_name,
                                  "reason": "a review merge or re-arm scan holds the record lock"}))
                return 2
        else:
            out["apply"] = apply_plan(plan, class_dir, archive)
        out["archive"] = str(archive)
        out["state"] = "applied"
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
