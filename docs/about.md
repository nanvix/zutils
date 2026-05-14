Zutils is the primary build tool for the [Nanvix](https://github.com/nanvix/nanvix) ecosystem. This file represents a finalized design. The facts may not align with this document, but this document should be considered the end-goal.

## Key files and directories

### bootstrappers

At the root level are three scripts. They are designed to bootstrap the zutils system. They set up the python environment for local development by installing a virtual environment at `.nanvix/venv`

### .nanvix

The entrypoint for the zutils build system. Contains the following key files:

| File        | Staged? | Generated? | What it does                                                                                                       |
| ----------- | ------- | ---------- | ------------------------------------------------------------------------------------------------------------------ |
| nanvix.toml | ✅      | ❌         | Hosts the dependencies and valid toolchains for this build. The manifest, similar to Cargo.toml or pyproject.toml. |
| z.py        | ✅      | ❌         | Entrypoint for the zutils library. Hosts an implementation of the `ZScript` class in Python.                       |
| nanvix.lock | ❌      | ✅         | Locks dependency versions. Generated at CI time.                                                                   |
| env.json    | ❌      | ✅         | Local build settings Generated during setup.                                                                       |
| buildroot/  | ❌      | ✅         | See below.                                                                                                         |
| sysroot/    | ❌      | ✅         | See below.                                                                                                         |

### Project-specific helpers

Zutils can call out to any build system, such as make, Cargo, or a hatchling. In the end, zutils is just a python script which we use to extend the existing build system to support nanvix. These are typically staged, though the project may choose to generate scripts at build time if desired.

Sometimes at the root level there are additional Makefiles and README-style NANVIX.md entries. These are strictly optional.

### .nanvix/buildroot/

The buildroot contains everything that is needed to build the dependency. It should _not_ include the nanvix runtime. Examples of what goes in this directory are files ending in .h, .a, .py. The exact contents of this directory are up to the implementor - whatever is needed to build the project.

### .nanvix/sysroot/

The sysroot contains everything that nanvix needs to run. It should _not_ include build dependencies. Currently this is extracted directly from GitHub releases, or copied over from a local directory using the `--with-nanvix` flag at setup time.

```
.nanvix/sysroot/
|-- bin/
|---- nanvixd.elf/exe              # nanvix runtime. Runs on host.
|---- kernel.elf                   # nanvix microkernel. Runs in nanvix.
|---- mkramfs.elf/exe              # Makes ramfs images. Runs on host.
|---- mkimage.elf/exe              # Creates full system image. Runs on host.
|-- etc/scripts/common/            # Utility scripts, run in nanvix.
|---- logging.sh
|---- utils.sh
|-- lib/                           # System libraries included with nanvix.
|---- libposix.a
|---- user.ld
```

(Note: Currently there are a lot more files in the Windows release, looks like debug symbols and utilities.)

## Lifecycle

Zutils has a multi-phase lifecycle similar to other build tools. Most lifecycle stages call out to the zutils library, with bootstrapping being the only exception. Additionally, many lifecycle stages can be overridden. Some are _required_ overrides 🟡, while some are _optional_ overrides 🟢. Those which are marked as optional overrides have default functionality. Typically this is called in addition to the extra functionality which the project may need. Those marked as required overrides have _no_ default behavior and must be overridden to do anything at all.

| Stage     | Example call                                       | Override? | What it does                                                                                                                                                                                           |
| --------- | -------------------------------------------------- | --------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Bootstrap | `./z`                                              | 🔴        | Bootstraps the nanvix*zutil virtual environment. Does \_not* depend on `ZScript`.                                                                                                                      |
| Setup     | `./z setup --with-docker nanvix/toolchain:minimal` | 🟢        | Sets up the build environment. By default, this downloads the runtime sysroot to `sysroot` and build-time dependencies to `buildroot`; `--with-docker` persists the Docker image for later commands. |
| Build     | `./z build`                                        | 🟡        | Builds the project. Should create ramfs images used for testing in addition to the final build artefacts. Build always happens in Docker, using the specified toolchain image.                         |
| Test      | `./z test`                                         | 🟡        | Runs project-specific test suites. Should _not_ create build artefacts.                                                                                                                                |
| Release   | `./z release`                                      | 🟡        | Packages build artefacts into tarballs and zip files for distribution.                                                                                                                                 |
| Benchmark | `./z benchmark`                                    | 🟡        | Runs benchmarks.                                                                                                                                                               |
| Clean     | `./z clean`                                        | 🟡        | Cleans up build files.                                                                                                                                                         |
| Distclean | `./z distclean`                                    | 🟢        | Removes all transient nanvix artefacts. Does _not_ run clean.                                                                                                                  |
| Format    | `./z format`                                       | 🟢        | Formats python files in `.nanvix` with black.                                                                                                                                  |
| Lint      | `./z lint`                                         | 🟢        | Lints python files in `.nanvix` with pyright.                                                                                                                                  |
| Lock      | `./z lock`                                         | 🟢        | Resolves lock info into `nanvix.lock`                                                                                                                                          |
| Info      | `./z info`                                         | 🔴        | Standalone command that queries GitHub for the relevant release files.                                                                                                         |
| Resolve   | `./z resolve`                                      | 🔴        | Resolves the manifest. Similar to `lock`, but outputs directly to the command line without producing a lock artefact.                                                          |

(Note: `lock` and `resolve` share the same backend and much of their functionality. We should either remove `lock` in preference of using `resolve` in CI, or replace `resolve` with a staged lockfile.)

(Note: I don't think that distclean, format, lint, and lock should be overridable. That defeats the purpose.)

(Note: Currently the `--with-docker` flag is _required_ for setup. It would be nice to be able to specify the correct docker image for setup in the nanvix.toml. That way we don't have to remember which toolchain to use.)

## Environment variables

In addition to flags, which modify behaviors, certain operational values can be overridden at runtime.

| Variable                 | Default                    | What it does                                                                       |
| ------------------------ | -------------------------- | ---------------------------------------------------------------------------------- |
| `NANVIX_TARGET`          | `x86`                      | Sets the target architecture.                                                      |
| `NANVIX_MACHINE`         | `microvm`                  | Sets the target virtual machine.                                                   |
| `NANVIX_DEPLOYMENT_MODE` | `standalone`               | Sets the deployment mode. Can be one of standalone, single-process, multi-process. |
| `NANVIX_MEMORY_SIZE`     | `256mb`                    | Sets nanvix's allocated memory. Can be one of 128mb, 256mb.                        |
| ~~NANVIX_SYSROOT~~       | ~~.nanvix/sysroot~~             | ~~Sets the sysroot path.~~                                                         |
| `NANVIX_TOOLCHAIN`       | `/opt/toolchain`                | Path to the cross-compilation toolchain.                                           |
| `NANVIX_DOCKER_IMAGE`    | (reads from `.nanvix/env.json`) | Overrides the docker image used for builds.                                        |
| `GH_TOKEN`               | (none)                          | Used to mitigate API usage limits.                                                 |

(Note: `NANVIX_SYSROOT` currently being removed.)
(Note: `NANVIX_TOOLCHAIN` in practice is never overwhelmed. Candidate for removal.)
(Note: `NANVIX_DOCKER_IMAGE` is typically set to a specific toolchain per project. Candidate for removal.)

## CI and Distribution

Nanvix artefacts, meaning the OS itself alongside all ported libraries and binaries, are hosted on GitHub. Docker containers may be created for toolchains as well, hosted on ghcr.io. Example toolchains are `ghcr.io/nanvix/toolchain:latest-minimal`, `ghcr.io/nanvix/toolchain-gcc:latest`, `ghcr.io/nanvix/toolchain-python:latest`.

The primary CI mechanism is stored at `.github/workflows/nanvix-ci.yml`. This is a thin wrapper around the real workflow, which are sourced from the [nanvix/workflows](https://github.com/nanvix/workflows) repository. This way the full workflow action can be shared between callers with minimal boilerplate. The workflow formats, lints, builds, tests, and may trigger a release if needed. This is a typical CI workflow, just shared across repositories.

(Note: Currently the input duplicates fields from `nanvix.toml`. We should rely on the `nanvix.toml` as the single source of truth if possible.)

In addition, zutils releases trigger automated downstream pull requests to fully replace the bootstrap scripts. This usually just bumps the zutils version. The workflow for this is `nanvix-update-zutils.yml`.

Downstream consumers of zutils are enumerated at [workflows/consumer-repos.json](https://github.com/nanvix/workflows/blob/main/consumer-repos.json).
