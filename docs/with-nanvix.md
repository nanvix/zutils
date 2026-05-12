# Using `--with-nanvix` for Local Development

The `--with-nanvix PATH` flag allows you to override the Nanvix sysroot
binaries with artifacts from a local build.  This is useful when developing
Nanvix itself and testing changes against downstream consumers (zlib, bzip2,
etc.) without publishing a release.

## How It Works

1. `./z setup` downloads the sysroot from GitHub as usual.
2. Files from `PATH/bin/` and `PATH/lib/` are copied on top of the
   downloaded sysroot, replacing matching artifacts.
3. Sysroot verification runs against the overlaid result.
4. If `PATH/deps/<name>/` directories exist for any declared dependency,
   those are installed from local files instead of downloading from GitHub.

## Prerequisites

- A local Nanvix build with `bin/` and `lib/` output directories:

  ```text
  ~/src/nanvix/nanvix/
  ├── bin/
  │   ├── nanvixd.elf
  │   ├── mkramfs.elf
  │   ├── kernel.elf
  │   ├── uservm.elf
  │   └── linuxd.elf
  └── lib/
      └── libposix.a
  ```

- The feature-branch version of `nanvix-zutil` installed in the consumer's
  venv.

## Quick Start

```bash
cd /path/to/consumer   # e.g. usr/lib/zlib

# Bootstrap venv and install feature-branch zutils
./z setup
.nanvix/venv/bin/pip install -e /path/to/zutils

# Clean sysroot and re-run with local override
rm -rf .nanvix/sysroot .nanvix/env.json
WITH_NANVIX=~/src/nanvix/nanvix .nanvix/venv/bin/nanvix-zutil setup

# Verify
cmp .nanvix/sysroot/bin/nanvixd.elf ~/src/nanvix/nanvix/bin/nanvixd.elf

# Build using the overlaid sysroot
WITH_NANVIX=~/src/nanvix/nanvix .nanvix/venv/bin/nanvix-zutil build
```

## Using the Shell Wrapper

The `z.sh` and `z.ps1` wrappers support `--with-nanvix` natively. Pass
the flag directly:

```bash
./z setup --with-nanvix ~/src/nanvix/nanvix
./z build --with-nanvix ~/src/nanvix/nanvix
```

The wrapper resolves the path to an absolute directory and exports it as
`WITH_NANVIX` before invoking `nanvix-zutil`.

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

When `WITH_NANVIX` is set and `deps/<name>/` exists, the dependency
is installed from the local directory and the GitHub download is skipped.

## Notes

- The overlay copies **all** files from `bin/` and `lib/` — not only the
  ones required by the sysroot verification.  Extra files are harmless.
- `WITH_NANVIX` has no effect on `build`, `test`, or other
  subcommands — it only modifies behavior during `setup`.
- To return to the normal (release-based) workflow, simply omit
  `--with-nanvix` and delete `.nanvix/sysroot` before re-running setup.
