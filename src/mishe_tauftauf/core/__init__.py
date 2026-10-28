"""The minimal Mishe life-support core.

This package holds the small, generic pieces a supervisor needs: a role
registry, a durable dispatch record, a bounded worker runner and the
observation that says whether the result can be trusted.

It deliberately contains no Mishe protocol.  Wakes, settlements, the shared
tape, walls and the host guard are reached only through the adapters in
:mod:`mishe_tauftauf.core.adapters`, and the generic modules import nothing
from the rest of the package at module scope.  A caller that only wants the
scheduling and state interfaces can import this package without pulling in
the tape parser, the tmux layer or the provider boundary.

See ``README.md`` in this directory for the boundary contract.
"""
from __future__ import annotations

from .interfaces import (Dispatch, DispatchState, Observation, RoleRegistry,
                         Worker, WorkerResult)
from .registry import StaticRoleRegistry, check_registry
from .state import DispatchRecord, DispatchStore, DispatchStoreError
from .supervisor import Supervisor, SupervisorError
from .worker import SubprocessWorker, WorkerTimeout

__all__ = [
    "Dispatch",
    "DispatchRecord",
    "DispatchState",
    "DispatchStore",
    "DispatchStoreError",
    "Observation",
    "RoleRegistry",
    "StaticRoleRegistry",
    "SubprocessWorker",
    "Supervisor",
    "SupervisorError",
    "Worker",
    "WorkerResult",
    "WorkerTimeout",
    "check_registry",
]
