from mishe_tauftauf import arm


class FakeRun:
    def __init__(self, listed=""):
        self.calls = []
        self.listed = listed

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        stdout = self.listed if argv[:4] == ["systemctl", "--user", "list-units", "--type=timer"] else ""

        class Done:
            returncode = 0
            stderr = b""

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


def test_a_refused_systemd_run_surfaces_the_manager_error(monkeypatch):
    """DM 299366: the armer swallowed stderr, so a failed arm could not be diagnosed."""
    class Refused:
        returncode = 1
        stderr = b"Failed to start transient unit: Unit is bound to an existing unit"
        stdout = b""

    monkeypatch.setattr(arm.subprocess, "run", lambda argv, **kwargs: Refused())
    try:
        arm.systemd_scheduler(2_000_000_000.0, command=["python", "due"])
        raise AssertionError("a refused arm must raise, not pass silently")
    except RuntimeError as exc:
        assert "rc=1" in str(exc) and "bound to an existing unit" in str(exc), str(exc)


def test_concurrent_rearms_serialise_so_no_arm_cancels_its_peer(monkeypatch, tmp_path):
    """Two arms racing must not interleave their stop/start.

    2026-11-11: producers ending in the same second each re-armed the next-due timer; B's
    ``systemctl stop`` caught A's still-pending start job and cancelled it, so A's
    ``systemd-run`` returned rc=1 "Job ... canceled" (delivery-requests 00:52:46Z,
    household-daylight 01:50:07Z). The lock file must make each replace atomic.
    """
    import threading
    import time

    lock = tmp_path / "next-due.lock"
    entered = threading.Event()   # a fresh timer start is in flight
    release = threading.Event()   # let that arm finish
    log = []
    log_lock = threading.Lock()

    class GatedRun:
        def __call__(self, argv, **kwargs):
            with log_lock:
                log.append((threading.current_thread().name, tuple(argv[:3])))
            if argv[0] == "systemd-run":
                entered.set()
                release.wait(5)

            class Done:
                returncode = 0
                stderr = b""
                stdout = ""

            return Done()

    monkeypatch.setattr(arm.subprocess, "run", GatedRun())

    def arm_once():
        arm.systemd_scheduler(2_000_000_000.0, command=["python", "due"], lock=lock)

    first = threading.Thread(target=arm_once, name="a")
    first.start()
    assert entered.wait(5), "the first arm never reached its fresh timer"
    second = threading.Thread(target=arm_once, name="b")
    second.start()
    time.sleep(0.2)                                  # b is contending for the lock
    assert {name for name, _ in log} == {"a"}, "b ran before the first arm released the lock"
    release.set()
    first.join(5)
    second.join(5)
    assert not first.is_alive() and not second.is_alive()
    names = [name for name, _ in log]
    assert set(names) == {"a", "b"}
    assert names == sorted(names)                    # every "a" call precedes every "b" call


def _listing(*rows):
    return "\n".join(rows) + "\n"


def _row(seconds_from_now, name):
    import time
    from datetime import datetime, timezone
    when = datetime.fromtimestamp(time.time() + seconds_from_now, timezone.utc)
    return f"{when:%a %Y-%m-%d %H:%M:%S} UTC {seconds_from_now}s - - {name}"


class TimerListing:
    """Answers list-timers with a canned listing and records every other call.

    ``env`` is the PYTHONPATH the pending timers' services report (default: this release's source).
    """

    def __init__(self, listing, env=None):
        self.listing = listing
        self.env = env if env is not None else "PYTHONPATH=" + arm._source_root()
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)

        class Done:
            returncode = 0
            stderr = b""
        done = Done()
        if argv[:3] == ["systemctl", "--user", "list-timers"]:
            done.stdout = self.listing
        elif argv[:4] == ["systemctl", "--user", "list-units", "--type=timer"]:
            done.stdout = "".join(line.split()[-1] + " loaded active waiting\n"
                                  for line in self.listing.splitlines() if line.strip())
        elif argv[:3] == ["systemctl", "--user", "show"] and "Triggers" in argv:
            done.stdout = argv[3].replace(".timer", ".service")
        elif argv[:3] == ["systemctl", "--user", "show"] and "Environment" in argv:
            done.stdout = self.env
        else:
            done.stdout = ""
        return done


def test_overdue_target_keeps_a_retry_already_due_in_the_window(monkeypatch):
    import time
    fake = TimerListing(_listing(_row(60, "mishe-next-due-aaaa1111.timer")))
    monkeypatch.setattr(arm.subprocess, "run", fake)
    arm.systemd_scheduler(time.time() - 3600, command=["python", "due"])
    assert not any(c[:3] == ["systemctl", "--user", "stop"] for c in fake.calls)
    assert not any(c[0] == "systemd-run" for c in fake.calls)


def test_overdue_target_replaces_an_in_window_retry_armed_by_an_older_release(monkeypatch):
    # The c14bac1f move observe failed on this: a retry due in the window, armed under the old pin, was kept.
    import time
    fake = TimerListing(_listing(_row(60, "mishe-next-due-9bb6d573d265.timer")),
                        env="PYTHONPATH=/home/x/releases/422912216/src MISHE_WORKLOAD_SLICE=mishe-plants.slice")
    monkeypatch.setattr(arm.subprocess, "run", fake)
    arm.systemd_scheduler(time.time() - 3600, command=["python", "due"])
    assert ["systemctl", "--user", "stop", "mishe-next-due-9bb6d573d265.timer"] in fake.calls
    assert any(c[0] == "systemd-run" for c in fake.calls)


def test_overdue_target_replaces_a_retry_later_than_the_window(monkeypatch):
    import time
    fake = TimerListing(_listing(_row(3000, "mishe-next-due-aaaa1111.timer")))
    monkeypatch.setattr(arm.subprocess, "run", fake)
    arm.systemd_scheduler(time.time() - 3600, command=["python", "due"])
    assert ["systemctl", "--user", "stop", "mishe-next-due-aaaa1111.timer"] in fake.calls
    assert any(c[0] == "systemd-run" for c in fake.calls)


def test_unparseable_listing_falls_back_to_replacement(monkeypatch):
    import time
    fake = TimerListing(_listing("n/a n/a n/a n/a mishe-next-due-aaaa1111.timer"))
    monkeypatch.setattr(arm.subprocess, "run", fake)
    arm.systemd_scheduler(time.time() - 3600, command=["python", "due"])
    assert any(c[0] == "systemd-run" for c in fake.calls)


def test_future_target_is_never_held_back(monkeypatch):
    import time
    fake = TimerListing(_listing(_row(30, "mishe-next-due-aaaa1111.timer")))
    monkeypatch.setattr(arm.subprocess, "run", fake)
    arm.systemd_scheduler(time.time() + 600, command=["python", "due"])
    assert any(c[0] == "systemd-run" for c in fake.calls)
