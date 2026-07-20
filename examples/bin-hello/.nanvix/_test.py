# This module exists mostly to show off local imports.
# Conventional _ prefix to indicate a private module,
# and to prevent collisions with stdlib

import sys
from pathlib import Path

from nanvix_zutil import (
    StandaloneTest,
    StandaloneTestFailure,
    ZScript,
    log,
)
from nanvix_zutil.config import DeploymentMode
from nanvix_zutil.exitcodes import EXIT_TEST_FAILURE
from nanvix_zutil.paths import repo_root


class Test:
    def __init__(self, script: ZScript):
        self.script = script

    def test(self) -> None:
        """Run the test suite (smoke + integration + functional).

        Functional tests run the built ELF under ``nanvixd`` in
        standalone mode via :class:`StandaloneTest`. That is the
        shared plumbing all downstream consumers use — a case object
        per invocation, ``.run()`` to boot, aggregate failures
        yourself. The same code path runs on Linux and Windows;
        ``nanvixd``'s host extension (``.elf`` vs ``.exe``) is picked
        internally.

        Non-standalone modes are not exercised here; they need
        ``linuxd`` and a different topology (see design docs).
        """
        binary = repo_root() / "hello.elf"

        # Smoke: binary must exist and be non-trivially sized.
        log.info("=== bin-hello smoke tests ===")
        if not binary.exists():
            log.fatal(
                f"{binary} not found — run 'nanvix-zutil build' first.",
                code=EXIT_TEST_FAILURE,
            )
        size = binary.stat().st_size
        if size < 1000:
            log.fatal(f"{binary} too small ({size} bytes).", code=EXIT_TEST_FAILURE)
        log.success(f"OK: {binary.name} ({size} bytes)")

        # Integration: verify ELF magic.
        log.info("=== bin-hello integration tests ===")
        with binary.open("rb") as fh:
            magic = fh.read(4)
        if magic != b"\x7fELF":
            log.fatal(f"{binary} is not a valid ELF binary.", code=EXIT_TEST_FAILURE)
        log.success(f"OK: {binary.name} is a valid ELF binary")

        log.info("=== bin-hello functional tests ===")
        cases = [StandaloneTest(elf_path=binary)]
        failures: list[StandaloneTestFailure] = []
        for case in cases:
            try:
                case.run()
            except StandaloneTestFailure as e:
                failures.append(e)
        if failures:
            for f in failures:
                log.warning(f"  {f}")
            log.fatal(
                f"{len(failures)} functional test(s) failed.",
                code=EXIT_TEST_FAILURE,
            )
        log.success("PASS: bin-hello functional tests")
