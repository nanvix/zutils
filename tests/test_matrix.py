# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.matrix."""

import unittest

from nanvix_zutil.manifest import BuildMatrix
from nanvix_zutil.matrix import (
    BuildCombo,
    BuildResult,
    expand_matrix,
    filter_matrix,
)


def _make_matrix(
    platforms: list[str],
    modes: list[str],
    memory: list[str],
    exclude: list[dict[str, str]] | None = None,
) -> BuildMatrix:
    """Helper to build a :class:`BuildMatrix` for tests."""
    return BuildMatrix(
        dimensions={
            "platforms": platforms,
            "modes": modes,
            "memory": memory,
        },
        exclude=exclude or [],
    )


class TestExpandMatrix(unittest.TestCase):
    """Tests for :func:`expand_matrix`."""

    def test_simple_cross_product(self) -> None:
        """2 platforms × 2 modes × 1 memory = 4 combos."""
        matrix = _make_matrix(
            platforms=["hyperlight", "microvm"],
            modes=["multi-process", "standalone"],
            memory=["128mb"],
        )

        combos = expand_matrix(matrix)

        self.assertEqual(len(combos), 4)
        # Verify all expected combinations are present.
        expected = {
            BuildCombo(platform="hyperlight", mode="multi-process", memory="128mb"),
            BuildCombo(platform="hyperlight", mode="standalone", memory="128mb"),
            BuildCombo(platform="microvm", mode="multi-process", memory="128mb"),
            BuildCombo(platform="microvm", mode="standalone", memory="128mb"),
        }
        self.assertEqual(set(combos), expected)

    def test_single_dimension(self) -> None:
        """1 × 1 × 1 = 1 combo."""
        matrix = _make_matrix(
            platforms=["hyperlight"],
            modes=["multi-process"],
            memory=["128mb"],
        )

        combos = expand_matrix(matrix)

        self.assertEqual(len(combos), 1)
        self.assertEqual(
            combos[0],
            BuildCombo(platform="hyperlight", mode="multi-process", memory="128mb"),
        )

    def test_excludes_remove_matching_combos(self) -> None:
        """Exact exclude removes only the specified combo."""
        matrix = _make_matrix(
            platforms=["hyperlight", "microvm"],
            modes=["multi-process", "standalone"],
            memory=["128mb"],
            exclude=[{"platform": "hyperlight", "mode": "standalone"}],
        )

        combos = expand_matrix(matrix)

        # Should have 3 combos: the hyperlight/standalone one is excluded.
        self.assertEqual(len(combos), 3)
        excluded = BuildCombo(platform="hyperlight", mode="standalone", memory="128mb")
        self.assertNotIn(excluded, combos)

    def test_exclude_partial_match(self) -> None:
        """Exclude with only platform key removes all combos for that platform."""
        matrix = _make_matrix(
            platforms=["hyperlight", "microvm"],
            modes=["multi-process", "standalone"],
            memory=["128mb"],
            exclude=[{"platform": "hyperlight"}],
        )

        combos = expand_matrix(matrix)

        # All hyperlight combos removed → only 2 microvm combos remain.
        self.assertEqual(len(combos), 2)
        for combo in combos:
            self.assertNotEqual(combo.platform, "hyperlight")

    def test_no_excludes(self) -> None:
        """Without excludes the full cross-product is returned."""
        matrix = _make_matrix(
            platforms=["hyperlight", "microvm"],
            modes=["multi-process"],
            memory=["128mb", "256mb"],
        )

        combos = expand_matrix(matrix)

        # 2 × 1 × 2 = 4.
        self.assertEqual(len(combos), 4)


class TestFilterMatrix(unittest.TestCase):
    """Tests for :func:`filter_matrix`."""

    def setUp(self) -> None:
        self._combos = [
            BuildCombo(platform="hyperlight", mode="multi-process", memory="128mb"),
            BuildCombo(platform="hyperlight", mode="standalone", memory="128mb"),
            BuildCombo(platform="microvm", mode="multi-process", memory="128mb"),
        ]

    def test_filter_by_mode(self) -> None:
        """Only combos with the given mode are returned."""
        result = filter_matrix(self._combos, mode="multi-process")

        self.assertEqual(len(result), 2)
        for combo in result:
            self.assertEqual(combo.mode, "multi-process")

    def test_filter_no_match(self) -> None:
        """A mode that matches nothing returns an empty list."""
        result = filter_matrix(self._combos, mode="single-process")

        self.assertEqual(result, [])

    def test_filter_none_returns_all(self) -> None:
        """mode=None returns the full list unchanged."""
        result = filter_matrix(self._combos, mode=None)

        self.assertEqual(result, self._combos)


class TestBuildCombo(unittest.TestCase):
    """Tests for :class:`BuildCombo`."""

    def test_frozen(self) -> None:
        """BuildCombo is immutable — attribute assignment must raise."""
        combo = BuildCombo(platform="hyperlight", mode="multi-process", memory="128mb")

        with self.assertRaises((AttributeError, TypeError)):
            combo.platform = "microvm"  # type: ignore[misc]

    def test_equality(self) -> None:
        """Two combos with identical fields compare equal."""
        a = BuildCombo(platform="hyperlight", mode="multi-process", memory="128mb")
        b = BuildCombo(platform="hyperlight", mode="multi-process", memory="128mb")

        self.assertEqual(a, b)

    def test_hashable(self) -> None:
        """BuildCombo can be used as a dictionary key."""
        combo = BuildCombo(platform="hyperlight", mode="multi-process", memory="128mb")
        results: dict[BuildCombo, str] = {combo: "ok"}

        self.assertEqual(results[combo], "ok")


class TestBuildResult(unittest.TestCase):
    """Tests for :class:`BuildResult`."""

    def test_success_result(self) -> None:
        """A successful result has success=True and error=None."""
        combo = BuildCombo(platform="hyperlight", mode="multi-process", memory="128mb")
        result = BuildResult(combo=combo, success=True, duration_seconds=1.5)

        self.assertTrue(result.success)
        self.assertIsNone(result.error)
        self.assertEqual(result.duration_seconds, 1.5)
        self.assertIs(result.combo, combo)

    def test_failure_result(self) -> None:
        """A failed result has success=False and a non-None error message."""
        combo = BuildCombo(platform="microvm", mode="standalone", memory="256mb")
        result = BuildResult(
            combo=combo,
            success=False,
            duration_seconds=0.3,
            error="build failed",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error, "build failed")
        self.assertEqual(result.duration_seconds, 0.3)


if __name__ == "__main__":
    unittest.main()
