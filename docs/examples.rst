Examples
========

Hello World
-----------

The ``examples/hello-world/`` directory contains a complete runnable example
that cross-compiles a C program for Nanvix using the full ``ZScript``
lifecycle.

Structure
~~~~~~~~~

.. code-block:: text

   hello-world/
   ├── z                # Bash bootstrap wrapper
   ├── z.ps1            # PowerShell bootstrap wrapper
   ├── Makefile.nanvix  # Cross-compilation rules
   ├── .nanvix/
   │   └── z.py         # ZScript subclass (build orchestration)
   ├── src/
   │   └── hello.c      # Hello world C program
   └── README.md

Running the Example
~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   cd examples/hello-world
   ./z setup    # download Nanvix sysroot from GitHub releases
   ./z build    # cross-compile hello.c → hello.elf
   ./z test     # run tests (smoke, integration, functional)
   ./z clean    # remove build artifacts

How It Works
~~~~~~~~~~~~

1. **``./z setup``** downloads the Nanvix runtime sysroot from
   ``nanvix/nanvix`` GitHub releases. The sysroot contains ``libposix.a``,
   ``libc.a``, ``user.ld``, and the ``nanvixd.elf`` runtime.

2. **``./z build``** invokes ``make -f Makefile.nanvix`` which
   cross-compiles ``src/hello.c`` using ``i686-nanvix-gcc`` and links it
   statically against the Nanvix POSIX layer.

3. **``./z test``** runs smoke tests (binary exists), integration tests
   (valid ELF), and functional tests (executes on ``nanvixd.elf``).

Prerequisites
~~~~~~~~~~~~~

One of:

- **Native toolchain** — ``i686-nanvix-gcc`` (default path: ``/opt/nanvix/``)
- **Docker** with ``nanvix/toolchain:latest-minimal`` image (auto-detected fallback)
