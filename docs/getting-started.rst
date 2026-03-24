Getting Started
===============

Installation
------------

Install ``nanvix-zutil`` from PyPI:

.. code-block:: bash

   pip install nanvix-zutil

Bootstrap Chain
---------------

Consumer repositories use a thin bootstrap wrapper (``z`` or ``z.ps1``) at
the repo root that:

1. Finds Python ≥ 3.12 on the host.
2. Creates a ``.nanvix/venv/`` virtualenv and installs the pinned
   ``nanvix-zutil`` version if needed.
3. Re-execs the consumer's ``.nanvix/z.py`` under the venv Python.
4. Dispatches to the appropriate ``ZScript`` lifecycle hook.

Minimal Example
---------------

Create ``.nanvix/z.py`` in your consumer repository:

.. code-block:: python

   from nanvix_zutil import ZScript, Buildroot, Sysroot

   class MyBuild(ZScript):
       def setup(self) -> None:
           sysroot = Sysroot.download(
               machine=self.config.machine,
               deployment_mode=self.config.deployment_mode,
               memory_size=self.config.memory_size,
               tag="v1.0.0",
           )
           self.config.set("NANVIX_SYSROOT", str(sysroot.path))
           self.config.save()

       def build(self) -> None:
           self.run("make", "-f", "Makefile.nanvix", "all")

   if __name__ == "__main__":
       MyBuild.main()

Then invoke via the bootstrap wrapper at the repo root:

.. code-block:: bash

   ./z setup
   ./z build
   ./z test

See :doc:`examples` for a complete runnable walkthrough.
