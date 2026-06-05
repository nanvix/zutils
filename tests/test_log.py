# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.log."""

import sys
import unittest
from io import StringIO

import nanvix_zutil.log as log_mod


class TestInfo(unittest.TestCase):
    """Tests for log.info."""

    def test_basic(self) -> None:
        buf = StringIO()
        sys.stderr = buf
        try:
            log_mod.info("hello")
        finally:
            sys.stderr = sys.__stderr__
        self.assertIn("hello", buf.getvalue())


class TestSuccess(unittest.TestCase):
    """Tests for log.success."""

    def test_basic(self) -> None:
        buf = StringIO()
        sys.stderr = buf
        try:
            log_mod.success("done")
        finally:
            sys.stderr = sys.__stderr__
        self.assertIn("done", buf.getvalue())


class TestWarning(unittest.TestCase):
    """Tests for log.warning."""

    def test_basic(self) -> None:
        buf = StringIO()
        sys.stderr = buf
        try:
            log_mod.warning("careful")
        finally:
            sys.stderr = sys.__stderr__
        self.assertIn("careful", buf.getvalue())


class TestError(unittest.TestCase):
    """Tests for log.error."""

    def test_basic(self) -> None:
        buf = StringIO()
        sys.stderr = buf
        try:
            log_mod.error("oops")
        finally:
            sys.stderr = sys.__stderr__
        self.assertIn("oops", buf.getvalue())

    def test_with_hint(self) -> None:
        buf = StringIO()
        sys.stderr = buf
        try:
            log_mod.error("oops", hint="try this")
        finally:
            sys.stderr = sys.__stderr__
        output = buf.getvalue()
        self.assertIn("oops", output)
        self.assertIn("try this", output)


class TestFatal(unittest.TestCase):
    """Tests for log.fatal."""

    def test_exits(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            log_mod.fatal("fatal error", code=1)
        self.assertEqual(ctx.exception.code, 1)

    def test_custom_code(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            log_mod.fatal("network error", code=4)
        self.assertEqual(ctx.exception.code, 4)

    def test_emits_hint(self) -> None:
        buf = StringIO()
        sys.stderr = buf
        try:
            with self.assertRaises(SystemExit):
                log_mod.fatal("kaboom", code=5, hint="check logs")
        finally:
            sys.stderr = sys.__stderr__
        output = buf.getvalue()
        self.assertIn("kaboom", output)
        self.assertIn("check logs", output)


class TestAnsiWindowsEnablement(unittest.TestCase):
    """Tests for Windows ANSI VT processing enablement."""

    def test_no_crash_on_any_platform(self) -> None:
        """_enable_ansi_on_windows should never raise."""
        from nanvix_zutil.log import (
            _enable_ansi_on_windows,  # pyright: ignore[reportPrivateUsage]
        )

        # Should be a no-op on Linux, no crash on Windows.
        _enable_ansi_on_windows()


if __name__ == "__main__":
    unittest.main()
