"""Generic interfaces the core defines and its adapters implement.

Nothing here imports the Mishe package or the standard library's heavier
subsystems.  A concrete adapter (canonical wake, host guard, observation
source) satisfies these protocols; the core never needs to know which
implementation it is talking to.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Protocol, Sequence, runtime_checkable


#: The states a dispatch can hold.
STATE_NAMES = ("pending", "started", "completed", "failed", "skipped", "unknown", "reconciled")


@dataclass(frozen=True)
class DispatchState:
    """The durable outcome of one dispatch attempt.

    ``unknown`` is the one that matters: a worker that started and produced
    no outcome may have had an effect, so the dispatch cannot be replayed and
    cannot be called successful.
    """

    name: str

    def __post_init__(self) -> None:
        if self.name not in STATE_NAMES:
            raise ValueError(f"unknown dispatch state {self.name!r}")

    def __str__(self) -> str:
        return self.name


PENDING = DispatchState("pending")
STARTED = DispatchState("started")
COMPLETED = DispatchState("completed")
FAILED = DispatchState("failed")
SKIPPED = DispatchState("skipped")
UNKNOWN = DispatchState("unknown")
RECONCILED = DispatchState("reconciled")

#: States from which a dispatch may still be running or may have had an
#: effect whose outcome was not recorded.  A dispatch in one of these states
#: blocks a fresh dispatch: replaying it is how one effect becomes two.
OPEN_STATES = frozenset({STARTED, UNKNOWN})


@dataclass(frozen=True)
class Dispatch:
    """One unit of work handed to one role.

    ``key`` is the caller's durable identity for this work.  It is what a
    successor reads to decide whether the work already happened, so it must
    name the work rather than the attempt.
    """

    role: str
    key: str
    payload: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class WorkerResult:
    """What a worker reported back.

    ``outcome`` is one of:

    ``completed``
        the work ran and finished;
    ``failed``
        the work ran and did not finish;
    ``skipped``
        the site owed no turn, so no work was due;
    ``unknown``
        the work started and no outcome survived, so its effect is not known.

    ``detail`` is a short human-readable tail.  It never carries the evidence
    itself: evidence belongs in the durable record the adapter writes.

    ``pid`` is the operating-system pid of the worker, when it ran one.
    It is what a successor reads to find a worker that outlived its
    supervisor.
    """

    outcome: str
    detail: str = ""
    pid: int | None = None

    def __post_init__(self) -> None:
        if self.outcome not in {"completed", "failed", "skipped", "unknown"}:
            raise ValueError(f"unknown worker outcome {self.outcome!r}")
        if self.pid is not None and (isinstance(self.pid, bool) or not isinstance(self.pid, int)):
            raise ValueError("pid must be an integer or absent")

    @property
    def ok(self) -> bool:
        """Whether the work finished.  A skipped turn did not fail."""
        return self.outcome in {"completed", "skipped"}


@dataclass(frozen=True)
class Observation:
    """A reading of whether the current state can be trusted.

    ``fresh`` says the reading is current, not that it is healthy.  A stale or
    missing reading is UNKNOWN, never a pass: the distinction between "there
    is no evidence" and "the evidence says all is well" is the whole point.
    """

    fresh: bool
    healthy: bool
    detail: str = ""
    age_seconds: float | None = None


@runtime_checkable
class RoleRegistry(Protocol):
    """Which roles this supervisor may act for."""

    def roles(self) -> Sequence[str]:
        """The declared roles, in a stable order."""

    def knows(self, role: str) -> bool:
        """Whether ``role`` is declared and may be dispatched."""


@runtime_checkable
class Worker(Protocol):
    """Executes one dispatch and reports what happened.

    A worker that cannot finish within its own bound must report an unknown
    effect rather than a clean failure, so the caller reconciles instead of
    replaying.
    """

    def run(self, dispatch: Dispatch) -> WorkerResult:
        """Run ``dispatch`` and report the outcome."""
