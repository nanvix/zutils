# hello-transitive — Nanvix Transitive Dependency Example

> **Note:** This example uses a synthetic dependency (`libfoo`) that does
> not exist as a real GitHub repository.  The example does not build yet —
> it is designed for automated testing with mocked API responses.
> See `tests/test_example_transitive.py`.

A minimal C program demonstrating how `./z lock` discovers transitive
dependencies via each dependency's shallow `nanvix.lock` release asset.

## Dependency Chain

```
hello-transitive
  └── libfoo (commitish: abc1234)
        └── zlib (discovered transitively from libfoo's nanvix.lock)
```

`hello-transitive` declares only `libfoo` in its `nanvix.toml`.
Running `./z lock` resolves `libfoo`, downloads its `nanvix.lock`
release asset, and discovers that `libfoo` depends on `zlib`.  The
resolver then adds `zlib` to the lockfile as a transitive dependency.

## Structure

```
hello-transitive/
├── .nanvix/
│   ├── nanvix.toml             # Declares libfoo dependency
│   └── z.py                    # HelloTransitive(ZScript) — lifecycle hooks
├── Makefile.nanvix             # Cross-compilation rules
├── src/
│   └── hello.c                 # Simple C program
├── z                           # Bash bootstrap
├── z.ps1                       # PowerShell bootstrap
└── README.md
```

## Prerequisites

- **Python 3.12+**
- **Nanvix cross-compiler** — one of:
  - Native install at `/opt/nanvix/` (or `$NANVIX_TOOLCHAIN`)
  - Docker image `nanvix/toolchain:latest-minimal`
- **Docker** — for functional tests only

## Usage

```bash
./z lock       # Resolve deps (discovers zlib via libfoo's lockfile)
./z setup      # Download sysroot + all resolved deps
./z build      # Cross-compile hello.c → hello-transitive.elf
./z test       # Run smoke, integration, and functional tests
./z clean      # Remove build artifacts
```

## How It Works

1. `./z lock` resolves the dependency graph:
   - Resolves `libfoo` from `nanvix/libfoo` at commitish `abc1234`
   - Downloads `libfoo`'s `nanvix.lock` release asset (a shallow lockfile
     listing only `libfoo`'s direct dependencies)
   - Discovers that `libfoo` depends on `zlib`
   - Resolves `zlib` and adds it to the lockfile as a transitive dep
   - Writes `.nanvix/nanvix.lock` with all pinned versions and asset URLs

2. `./z lock --check` verifies the lockfile is up-to-date by comparing
   the SHA-256 hash of `nanvix.toml` against the hash stored in the
   lockfile metadata.  Exits with code 3 if missing or code 2 if stale.

3. `./z lock --shallow` resolves only direct dependencies (no transitive
   discovery).  CI runs this before a release and uploads the resulting
   `nanvix.lock` as a standalone release asset, so downstream consumers
   can discover this package's dependencies.

## Inspecting the Lockfile

After running `./z lock`, inspect `.nanvix/nanvix.lock`:

```bash
cat .nanvix/nanvix.lock
```

The lockfile lists each resolved package with its pinned tag, commitish,
release ID, and downloadable asset URLs.  Transitive dependencies are
marked with `transitive = true` and `required-by = ["libfoo"]`.

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `NANVIX_MACHINE` | `microvm` | Target machine |
| `NANVIX_DEPLOYMENT_MODE` | `standalone` | Deployment mode |
| `NANVIX_MEMORY_SIZE` | `256mb` | Memory size |
| `NANVIX_TOOLCHAIN` | `/opt/nanvix` | Toolchain path |
| `GH_TOKEN` | *(none)* | GitHub token for API rate limits |
