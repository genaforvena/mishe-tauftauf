from mishe_tauftauf import arm


class FakeRun:
    def __init__(self, listed=""):
        self.calls = []
        self.listed = listed

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        stdout = self.listed if argv[:4] == ["systemctl", "--user", "list-units", "--type=timer"] else ""

        class Done:
            pass

        done = Done()
        done.stdout = stdout
        return done


def test_each_arm_gets_a_fresh_unit_name(monkeypatch):
    fake = FakeRun()
    monkeypatch.setattr(arm.subprocess, "run", fake)
    arm.systemd_scheduler(2_000_000_000.0, command=["python", "due"])
    arm.systemd_scheduler(2_000_000_100.0, command=["python", "due"])
    names = [next(a[len("--unit="):] for a in c if a.startswith("--unit="))
             for c in fake.calls if c[0] == "systemd-run"]
    assert len(names) == 2
    assert names[0] != names[1]
    assert all(n.startswith("mishe-next-due-") for n in names)


def test_arm_never_reuses_the_running_due_service_name(monkeypatch):
    fake = FakeRun()
    monkeypatch.setattr(arm.subprocess, "run", fake)
    arm.systemd_scheduler(2_000_000_000.0, command=["python", "due"])
    unit = next(a for c in fake.calls if c[0] == "systemd-run" for a in c if a.startswith("--unit="))
    assert unit != f"--unit={arm.TIMER_UNIT}"


def test_earlier_pending_timers_are_stopped_before_rearm(monkeypatch):
    listed = "mishe-next-due-aaaa1111.timer loaded active waiting\n"
    fake = FakeRun(listed=listed)
    monkeypatch.setattr(arm.subprocess, "run", fake)
    arm.systemd_scheduler(2_000_000_000.0, command=["python", "due"])
    stops = [c[-1] for c in fake.calls if c[:3] == ["systemctl", "--user", "stop"]]
    assert "mishe-next-due-aaaa1111.timer" in stops
    assert stops.index("mishe-next-due-aaaa1111.timer") < next(
        i for i, c in enumerate(fake.calls) if c[0] == "systemd-run")


def test_none_only_removes_pending_timers(monkeypatch):
    fake = FakeRun(listed="mishe-next-due-aaaa1111.timer loaded active waiting\n")
    monkeypatch.setattr(arm.subprocess, "run", fake)
    arm.systemd_scheduler(None, command=["python", "due"])
    assert not any(c[0] == "systemd-run" for c in fake.calls)
    assert ["systemctl", "--user", "stop", "mishe-next-due-aaaa1111.timer"] in fake.calls


def calendar_of(fake):
    call = next(c for c in fake.calls if c[0] == "systemd-run")
    return next(a[len("--on-calendar="):] for a in call if a.startswith("--on-calendar="))


def test_an_overdue_target_retries_on_a_backoff_not_every_second(monkeypatch):
    # After a long outage every intention is overdue; re-arming for now+1 s spun the plant at several
    # refused wake requests per second (2026-11-05 22:33-22:42Z: 3-5 per second, 3700 start-limit refusals/hour).
    import time
    from datetime import datetime, timezone
    fake = FakeRun()
    monkeypatch.setattr(arm.subprocess, "run", fake)
    now = time.time()
    arm.systemd_scheduler(now - 20000, command=["python", "due"])
    moment = datetime.strptime(calendar_of(fake), "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
    wait = moment.timestamp() - now
    assert arm.OVERDUE_RETRY_SECONDS - 3 <= wait <= arm.OVERDUE_RETRY_SECONDS + 3
    assert arm.OVERDUE_RETRY_SECONDS >= 60


def test_a_near_future_target_keeps_its_precision(monkeypatch):
    import time
    from datetime import datetime, timezone
    fake = FakeRun()
    monkeypatch.setattr(arm.subprocess, "run", fake)
    now = time.time()
    arm.systemd_scheduler(now + 30, command=["python", "due"])
    moment = datetime.strptime(calendar_of(fake), "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
    assert 27 <= moment.timestamp() - now <= 33


def test_an_imminent_target_is_not_pushed_out_by_the_backoff(monkeypatch):
    import time
    from datetime import datetime, timezone
    fake = FakeRun()
    monkeypatch.setattr(arm.subprocess, "run", fake)
    now = time.time()
    arm.systemd_scheduler(now + 0.4, command=["python", "due"])
    moment = datetime.strptime(calendar_of(fake), "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
    assert 0 <= moment.timestamp() - now <= 3


def test_scheduled_return_uses_callers_source_pin_and_plant_slice(monkeypatch):
    from pathlib import Path
    fake=FakeRun();monkeypatch.setattr(arm.subprocess,"run",fake)
    arm.systemd_scheduler(2_000_000_000.0,command=["python","due"])
    call=next(c for c in fake.calls if c[0]=="systemd-run")
    assert "--setenv=PYTHONPATH="+str(Path(arm.__file__).resolve().parents[1]) in call
    assert "--slice=mishe-plants.slice" in call
