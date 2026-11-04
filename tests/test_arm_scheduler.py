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
