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

from .dispatch_record import receipt_is_held
from .dispatch_record import snapshot_is_unhealthy
from .dispatch_record import receipt_is_unknown
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
    """Run one canonical turn for one role and settle it.

    The worker owns the whole canonical round trip for a single wake:

    1. ask the canonical tick for a wake (it creates one only when the site
       owes this role a turn, and it refuses when the mind is busy);
    2. run the turn's command with its own bound;
    3. settle the exact wake with the canonical settlement, which writes the
       wall, the handoff and the tape receipt.

    The wake number is the durable key.  When the site owes this role no
    turn, the worker reports ``skipped`` and no wake exists: nothing was
    due, so nothing is claimed.
    """

    def __init__(self, *, home: str | Path, session: str, role: str,
                 notes_file: str | Path, result: str = "verified",
                 continue_task: bool = False, timeout: float = 900.0,
                 cwd: str | Path | None = None):
        self.home = Path(home)
        self.session = session
        self.role = role
        self.notes_file = Path(notes_file)
        if result not in {"changed", "verified", "blocked", "unspecified"}:
            raise ValueError("result must be changed, verified, blocked or unspecified")
        self.result = result
        self.continue_task = continue_task
        self.timeout = timeout
        self.cwd = Path(cwd) if cwd is not None else self.home.parent

    def run(self, dispatch: Dispatch) -> WorkerResult:
        from .. import seed
        from ..observations import validate_slug
        validate_slug(self.role)
        if dispatch.role != self.role:
            # The record would name one role while the effect belongs to
            # another.  That is worse than a refusal: the durable state
            # would claim a turn finished that never happened.
            return WorkerResult(
                outcome="failed",
                detail=f"this worker acts for role {self.role!r}, "
                       f"not {dispatch.role!r}")
        command = dispatch.payload.get("command")
        if not isinstance(command, (list, tuple)) or not command:
            raise ValueError("dispatch payload must carry a nonempty command list")
        # The canonical tick decides whether a wake is owed.  It refuses when
        # the mind is busy or the pane is not observable, and it never creates
        # a second wake for a turn that is already pending.
        #
        # ``seed.tick`` is the canonical entry point, not ``wall.tick``: it is
        # the one that turns a stale or unreadable observation into an
        # UNKNOWN receipt instead of raising.  A stale dashboard must not
        # crash the supervisor, and it must not read as a pass.
        receipt = seed.tick(self.home, self.session, self.role,
                            float(dispatch.payload.get("self_pick_seconds", 0)))
        # A receipt that says the reading could not be trusted, or that the
        # plant is holding the wake, stops the turn whether or not a wake
        # is pending.  Acting on an unreadable observation would settle a
        # turn the plant could not confirm, and settling a held wake would
        # override a hold the plant itself raised.
        if receipt_is_unknown(receipt):
            # The observation was stale or unreadable.  The turn is not
            # known to be undone; the reading is UNKNOWN, not a pass.
            return WorkerResult(outcome="unknown", detail=receipt)
        if receipt_is_held(receipt):
            # The plant is holding this wake for reconciliation.  Settling it
            # would override a hold the plant itself raised, so the turn is
            # left alone and the record stays open.
            return WorkerResult(outcome="unknown", detail=receipt)
        # The wake number is read from the canonical decoded state, not from
        # the receipt's text: the receipt is a human-readable line, and the
        # plant's own state is what both the wake and the settlement check.
        wake = self._pending_wake()
        if wake is None:
            return WorkerResult(outcome="skipped", detail=f"no canonical wake was owed: {receipt}")
        payload = dict(dispatch.payload)
        payload["wake"] = wake
        from .worker import SubprocessWorker
        worker = SubprocessWorker(timeout=self.timeout, cwd=self.cwd)
        outcome = worker.run(Dispatch(role=self.role, key=dispatch.key, payload=payload))
        if outcome.outcome == "unknown":
            # The worker's effect is not established.  That outcome is kept:
            # a "failed" record is not open, so it would let the next
            # dispatch run the same work again on the same wake.
            return outcome
        if not outcome.ok:
            # The turn's command failed outright and had no effect, so no
            # settlement is written: the wake stays pending and a successor
            # reconciles it rather than settling a failed turn.
            return WorkerResult(outcome="failed", detail=outcome.detail)
        # From here the command has already run.  A missing notes file or a
        # refused settlement is therefore an unknown effect, not an
        # established absence of one: the turn may have done its work, and
        # the wake stays pending.  Reporting "failed" would let the next
        # dispatch drive the same wake again.
        notes = self.notes_file
        if not notes.is_file():
            return WorkerResult(outcome="unknown",
                                detail=f"settlement notes missing after the turn ran: {notes}")
        try:
            settled = seed.yield_wake(self.home, self.role, wake, notes,
                                      self.continue_task, self.result)
        except (OSError, ValueError) as exc:
            return WorkerResult(outcome="unknown",
                                detail=f"settlement refused after the turn ran: {exc}")
        return WorkerResult(outcome="completed", detail=settled)

    def _pending_wake(self) -> int | None:
        """The wake this role currently owes, read from the canonical state.

        A state that cannot be read is not the same as a state that says
        there is nothing owed.  The first is unknown; the second is a
        skipped turn.
        """
        from .. import seed
        return seed._state(self.home, self.role)[1]


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

