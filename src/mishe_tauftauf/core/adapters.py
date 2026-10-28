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
        # The wake number is read from the canonical decoded state, not from
        # the receipt's text: the receipt is a human-readable line, and the
        # plant's own state is what both the wake and the settlement check.
        wake = self._pending_wake()
        if wake is None:
            if receipt_is_unknown(receipt):
                # The observation was stale or unreadable.  The turn is not
                # known to be undone; the reading is UNKNOWN, not a pass.
                return WorkerResult(outcome="unknown", detail=receipt)
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
        notes = self.notes_file
        if not notes.is_file():
            return WorkerResult(outcome="failed", detail=f"settlement notes missing: {notes}")
        try:
            settled = seed.yield_wake(self.home, self.role, wake, notes,
                                      self.continue_task, self.result)
        except (OSError, ValueError) as exc:
            return WorkerResult(outcome="failed", detail=f"settlement refused: {exc}")
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
    says all is well".
    """

    def __init__(self, *, home: str | Path, role: str):
        self.home = Path(home)
        self.role = role

    def __call__(self) -> Observation:
        from .. import seed
        try:
            digest, pending, last_yield, last_clear, _, _, _, _ = seed._state(self.home, self.role)
        except (OSError, ValueError) as exc:
            return Observation(fresh=False, healthy=False,
                               detail=f"canonical state unreadable: {exc}")
        detail = (f"pending={pending if pending is not None else 'none'} "
                  f"yield={last_yield if last_yield is not None else 'none'} "
                  f"clear={last_clear if last_clear is not None else 'none'}")
        if digest is None:
            # The role has no observation record.  The plant's own convention
            # writes "UNKNOWN -- no sensor observation" in the same case.
            # There is no evidence here, so this is not a pass.
            return Observation(fresh=False, healthy=False,
                               detail=f"no observation for role {self.role}; " + detail)
        return Observation(fresh=True, healthy=True, detail=detail)
