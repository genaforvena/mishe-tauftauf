"""Adapters that connect the core to Mishe's canonical machinery.

Every import of the existing stack happens inside a function here, so the
core's generic modules stay free of the tape parser, the wall writer, the
tmux layer and the effect ledger.  A caller that uses only the core's
scheduling and state interfaces never loads them.

Three adapters are provided:

``CanonicalRoleRegistry``
    reads the site's own declared roles;
``CanonicalWorker``
    runs one canonical wake to a canonical settlement for one role;
``CanonicalObservation``
    reads whether the canonical state is currently trustworthy.

The protocol vocabulary is the plant's, not this package's: a wake is
created by ``wall.tick``, a turn is settled by ``seed.yield_wake``, and a
turn is cleared by ``wall.clear``.  This package adds no second protocol.
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .dispatch_record import decode_tick_receipt
from .dispatch_record import snapshot_is_unhealthy
from .interfaces import Dispatch, Observation, WorkerResult
from .registry import StaticRoleRegistry


class CanonicalRoleRegistry:
    """The roles this site declares, read from the site's own registry.

    The site decides what "declared" means — the built-in role set plus any
    resident that shipped a charter and a launcher.  This adapter only reads
    that decision; it never adds a role of its own.
    """

    def __init__(self, home: str | Path):
        self.home = Path(home)

    def roles(self) -> Sequence[str]:
        from ..activity import roles as declared_roles
        return tuple(sorted(declared_roles(self.home)))

    def knows(self, role: str) -> bool:
        return role in self.roles()

    def registry(self) -> StaticRoleRegistry:
        return StaticRoleRegistry(self.roles())


class CanonicalWorker:
    """Ask the canonical tick to deliver a new wake and observe its settlement.

    The role's resident mind does the work and settles its own wake.  The
    dispatch lock and STARTED record span tick, delivery and settlement.  An
    uncertain delivery or missing settlement stays open for reconciliation.
    """

    def __init__(self, *, home: str | Path, session: str, role: str,
                 timeout: float = 900.0):
        import math
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        self.home = Path(home)
        self.session = session
        self.role = role
        self.timeout = timeout

    def reconcile_check(self, record) -> None:
        """Do not close an unknown while its resident mind may still run."""
        from .. import seed
        from ..tmux import owns_session
        if record.role != self.role:
            raise ValueError("reconciliation worker does not match the dispatch role")
        if not owns_session(self.home, self.session) or not seed._mind_idle(self.session, self.role):
            raise ValueError("canonical mind is not observably idle; do not reconcile")
        _, pending, settled, cleared, *_ = seed._state(self.home, self.role)
        if pending is None and settled is not None and settled != cleared:
            raise ValueError("settled wake is not cleared; reconcile its canonical idle rotation first")

    def run(self, dispatch: Dispatch) -> WorkerResult:
        import time
        from .. import seed
        from ..chat_protocol import decode_lifecycle
        from ..feed import Feed
        from ..observations import validate_slug

        validate_slug(self.role)
        if dispatch.role != self.role:
            return WorkerResult(outcome="failed",
                                detail=f"this worker acts for role {self.role!r}, "
                                       f"not {dispatch.role!r}")
        feed = Feed(self.home)
        entries = feed.role_entries(self.role)
        if seed._state(self.home, self.role, entries=entries)[1] is not None:
            return WorkerResult(outcome="unknown",
                                detail="an existing canonical wake requires reconciliation")
        before = max((entry.sequence for entry in entries), default=0)
        receipt = seed.tick(self.home, self.session, self.role,
                            float(dispatch.payload.get("self_pick_seconds", 0)),
                            new_only=True)
        status, wake = decode_tick_receipt(receipt, self.role)
        if status == "unknown":
            return WorkerResult(outcome="unknown", detail=f"tick not confirmed: {receipt}")
        if status == "skipped":
            return WorkerResult(outcome="skipped", detail=receipt)
        if wake is None or wake <= before:
            return WorkerResult(outcome="unknown", detail=f"tick named an earlier wake: {receipt}")

        # Persist the canonical identity before waiting.  A crash before this
        # write still leaves STARTED, which blocks every subsequent dispatch.
        detail = f"canonical wake {wake}"
        publish = dispatch.payload.get("publish_detail")
        if callable(publish):
            publish(detail)
        deadline = time.monotonic() + self.timeout
        while True:
            entries = feed.role_entries(self.role)
            events = [(entry.sequence, decode_lifecycle(entry)) for entry in entries
                      if entry.sequence > before]
            wakes = [sequence for sequence, event in events
                     if event is not None and event.role == self.role and event.kind == "wake"]
            if wakes != [wake]:
                return WorkerResult(outcome="unknown",
                                    detail=f"{detail}: canonical wake history is ambiguous")
            if any(event is not None and event.role == self.role and event.kind == "yield"
                   and event.wake == wake for _, event in events):
                _, pending, settled, cleared, *_ = seed._state(self.home, self.role, entries=entries)
                if pending is not None or settled != wake:
                    return WorkerResult(outcome="unknown",
                                        detail=f"{detail}: settlement state changed; reconcile")
                if cleared == wake:
                    return WorkerResult(outcome="completed",
                                        detail=f"{detail}: canonical settlement and idle clear observed")
                if seed._clear_due(self.home, self.role, 15):
                    receipt = seed.clear(self.home, self.session, self.role, expected_wake=wake)
                    _, pending, settled, cleared, *_ = seed._state(self.home, self.role)
                    if pending is None and settled == wake and cleared == wake:
                        return WorkerResult(outcome="completed",
                                            detail=f"{detail}: canonical settlement and idle clear observed")
                    if receipt != f"held seed {self.role} mind busy":
                        return WorkerResult(outcome="unknown",
                                            detail=f"{detail}: idle clear unconfirmed: {receipt}")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return WorkerResult(outcome="unknown",
                                    detail=f"{detail}: settlement or idle clear not observed before timeout")
            time.sleep(min(1.0, remaining))



class CanonicalLiveObservation:
    """Read the current live renderer without manufacturing a feed event."""

    def __init__(self, *, home: str | Path, session: str, role: str):
        self.home = Path(home)
        self.session = session
        self.role = role

    def __call__(self) -> Observation:
        from .. import seed, wall
        from ..tmux import TmuxError
        try:
            with seed._lock(self.home):
                sensor, ok = wall._live_sensor(self.home, self.session, self.role)
                meaningful = wall.observation_text(self.role, sensor)
        except (OSError, ValueError, TmuxError) as exc:
            return Observation(fresh=False, healthy=False, detail=f"live sensor unreadable: {exc}")
        if not ok or wall._sensor_unknown(meaningful):
            return Observation(fresh=False, healthy=False,
                               detail=f"live sensor is UNKNOWN: {meaningful[:200]}")
        if wall._sensor_failed(meaningful, ok):
            return Observation(fresh=True, healthy=False, detail=f"live sensor reports a problem: {meaningful[:200]}")
        return Observation(fresh=True, healthy=True, detail="live sensor is healthy")


class CanonicalObservation:
    """Read whether the canonical state is currently trustworthy.

    A missing or unreadable reading is UNKNOWN, never healthy: the point of
    the reading is to distinguish "there is no evidence" from "the evidence
    says all is well".  A record that exists but carries no evidence of
    health — the plant's own "no sensor observation" record, or a sensor
    that reported RED — is not a pass either.
    """

    def __init__(self, *, home: str | Path, role: str, max_age_seconds: float = 30.0):
        self.home = Path(home)
        self.role = role
        self.max_age_seconds = max_age_seconds

    def __call__(self) -> Observation:
        from .. import seed
        try:
            digest, pending, last_yield, last_clear, observation, _, _, _ = \
                seed._state(self.home, self.role)
        except (OSError, ValueError) as exc:
            return Observation(fresh=False, healthy=False,
                               detail=f"canonical state unreadable: {exc}")
        detail = (f"pending={pending if pending is not None else 'none'} "
                  f"yield={last_yield if last_yield is not None else 'none'} "
                  f"clear={last_clear if last_clear is not None else 'none'}")
        if digest is None or observation is None:
            # The role has no observation record.  The plant's own convention
            # writes "UNKNOWN -- no sensor observation" in the same case.
            # There is no evidence here, so this is not a pass.
            return Observation(fresh=False, healthy=False,
                               detail=f"no observation for role {self.role}; " + detail)
        record, age = self._record(observation)
        if record is None:
            return Observation(fresh=False, healthy=False,
                               detail=f"the observation record for {self.role} is unreadable; " + detail)
        if age is None:
            return Observation(fresh=False, healthy=False,
                               detail=f"the observation for {self.role} has no readable age; " + detail)
        if age < 0 or age > self.max_age_seconds:
            # An observation that is stale or dated in the future is not
            # evidence about now.  The plant's own dashboard reader refuses
            # the same readings.
            return Observation(fresh=False, healthy=False, age_seconds=age,
                               detail=f"the observation for {self.role} is stale "
                                      f"({age:.0f}s old); " + detail)
        snapshot = record.get("snapshot")
        command_ok = record.get("command_ok")
        text = snapshot if isinstance(snapshot, str) else ""
        if command_ok is not True:
            # The plant's own record says the sensor command did not succeed.
            # A record that exists is not a record that says all is well.
            return Observation(fresh=False, healthy=False, age_seconds=age,
                               detail=f"the observation for {self.role} did not succeed: {text[:200]}")
        if snapshot_is_unhealthy(text):
            return Observation(fresh=True, healthy=False, age_seconds=age,
                               detail=f"the observation for {self.role} reports a problem: {text[:200]}")
        return Observation(fresh=True, healthy=True, age_seconds=age, detail=detail)

    def _record(self, sequence: int) -> tuple[dict | None, float | None]:
        """The observation record's own payload and its age in seconds.

        The age comes from the feed entry's timestamp, which is when the
        record was published.  A record whose age cannot be read is not
        evidence about now.
        """
        from ..feed import Feed
        from ..records import payload
        try:
            entries = Feed(self.home).entries(start=sequence, limit=1)
        except (OSError, ValueError):
            return None, None
        for entry in entries:
            if entry.sequence != sequence:
                continue
            try:
                data = payload(entry)
            except (OSError, ValueError, KeyError, TypeError):
                return None, None
            if not isinstance(data, dict):
                return None, None
            return data, self._age(entry.timestamp)
        return None, None

    @staticmethod
    def _age(timestamp: str) -> float | None:
        """How old a published entry is, in seconds."""
        from .dispatch_record import entry_age_seconds
        return entry_age_seconds(timestamp)

