User Guide
==========

Lifecycle Hooks
---------------

``ZScript`` exposes six lifecycle hooks that consumer repositories override.
Each hook maps directly to a CLI subcommand:

.. list-table::
   :header-rows: 1
   :widths: 20 30 50

   * - Hook
     - CLI command
     - Description
   * - ``setup``
     - ``./z setup``
     - Download sysroot/buildroot, write ``env.json``
   * - ``build``
     - ``./z build``
     - Compile sources
   * - ``test``
     - ``./z test``
     - Run test suite
   * - ``benchmark``
     - ``./z benchmark``
     - Run performance benchmarks
   * - ``release``
     - ``./z release``
     - Produce release artifacts
   * - ``clean``
     - ``./z clean``
     - Remove build artifacts

All hooks default to a no-op. Override only the ones you need.

Config System
-------------

``Config`` persists build configuration in ``.nanvix/env.json`` and supports
environment variable overrides at runtime.

**Precedence (highest → lowest):**

1. Environment variables (e.g. ``NANVIX_MACHINE``)
2. Values stored in ``env.json`` (written by ``setup``)
3. Built-in defaults

**Typical usage:**

.. code-block:: python

   def setup(self) -> None:
       self.config.set("NANVIX_SYSROOT", str(sysroot.path))
       self.config.save()

   def build(self) -> None:
       sysroot = self.config.get("NANVIX_SYSROOT")
       self.run("make", f"SYSROOT={sysroot}", "all")

Sysroot and Buildroot
---------------------

``Sysroot`` downloads the Nanvix runtime sysroot from a GitHub release:

.. code-block:: python

   sysroot = Sysroot.download(
       machine=self.config.machine,
       deployment_mode=self.config.deployment_mode,
       memory_size=self.config.memory_size,
       tag="v1.0.0",
   )

``Buildroot`` manages build-time dependencies (headers and static libraries)
declared as ``Dependency`` objects:

.. code-block:: python

   buildroot = Buildroot(path=Path(".nanvix/buildroot"))
   buildroot.add(Dependency(name="libfoo", repo="nanvix/foo", tag="v1.0.0"))
   buildroot.sync()

``--json`` Mode
---------------

Pass ``--json`` to any subcommand to receive machine-readable output:

.. code-block:: bash

   ./z build --json

All log messages are emitted as JSON objects with ``level``, ``code``,
``message``, and optional ``hint`` fields. This is useful for CI pipelines
that parse build output programmatically.

Exit Codes
----------

.. list-table::
   :header-rows: 1
   :widths: 10 20 70

   * - Code
     - Constant
     - Meaning
   * - 0
     - ``EXIT_SUCCESS``
     - Command completed successfully
   * - 1
     - ``EXIT_FAILURE``
     - General / unclassified error
   * - 2
     - ``EXIT_INVALID_ARGS``
     - Invalid command-line arguments
   * - 3
     - ``EXIT_MISSING_DEP``
     - Required dependency not found
   * - 4
     - ``EXIT_NETWORK``
     - Network or download error
   * - 5
     - ``EXIT_BUILD``
     - Build step failed
   * - 6
     - ``EXIT_TEST``
     - Test step failed

Environment Variables
---------------------

.. list-table::
   :header-rows: 1
   :widths: 30 20 50

   * - Variable
     - Default
     - Purpose
   * - ``NANVIX_MACHINE``
     - ``hyperlight``
     - Target machine identifier
   * - ``NANVIX_DEPLOYMENT_MODE``
     - ``multi-process``
     - Deployment mode (``single-process``, ``multi-process``, ``standalone``)
   * - ``NANVIX_MEMORY_SIZE``
     - ``128mb``
     - Memory size for artifact naming
   * - ``NANVIX_SYSROOT``
     - *(set by setup)*
     - Path to the runtime sysroot
   * - ``NANVIX_BUILDROOT``
     - *(set by setup)*
     - Path to the build-time root
   * - ``GH_TOKEN``
     - *(none)*
     - GitHub token for API rate-limit increases
