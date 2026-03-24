Developer Guide
===============

This guide describes how to set up a local development environment, run the
test suite, and contribute to ``nanvix-zutil``.

Prerequisites
-------------

- Python 3.12 or newer
- `uv <https://docs.astral.sh/uv/>`_ (Python package and project manager)
- Git

Cloning and Dev Setup
---------------------

.. code-block:: bash

   git clone https://github.com/nanvix/zutils
   cd zutils
   uv sync                       # install project + dev + docs dependencies
   uv run tasks.py setup         # configure git hooks

Dev Commands
------------

All development tasks are driven through ``tasks.py``:

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Command
     - Description
   * - ``uv run tasks.py lint``
     - Check code formatting (black)
   * - ``uv run tasks.py format``
     - Fix code formatting (black)
   * - ``uv run tasks.py typecheck``
     - Strict type checking (basedpyright)
   * - ``uv run tasks.py test``
     - Run the full test suite (pytest)
   * - ``uv run tasks.py docs``
     - Build Sphinx HTML documentation
   * - ``uv run tasks.py docs-serve``
     - Serve the built docs on http://localhost:8000
   * - ``uv run tasks.py clean``
     - Remove caches and build artifacts
   * - ``uv run tasks.py release``
     - Build wheel + sdist in ``dist/``

Building Documentation Locally
-------------------------------

.. code-block:: bash

   uv sync --group docs
   uv run tasks.py docs
   # Open docs/_build/html/index.html in a browser

Or use the Sphinx Makefile directly:

.. code-block:: bash

   cd docs
   make html

Running Tests
-------------

.. code-block:: bash

   uv run tasks.py test
   # or
   uv run pytest tests/ -v

Running Lint and Type Checking
------------------------------

.. code-block:: bash

   uv run tasks.py lint
   uv run tasks.py typecheck

The pre-push Git hook also runs these checks automatically:

.. code-block:: bash

   bash .githooks/pre-push

Commit Message Conventions
---------------------------

Follow the ``[scope] T: Short title`` format:

- **Scope:** ``doc``, ``tests``, ``zutils``, ``ci``, ``examples``
- **Type:** ``F`` (feature), ``B`` (bugfix), ``E`` (enhancement)
- **Title:** ≤ 50 characters, imperative mood

Examples::

   [doc] F: Add Sphinx documentation with GitHub Pages deployment
   [tests] B: Fix basedpyright strict errors
   [zutils] E: Add retry logic to download_release_asset

CI
--

Continuous integration runs on GitHub Actions (`.github/workflows/ci.yml`).
It checks formatting, type correctness, and the full test suite on every push.

Documentation is built and deployed to `GitHub Pages
<https://nanvix.github.io/zutils/>`_ by `.github/workflows/docs.yml` on every
push to ``dev`` or ``main``.
