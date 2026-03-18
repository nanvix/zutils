# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Tests for nanvix_zutil.log."""

import json
import sys
import unittest
from io import StringIO

import nanvix_zutil.log as log_mod


class TestInfo(unittest.TestCase):
    """Tests for log.info."""

    def setUp(self) -> None:
        log_mod.set_json_mode(False)

    def test_basic(self) -> None:
        buf = StringIO()
        sys.stdout = buf
        try:
            log_mod.info("hello")
        finally:
            sys.stdout = sys.__stdout__
        self.assertIn("hello", buf.getvalue())

    def test_json_mode(self) -> None:
        log_mod.set_json_mode(True)
        buf = StringIO()
        sys.stdout = buf
        try:
            log_mod.info("hello json")
        finally:
            sys.stdout = sys.__stdout__
            log_mod.set_json_mode(False)
        obj = json.loads(buf.getvalue().strip())
        self.assertEqual(obj["level"], "info")
        self.assertEqual(obj["message"], "hello json")


class TestSuccess(unittest.TestCase):
    """Tests for log.success."""

    def setUp(self) -> None:
        log_mod.set_json_mode(False)

    def test_basic(self) -> None:
        buf = StringIO()
        sys.stdout = buf
        try:
            log_mod.success("done")
        finally:
            sys.stdout = sys.__stdout__
        self.assertIn("done", buf.getvalue())

    def test_json_mode(self) -> None:
        log_mod.set_json_mode(True)
        buf = StringIO()
        sys.stdout = buf
        try:
            log_mod.success("all good")
        finally:
            sys.stdout = sys.__stdout__
            log_mod.set_json_mode(False)
        obj = json.loads(buf.getvalue().strip())
        self.assertEqual(obj["level"], "success")


class TestWarning(unittest.TestCase):
    """Tests for log.warning."""

    def setUp(self) -> None:
        log_mod.set_json_mode(False)

    def test_basic(self) -> None:
        buf = StringIO()
        sys.stderr = buf
        try:
            log_mod.warning("careful")
        finally:
            sys.stderr = sys.__stderr__
        self.assertIn("careful", buf.getvalue())

    def test_json_mode(self) -> None:
        log_mod.set_json_mode(True)
        buf = StringIO()
        sys.stderr = buf
        try:
            log_mod.warning("watch out")
        finally:
            sys.stderr = sys.__stderr__
            log_mod.set_json_mode(False)
        obj = json.loads(buf.getvalue().strip())
        self.assertEqual(obj["level"], "warning")


class TestError(unittest.TestCase):
    """Tests for log.error."""

    def setUp(self) -> None:
        log_mod.set_json_mode(False)

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

    def test_json_mode_with_hint(self) -> None:
        log_mod.set_json_mode(True)
        buf = StringIO()
        sys.stderr = buf
        try:
            log_mod.error("bad", hint="fix it")
        finally:
            sys.stderr = sys.__stderr__
            log_mod.set_json_mode(False)
        obj = json.loads(buf.getvalue().strip())
        self.assertEqual(obj["level"], "error")
        self.assertEqual(obj["hint"], "fix it")


class TestFatal(unittest.TestCase):
    """Tests for log.fatal."""

    def setUp(self) -> None:
        log_mod.set_json_mode(False)

    def test_exits(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            log_mod.fatal("fatal error", code=1)
        self.assertEqual(ctx.exception.code, 1)

    def test_custom_code(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            log_mod.fatal("network error", code=4)
        self.assertEqual(ctx.exception.code, 4)

    def test_json_mode(self) -> None:
        log_mod.set_json_mode(True)
        buf = StringIO()
        sys.stderr = buf
        try:
            with self.assertRaises(SystemExit):
                log_mod.fatal("kaboom", code=5, hint="check logs")
        finally:
            sys.stderr = sys.__stderr__
            log_mod.set_json_mode(False)
        obj = json.loads(buf.getvalue().strip())
        self.assertEqual(obj["level"], "error")
        self.assertEqual(obj["code"], 5)
        self.assertEqual(obj["hint"], "check logs")


if __name__ == "__main__":
    unittest.main()
