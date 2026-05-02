# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.matrix."""

import unittest
from pathlib import Path

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

    def test_used_fallback_defaults_false(self) -> None:
        """used_fallback defaults to False when not specified."""
        combo = BuildCombo(platform="microvm", mode="standalone", memory="256mb")
        result = BuildResult(combo=combo, success=True, duration_seconds=1.0)

        self.assertFalse(result.used_fallback)


class TestRunAllBuilds(unittest.TestCase):
    """Tests for :func:`run_all_builds`."""

    def setUp(self) -> None:
        import tempfile

        from nanvix_zutil.script import ZScript

        from tests.testutils import write_manifest

        self._tmpdir = tempfile.mkdtemp()
        self._repo_root = Path(self._tmpdir)
        write_manifest(self._repo_root)

        # A minimal ZScript subclass whose hooks just record they were called.
        class _Stub(ZScript):
            hook_log: list[str] = []

            def build(self) -> None:
                _Stub.hook_log.append("build")

            def test(self) -> None:
                _Stub.hook_log.append("test")

            def clean(self) -> None:
                _Stub.hook_log.append("clean")

            def setup(self) -> bool:
                _Stub.hook_log.append("setup")
                return False

        self._stub_cls = _Stub
        _Stub.hook_log = []

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_all_combos_succeed(self) -> None:
        """run_all_builds returns a success result for every combo."""
        from nanvix_zutil.matrix import run_all_builds

        combos = [
            BuildCombo(platform="microvm", mode="standalone", memory="256mb"),
            BuildCombo(platform="microvm", mode="multi-process", memory="256mb"),
        ]

        results = run_all_builds(
            script_cls=self._stub_cls,
            combos=combos,
            hook="clean",
            targets=[],
            docker_image=None,
            repo_root=self._repo_root,
        )

        self.assertEqual(len(results), 2)
        for combo, result in results.items():
            self.assertTrue(result.success, f"{combo} should succeed")
            self.assertIsNone(result.error)
            self.assertGreaterEqual(result.duration_seconds, 0.0)

    def test_hook_chain_runs_prerequisites(self) -> None:
        """'build' runs setup then build via _HOOK_CHAIN."""
        from nanvix_zutil.matrix import run_all_builds

        combos = [
            BuildCombo(platform="microvm", mode="standalone", memory="256mb"),
        ]
        self._stub_cls.hook_log = []

        run_all_builds(
            script_cls=self._stub_cls,
            combos=combos,
            hook="build",
            targets=[],
            docker_image=None,
            repo_root=self._repo_root,
        )

        # _HOOK_CHAIN["build"] == ("setup", "build").
        self.assertEqual(self._stub_cls.hook_log, ["setup", "build"])

    def test_failing_hook_captured(self) -> None:
        """A SystemExit from a hook produces a failed BuildResult."""
        from nanvix_zutil.matrix import run_all_builds
        from nanvix_zutil.script import ZScript

        class _Failing(ZScript):
            def setup(self) -> bool:
                return False

            def build(self) -> None:
                raise SystemExit(5)

        combos = [
            BuildCombo(platform="microvm", mode="standalone", memory="256mb"),
        ]

        results = run_all_builds(
            script_cls=_Failing,
            combos=combos,
            hook="build",
            targets=[],
            docker_image=None,
            repo_root=self._repo_root,
        )

        result = results[combos[0]]
        self.assertFalse(result.success)
        self.assertIn("5", result.error or "")

    def test_env_vars_restored_after_run(self) -> None:
        """Environment variables are restored to their original values."""
        import os

        from nanvix_zutil.matrix import run_all_builds

        # Set known values before run.
        os.environ["NANVIX_MACHINE"] = "original_machine"
        os.environ["NANVIX_DEPLOYMENT_MODE"] = "original_mode"
        os.environ["NANVIX_MEMORY_SIZE"] = "original_mem"

        combos = [
            BuildCombo(platform="microvm", mode="standalone", memory="256mb"),
        ]

        run_all_builds(
            script_cls=self._stub_cls,
            combos=combos,
            hook="clean",
            targets=[],
            docker_image=None,
            repo_root=self._repo_root,
        )

        self.assertEqual(os.environ.get("NANVIX_MACHINE"), "original_machine")
        self.assertEqual(os.environ.get("NANVIX_DEPLOYMENT_MODE"), "original_mode")
        self.assertEqual(os.environ.get("NANVIX_MEMORY_SIZE"), "original_mem")

        # Clean up.
        os.environ.pop("NANVIX_MACHINE", None)
        os.environ.pop("NANVIX_DEPLOYMENT_MODE", None)
        os.environ.pop("NANVIX_MEMORY_SIZE", None)

    def test_parallel_combos_have_isolated_workspaces(self) -> None:
        """Each combo gets its own workspace copy, not the shared repo root."""
        from unittest.mock import patch

        from nanvix_zutil.matrix import run_all_builds
        from nanvix_zutil.script import ZScript

        instantiated_roots: list[Path] = []
        original_init = ZScript.__init__

        def tracking_init(
            self_inner: ZScript, repo_root: Path, *a: object, **kw: object
        ) -> None:
            instantiated_roots.append(repo_root)
            original_init(self_inner, repo_root)

        combos = [
            BuildCombo(platform="microvm", mode="standalone", memory="256mb"),
            BuildCombo(platform="microvm", mode="multi-process", memory="256mb"),
        ]

        with patch.object(ZScript, "__init__", tracking_init):
            run_all_builds(
                script_cls=self._stub_cls,
                combos=combos,
                hook="clean",
                targets=[],
                docker_image=None,
                repo_root=self._repo_root,
            )

        # Each combo should have been instantiated with a DIFFERENT root.
        self.assertEqual(len(instantiated_roots), 2)
        self.assertNotEqual(instantiated_roots[0], instantiated_roots[1])
        # Neither should be the original repo_root.
        for root in instantiated_roots:
            self.assertNotEqual(root, self._repo_root)

    def test_workspace_cleanup_on_success(self) -> None:
        """Per-combo workspaces are cleaned up after successful build."""
        from nanvix_zutil.matrix import run_all_builds

        combos = [
            BuildCombo(platform="microvm", mode="standalone", memory="256mb"),
            BuildCombo(platform="microvm", mode="multi-process", memory="256mb"),
        ]

        results = run_all_builds(
            script_cls=self._stub_cls,
            combos=combos,
            hook="clean",
            targets=[],
            docker_image=None,
            repo_root=self._repo_root,
        )

        # All combos should succeed.
        for result in results.values():
            self.assertTrue(result.success)

        # After run_all_builds completes, _builds/ should be empty or gone.
        builds_dir = self._repo_root / ".nanvix" / "_builds"
        self.assertFalse(
            builds_dir.exists() and any(builds_dir.iterdir()),
            "Per-combo workspaces should be cleaned up after success",
        )

    def test_workspace_cleanup_on_failure(self) -> None:
        """Per-combo workspaces are cleaned up even when a combo fails."""
        from nanvix_zutil.matrix import run_all_builds
        from nanvix_zutil.script import ZScript

        class _Failing(ZScript):
            def setup(self) -> bool:
                return False

            def build(self) -> None:
                raise SystemExit(5)

        combos = [
            BuildCombo(platform="microvm", mode="standalone", memory="256mb"),
        ]

        results = run_all_builds(
            script_cls=_Failing,
            combos=combos,
            hook="build",
            targets=[],
            docker_image=None,
            repo_root=self._repo_root,
        )

        # The combo should fail.
        result = results[combos[0]]
        self.assertFalse(result.success)

        # But the workspace copy must still be cleaned up.
        builds_dir = self._repo_root / ".nanvix" / "_builds"
        self.assertFalse(
            builds_dir.exists() and any(builds_dir.iterdir()),
            "Per-combo workspaces should be cleaned up even on failure",
        )

    def test_setup_fallback_propagated_to_result(self) -> None:
        """run_all_builds sets used_fallback=True when setup sets _used_fallback."""
        from nanvix_zutil.matrix import run_all_builds
        from nanvix_zutil.script import ZScript

        class _FallbackStub(ZScript):
            def setup(self) -> bool:
                self._used_fallback = True
                return True

        combos = [
            BuildCombo(platform="microvm", mode="standalone", memory="256mb"),
        ]

        results = run_all_builds(
            script_cls=_FallbackStub,
            combos=combos,
            hook="setup",
            targets=[],
            docker_image=None,
            repo_root=self._repo_root,
        )

        result = results[combos[0]]
        self.assertTrue(result.success)
        self.assertTrue(result.used_fallback)

    def test_no_fallback_result_has_false(self) -> None:
        """run_all_builds sets used_fallback=False when setup() returns False."""
        from nanvix_zutil.matrix import run_all_builds

        combos = [
            BuildCombo(platform="microvm", mode="standalone", memory="256mb"),
        ]

        results = run_all_builds(
            script_cls=self._stub_cls,
            combos=combos,
            hook="setup",
            targets=[],
            docker_image=None,
            repo_root=self._repo_root,
        )

        result = results[combos[0]]
        self.assertTrue(result.success)
        self.assertFalse(result.used_fallback)

    def test_setup_return_value_propagated_to_result(self) -> None:
        """run_all_builds honors setup() returning True without mutating state."""
        from nanvix_zutil.matrix import run_all_builds
        from nanvix_zutil.script import ZScript

        class _ReturnOnlyFallback(ZScript):
            def setup(self) -> bool:
                return True

        combos = [
            BuildCombo(platform="microvm", mode="standalone", memory="256mb"),
        ]

        results = run_all_builds(
            script_cls=_ReturnOnlyFallback,
            combos=combos,
            hook="setup",
            targets=[],
            docker_image=None,
            repo_root=self._repo_root,
        )

        result = results[combos[0]]
        self.assertTrue(result.success)
        self.assertTrue(result.used_fallback)


class TestUnsupportedAllBuilds(unittest.TestCase):
    """Tests for the --all-builds hook allowlist guard in ZScript.main()."""

    def setUp(self) -> None:
        import tempfile

        from tests.testutils import MANIFEST_WITH_BUILDS, write_manifest

        self._tmpdir = tempfile.mkdtemp()
        self._repo_root = Path(self._tmpdir)
        write_manifest(self._repo_root, content=MANIFEST_WITH_BUILDS)

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_lock_rejected_with_all_builds(self) -> None:
        """lock --all-builds exits with an error."""
        import sys

        from nanvix_zutil.script import ZScript

        sys.argv = ["z.py", "lock", "--all-builds"]
        with self.assertRaises(SystemExit):
            ZScript.main(repo_root=self._repo_root)

    def test_distclean_rejected_with_all_builds(self) -> None:
        """distclean --all-builds exits with an error."""
        import sys

        from nanvix_zutil.script import ZScript

        sys.argv = ["z.py", "distclean", "--all-builds"]
        with self.assertRaises(SystemExit):
            ZScript.main(repo_root=self._repo_root)


class TestAllBuildsFallbackExit(unittest.TestCase):
    """--all-builds exits EXIT_DEGRADED_SETUP when any combo used fallback."""

    def setUp(self) -> None:
        import tempfile

        from tests.testutils import MANIFEST_WITH_BUILDS, write_manifest

        self._tmpdir = tempfile.mkdtemp()
        self._repo_root = Path(self._tmpdir)
        write_manifest(self._repo_root, content=MANIFEST_WITH_BUILDS)

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_all_builds_exits_7_when_any_combo_used_fallback(self) -> None:
        """--all-builds setup exits EXIT_DEGRADED_SETUP on fallback."""
        from unittest.mock import patch

        from nanvix_zutil.exitcodes import EXIT_DEGRADED_SETUP
        from nanvix_zutil.script import ZScript

        combo = BuildCombo(platform="hyperlight", mode="multi-process", memory="128mb")
        fake_results = {
            combo: BuildResult(
                combo=combo,
                success=True,
                duration_seconds=1.0,
                used_fallback=True,
            ),
        }

        with (
            patch("sys.argv", ["z.py", "--all-builds", "setup"]),
            patch("nanvix_zutil.script.docker_available", return_value=True),
            patch("nanvix_zutil.script.image_exists", return_value=True),
            patch(
                "nanvix_zutil.script.run_all_builds",
                return_value=fake_results,
            ),
            patch("nanvix_zutil.script.print_summary"),
            self.assertRaises(SystemExit) as ctx,
        ):
            ZScript.main(repo_root=self._repo_root)

        self.assertEqual(ctx.exception.code, EXIT_DEGRADED_SETUP)

    def test_all_builds_succeeds_when_no_fallback(self) -> None:
        """--all-builds setup exits cleanly when no combo used fallback."""
        from unittest.mock import patch

        from nanvix_zutil.script import ZScript

        combo = BuildCombo(platform="hyperlight", mode="multi-process", memory="128mb")
        fake_results = {
            combo: BuildResult(
                combo=combo,
                success=True,
                duration_seconds=1.0,
                used_fallback=False,
            ),
        }

        with (
            patch("sys.argv", ["z.py", "--all-builds", "setup"]),
            patch("nanvix_zutil.script.docker_available", return_value=True),
            patch("nanvix_zutil.script.image_exists", return_value=True),
            patch(
                "nanvix_zutil.script.run_all_builds",
                return_value=fake_results,
            ),
            patch("nanvix_zutil.script.print_summary"),
        ):
            # Should not raise SystemExit.
            ZScript.main(repo_root=self._repo_root)


if __name__ == "__main__":
    unittest.main()
