from __future__ import annotations

import math
import unittest

from mishe_tauftauf import jev_judge as jev


class DirichletCalibrationTests(unittest.TestCase):
    def test_vocabulary_names_source_and_version(self) -> None:
        self.assertEqual(jev.DIRICHLET_CALIBRATION_VERSION, 1)
        self.assertIn("1910.12656", jev.DIRICHLET_LINEAR_MAP_DOC + jev.CLASSWISE_ECE_DEFINITION + jev.TEMPERATURE_AS_DIRICHLET_DOC)
        self.assertIn("classwise", jev.CLASSWISE_ECE_DEFINITION.lower())
        self.assertIn("2.2e-308", jev.DIRICHLET_LINEAR_MAP_DOC)
        self.assertIn("1/t", jev.TEMPERATURE_AS_DIRICHLET_DOC)
        self.assertIn("identity", jev.TEMPERATURE_AS_DIRICHLET_DOC.lower())

    def test_log_transform_clips_zero_and_rejects_bad_input(self) -> None:
        out = jev.dirichlet_log_transform({"yes": 0.8, "no": 0.2})
        self.assertAlmostEqual(out["yes"], math.log(0.8))
        self.assertIsNotNone(jev.dirichlet_log_transform({"yes": 0.0, "no": 1.0}))
        self.assertTrue(math.isfinite(jev.dirichlet_log_transform({"yes": 0.0, "no": 1.0})["yes"]))
        self.assertIsNone(jev.dirichlet_log_transform({}))
        self.assertIsNone(jev.dirichlet_log_transform({"yes": float("nan"), "no": 0.5}))
        self.assertIsNone(jev.dirichlet_log_transform({"yes": 1.5, "no": 0.5}))
        self.assertIsNone(jev.dirichlet_log_transform({"yes": True, "no": 0.5}))

    def test_identity_map_returns_input(self) -> None:
        probs = {"yes": 0.8, "no": 0.2}
        out = jev.dirichlet_linear_map(probs, [[1, 0], [0, 1]], [0, 0])
        self.assertAlmostEqual(out["yes"], 0.8)
        self.assertAlmostEqual(out["no"], 0.2)
        self.assertAlmostEqual(sum(out.values()), 1.0)

    def test_temperature_is_diagonal_map(self) -> None:
        probs = {"yes": 0.7, "no": 0.3}
        t = 2.0
        temp = jev.dirichlet_linear_map(probs, [[1 / t, 0], [0, 1 / t]], [0, 0])
        expected_yes = probs["yes"] ** (1 / t) / (probs["yes"] ** (1 / t) + probs["no"] ** (1 / t))
        self.assertAlmostEqual(temp["yes"], expected_yes)

    def test_linear_map_rejects_shape_and_empty(self) -> None:
        self.assertIsNone(jev.dirichlet_linear_map({}, [[1, 0], [0, 1]], [0, 0]))
        self.assertIsNone(jev.dirichlet_linear_map({"yes": 0.8, "no": 0.2}, [[1]], [0]))
        self.assertIsNone(jev.dirichlet_linear_map({"yes": 0.8, "no": 0.2}, [[1, 0], [0, 1]], [0]))
        self.assertIsNone(jev.dirichlet_linear_map({"yes": 0.8, "no": 0.2}, [[float("inf"), 0], [0, 1]], [0, 0]))

    def test_canonical_params_nonnegative_and_c_is_distribution(self) -> None:
        W = [[0.5, 0.2], [0.1, 0.5]]
        b = [0.1, -0.1]
        A, c = jev.canonical_params(W, b)
        self.assertTrue(all(v >= -1e-12 for row in A for v in row))
        for j in range(2):
            self.assertAlmostEqual(min(A[i][j] for i in range(2)), 0.0)
        self.assertAlmostEqual(sum(c), 1.0)
        self.assertIsNone(jev.canonical_params([[1]], [0, 0]))
        self.assertIsNone(jev.canonical_params([], []))

    def test_classwise_ece_perfect_and_invalid(self) -> None:
        perfect = [{"yes": 1.0, "no": 0.0}, {"yes": 0.0, "no": 1.0}]
        self.assertAlmostEqual(jev.classwise_ece(perfect, ["yes", "no"], bins=2), 0.0)
        ps = [{"yes": 0.9, "no": 0.1}, {"yes": 0.6, "no": 0.4}, {"yes": 0.51, "no": 0.49}]
        e = jev.classwise_ece(ps, ["yes", "yes", "no"], bins=2)
        self.assertIsNotNone(e)
        self.assertTrue(0.0 <= e <= 1.0)
        self.assertIsNone(jev.classwise_ece([], [], bins=15))
        self.assertIsNone(jev.classwise_ece(ps, ["yes"], bins=2))
        self.assertIsNone(jev.classwise_ece(ps, ["yes", "yes", "maybe"], bins=2))
        self.assertIsNone(jev.classwise_ece(ps, ["yes", "yes", "no"], bins=0))


if __name__ == "__main__":
    unittest.main()
