"""Two durable trial checkpoints, driven by an existing deterministic producer.

Reports stop at 24h. Missing/corrupt reports and interrupted notifications are
UNKNOWN and route through the producer failure target; future ticks validate
retained final evidence instead of starting new trials.
"""
from __future__ import annotations
import hashlib
from pathlib import Path
import time
from .state_files import read_json, write_json
from . import work_metrics


def tick(home, *, now, notify=None):
    root=Path(home)/"work/experiment"
    try:
        manifest=read_json(root/"manifest.json")
        if manifest is None:
            if list(root.glob("*.json")):
                raise ValueError("trial manifest missing after retained evidence")
            return 0
        if manifest.get("version") != 1 or manifest["end"] != manifest["start"]+86400:
            raise ValueError("malformed fixed trial window")
        for label,seconds in (("6h",21600),("24h",86400)):
            deadline=manifest["start"]+seconds
            if now < deadline: continue
            path=root/f"{label}.json"
            marker=root/f"{label}.notification.json"
            record=read_json(path)
            if record is None:
                if marker.exists():
                    raise ValueError("completed or attempted checkpoint lost; preserve UNKNOWN instead of regenerating")
                report=work_metrics.report(home,start=manifest["start"],end=deadline)
                report["input_digests"]={name:hashlib.sha256((Path(home)/name).read_bytes()).hexdigest()
                                         for name in report["inventory"]}
                report["rollout"]=manifest
                write_json(path,report)
                record=report
            if record.get("start") != manifest["start"] or record.get("end") != deadline:
                raise ValueError("checkpoint window mismatch")
            state=read_json(marker)
            if state is None:
                write_json(marker,dict(state="dispatching",at=now))
                if notify is None:
                    from .failure import default_notify
                    notify=lambda role,text:default_notify(home,role,text)
                notify("steward",f"Useful-work {label} checkpoint: {path}. Choose one intervention if loops remain; at 24h record keep/revise/revert. Unknown denominators are not success.")
                write_json(marker,dict(state="sent",at=now))
            elif state.get("state") != "sent":
                raise ValueError("checkpoint notification interrupted; reconcile before replay")
        manifest["state"]="AWAITING_DISPOSITION" if now >= manifest["end"] else "RUNNING"
        write_json(root/"manifest.json",manifest)
        return 0
    except (OSError,ValueError,KeyError,TypeError) as exc:
        write_json(root/"reporter-error.json",dict(state="UNKNOWN",at=now,error=str(exc)))
        print(f"UNKNOWN work-trial: {exc}")
        return 3


def main():
    from .work_boundary import trial_args
    args=trial_args()
    return tick(args.home,now=time.time())


if __name__ == "__main__":
    raise SystemExit(main())
