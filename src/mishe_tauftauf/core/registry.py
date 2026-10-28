"""A role registry the core can check without knowing how roles are declared."""
from __future__ import annotations

from typing import Iterable, Sequence

from .interfaces import RoleRegistry


class StaticRoleRegistry:
    """Roles declared up front, in a stable order.

    The core itself declares no roles.  An adapter that reads the site's own
    registry hands the resulting set to this class, so the core's rule stays
    "only a declared role may be dispatched" while the site keeps ownership
    of what "declared" means.
    """

    def __init__(self, roles: Iterable[str]):
        declared: list[str] = []
        for role in roles:
            if not isinstance(role, str) or not role:
                raise ValueError("a declared role must be a nonempty string")
            if role in declared:
                raise ValueError(f"duplicate declared role {role!r}")
            declared.append(role)
        self._roles = tuple(declared)

    def roles(self) -> Sequence[str]:
        return self._roles

    def knows(self, role: str) -> bool:
        return role in self._roles

    def __len__(self) -> int:
        return len(self._roles)

    def __repr__(self) -> str:
        return f"StaticRoleRegistry({list(self._roles)!r})"


def check_registry(registry: RoleRegistry) -> None:
    """Fail closed when a registry does not satisfy the core's contract.

    A registry that raises, or that reports a role it cannot dispatch, would
    turn "only declared roles may be dispatched" into an unchecked claim.
    """
    roles = registry.roles()
    if not isinstance(roles, (list, tuple)):
        raise ValueError("a role registry must return a sequence of roles")
    for role in roles:
        if not isinstance(role, str) or not role:
            raise ValueError("a declared role must be a nonempty string")
        if not registry.knows(role):
            raise ValueError(f"registry reports role {role!r} but does not know it")
