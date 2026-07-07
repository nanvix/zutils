# Using `--with-nanvix` for Local Development

The `--with-nanvix PATH` flag replaces the Nanvix sysroot with a local
directory.  This is useful when developing Nanvix itself and testing
changes against downstream consumers (zlib, bzip2, etc.) without
publishing a release.

## How It Works

1. `./z setup` symlinks `.nanvix/sysroot` to `PATH`.  No download.
2. Sysroot verification runs against the symlink target.
3. If `PATH/deps/<name>/` directories exist for any ported dependency
   (repos under the `nanvix/` organisation, such as `nanvix/zlib` or
   `nanvix/cpython`), those are installed from local files instead of
   downloading from GitHub.

Because `.nanvix/sysroot` is a symlink, edits to `PATH` are picked up
immediately with no re-copy step.

## Offline Mode

The `--offline` flag skips the dependency resolver entirely and requires
all artifacts to be available locally via `--with-nanvix`.  In offline
mode:

- `--with-nanvix PATH` is **required** — a fatal error is raised if it
  is not provided.
- **All** dependencies (not just `nanvix/`-owned) are resolved from
  `PATH/deps/<name>/`.
- Missing individual dependencies produce a warning rather than a fatal
  error, allowing the port's own build logic to handle fallbacks.

## Prerequisites

- A local Nanvix build with `bin/` and `lib/` output directories.
  Required files depend on `NANVIX_DEPLOYMENT_MODE`:

  ```text
  ~/src/nanvix/nanvix/
  ├── bin/
  │   ├── nanvixd.elf          # all modes
  │   ├── kernel.elf           # all modes
  │   ├── mkramfs.elf          # all modes
  │   ├── linuxd.elf           # multi-process only
  │   └── uservm.elf           # multi-process only
  └── lib/
      ├── libposix.a           # all modes
      └── user.ld              # all modes
  ```

  `standalone` (default) and `single-process` require the same set.
  `multi-process` additionally requires `linuxd.elf` and `uservm.elf`.

- On Windows, symlink creation requires **Developer Mode** (or running
  the terminal as Administrator).
- The feature-branch version of `nanvix-zutil` installed in the consumer's
  venv.

## Quick Start

```bash
cd /path/to/consumer   # e.g. usr/lib/zlib

# Bootstrap venv and install feature-branch zutils
./z setup --with-docker nanvix/toolchain:latest-minimal
.nanvix/venv/bin/pip install -e /path/to/zutils

# Point sysroot at the local build
rm -rf .nanvix/sysroot .nanvix/env.json
.nanvix/venv/bin/nanvix-zutil setup \
    --with-docker nanvix/toolchain:latest-minimal \
    --with-nanvix ~/src/nanvix/nanvix

# Verify
readlink .nanvix/sysroot   # → ~/src/nanvix/nanvix

# Build using the local sysroot
.nanvix/venv/bin/nanvix-zutil build
```

## Offline Quick Start

When building ports in a dependency chain without network access:

```bash
cd /path/to/consumer

# All artifacts pre-built under ~/nanvix/build (bin/, lib/, deps/*)
PYTHONPATH=~/nanvix/usr/lib/zutils/src \
  python3 -m nanvix_zutil setup \
    --offline \
    --with-docker ghcr.io/nanvix/toolchain-gcc:latest \
    --with-nanvix ~/nanvix/build

# Then build
PYTHONPATH=~/nanvix/usr/lib/zutils/src \
  python3 -m nanvix_zutil build
```

## Using the Shell Wrapper

The `z.sh` and `z.ps1` wrappers forward `--with-nanvix` directly to
`nanvix-zutil`.  Pass the flag to any subcommand that accepts it:

```bash
./z setup --with-docker nanvix/toolchain:latest-minimal \
    --with-nanvix ~/src/nanvix/nanvix
./z build
```

The CLI canonicalises the path to an absolute directory.  Relative paths
and `~` are accepted; the path must exist and be a directory.

## Local Dependency Override

If you also build transitive dependencies locally, place their artifacts
under `PATH/deps/<name>/`:

```text
~/src/nanvix/nanvix/
└── deps/
    └── zlib/
        ├── lib/
        │   └── libz.a
        └── include/
            └── zlib.h
```

When `--with-nanvix` is set and `deps/<name>/` exists, the dependency
is installed from the local directory and the GitHub download is
skipped.

## Notes

- `--with-nanvix` has no effect on `build`, `test`, or other subcommands
  — it only modifies behavior during `setup`.
- To return to the normal (release-based) workflow, omit `--with-nanvix`
  and re-run `setup`.  The existing symlink is replaced with a fresh
  download automatically.
- In offline mode, missing individual dependencies produce a warning
  (not a fatal error), allowing port-specific build logic to handle
  them.  However, `--with-nanvix` itself is required — omitting it is
  fatal.

## install Subcommand

The `install --output PATH` subcommand exports a port's build
artifacts (libraries, headers, binaries) to a target directory:

```bash
nanvix-zutil install --output /path/to/output
```

This creates `<output>/{lib,include,bin}/` subdirectories with the
port's artifacts from `.nanvix/output/`.
