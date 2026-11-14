from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from mishe_tauftauf import owner_routing as orr


def _registry() -> dict:
    return {
        "schema": orr.SCHEMA,
        "signals": {"ci": {"resolver": "genome", "subscribers": ["genome"], "max_age_s": 900.0}},
        "projects": {"research.md": "field-review"},
        "retired_roles": {"witness": "health"},
    }


class OwnerRoutingTests(unittest.TestCase):
    def test_signal_project_and_role_are_decided_by_lookup(self):
        reg = _registry()
        self.assertEqual(orr.route(reg, "signal", "ci").owner, "genome")
        self.assertEqual(orr.route(reg, "project", "research.md").owner, "field-review")
        self.assertEqual(orr.route(reg, "role", "witness").owner, "health")

    def test_unknown_key_is_residue_not_a_guess(self):
        decision = orr.route(_registry(), "signal", "no-such-signal")
        self.assertFalse(decision.decided)
        self.assertEqual(decision.source, "residue")

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            orr.route(_registry(), "path", "docs/mesh.md")

    def test_missing_or_malformed_registry_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with self.assertRaises(orr.RegistryError):
                orr.load(home)
            (home / "system1").mkdir()
            (home / "system1" / orr.REGISTRY_NAME).write_text(json.dumps({"schema": "other"}))
            with self.assertRaises(orr.RegistryError):
                orr.load(home)

    def test_loads_valid_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "system1").mkdir()
            (home / "system1" / orr.REGISTRY_NAME).write_text(json.dumps(_registry()))
            self.assertEqual(orr.route(orr.load(home), "signal", "ci").owner, "genome")


if __name__ == "__main__":
    unittest.main()
