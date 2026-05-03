# nanvix_zutil — Design & Architecture

## Overview

`nanvix_zutil` is a Python 3.12+ library that provides unified build
orchestration for the Nanvix ecosystem. Consumer repositories (e.g.
`nanvix/zlib`, `nanvix/cpython`) subclass `ZScript` in a
`.nanvix/z.py` file and invoke it via bootstrap wrappers (`z`, `z.sh`,
`z.ps1`) at the repo root. The library handles sysroot and dependency
management, Docker-based cross-compilation, lockfile resolution,
and structured logging — so consumers only implement
the lifecycle hooks they need.

## Module Dependency Graph

```
__main__.py            ← nanvix-zutil CLI entry point
  ├── script.py        ← ZScript base class, CLI dispatch, lifecycle orchestration
  │     ├── cli.py           ← argparse parser factory, subcommand registration
  │     ├── config.py        ← .nanvix/env.json persistence, env var overrides
  │     ├── buildroot.py     ← Buildroot + Dependency (build-time deps)
  │     ├── sysroot.py       ← Sysroot download/extraction/verification
  │     ├── github.py        ← GitHub release API with retry + GH_TOKEN
  │     ├── lockfile.py      ← Lockfile dataclasses, TOML read/write
  │     ├── resolver.py      ← BFS dependency resolution, cycle detection
  │     ├── manifest.py      ← nanvix.toml parser (metadata + dependencies)
  │     ├── docker.py        ← Docker integration (per-command wrapping, mounts)
  │     ├── release.py       ← Release artifact packaging (.tar.gz, .zip, etc.)
  │     ├── log.py           ← Colored terminal output, --json mode, fatal()
  │     └── exitcodes.py     ← Deterministic exit code constants (0–7)
  │
  ├── info.py          ← nanvix-info CLI (query Nanvix release metadata)
  ├── resolve_cmd.py   ← nanvix-zutil resolve CLI (emit resolved metadata)
  └── utils.py         ← Shared utilities (semver regex)
```

## Module Descriptions

### `script.py` — ZScript

The public-facing orchestrator. `ZScript` is the base class that all
consumer build scripts subclass. It provides:

- **Lifecycle hooks**: `setup`, `distclean`, `build`, `test`,
  `benchmark`, `release`, `clean`, `lock`. Auto-implemented hooks
  (`setup`, `distclean`, `lock`, `help`) are always available; consumer
  hooks only appear in the CLI when the subclass overrides them.
- **CLI dispatch**: `main()` parses arguments via `cli.py`, resolves
  Docker configuration, and routes to the appropriate hook.
- **Subprocess execution**: `run()` transparently wraps commands in
  `docker run` when Docker mode is active.
- **Path translation**: `translate_path()` maps host paths to container
  paths when running inside Docker.
- **Config sync**: Copies canonical tool configs (pyrightconfig.json,
  .yamllint.yml) into `.nanvix/` during setup.

Consumers interact almost exclusively with `ZScript` and the types it
exposes.

### `cli.py` — Argument Parsing

Internal module that builds the `argparse` parser for `ZScript.main()`.
Registers subcommands dynamically based on which hooks the consumer
overrides. Handles `--json`, `--version`, `--mode`, and
the per-subcommand `--with-docker` flag.

### `config.py` — Configuration

Persistent key-value store backed by `.nanvix/env.json`. Three-tier
precedence:

1. **Environment variables** (highest)
2. **Persisted `.nanvix/env.json`**
3. **Built-in defaults** (lowest)

Standard keys: `NANVIX_TARGET`, `NANVIX_MACHINE`,
`NANVIX_DEPLOYMENT_MODE`, `NANVIX_MEMORY_SIZE`, `NANVIX_SYSROOT`,
`NANVIX_TOOLCHAIN`, `NANVIX_DOCKER_IMAGE`, `GH_TOKEN`.

### `docker.py` — Docker Integration

Per-command Docker wrapping for cross-compilation. Docker mode is always
enabled for `setup`, `build`, `release`, and `clean`. The
`--with-docker IMAGE` flag on `setup` allows consumers to override the
default image (`nanvix/toolchain:latest-minimal`). Key types:

- **`DockerConfig`**: Image name, mounts, UID/GID, workdir, extra env
  vars. Builds `docker run` command lines.
- **`Mount`**: Host-to-container volume mount (path + readonly flag).
- **Path translation**: Maps host paths to container paths via mount
  table lookups.
- **Windows support**: Translates Windows paths to Docker-compatible
  MSYS-style paths (`C:\foo` → `/c/foo`). On Windows, a tar-copy
  strategy is used instead of bind mounts for file I/O reliability.

Container path constants:

| Constant | Path |
|---|---|
| `WORKSPACE_CONTAINER_PATH` | `/mnt/workspace` |
| `SYSROOT_CONTAINER_PATH` | `/mnt/sysroot` |
| `BUILDROOT_CONTAINER_PATH` | `/mnt/buildroot` |
| `TOOLCHAIN_CONTAINER_PATH` | `/opt/nanvix` |

### `manifest.py` — Manifest Parser

Parses `nanvix.toml`, the declarative manifest that declares package
metadata and dependencies. Supports:

- **Sysroot version**: semver string or `"latest"`.
- **Dependency specifiers**: `version`, `tag`, `commitish`, or `id`.
  Version refs are auto-suffixed with `-nanvix-{sysroot_version}`
  unless the sysroot is `"latest"` (deferred to resolver).
- **Environment overrides**: `NANVIX_VERSION` and
  `NANVIX_VERSION_<NAME>` override manifest-declared versions.

### `lockfile.py` — Lockfile

Defines the `Lockfile` dataclass — a fully resolved dependency graph
with pinned asset URLs. Provides TOML serialization (`write_lockfile`)
and deserialization (`read_lockfile`). Also handles downloading shallow
`nanvix.lock` from GitHub releases for transitive dependency discovery.

Key types:

- **`Lockfile`**: Metadata + list of `ResolvedPackage`.
- **`ResolvedPackage`**: Package name, repo, ref, and list of
  `ResolvedAsset`.
- **`ResolvedAsset`**: File name + download URL.
- **`LockfileMetadata`**: Generator version, manifest hash.

### `resolver.py` — Dependency Resolver

BFS-based dependency resolver that walks the dependency graph starting
from the manifest's direct dependencies. Discovers transitive
dependencies by downloading each dependency's `nanvix.lock` release
asset. Features:

- **Cycle detection**: Tracks visited packages to prevent infinite loops.
- **Staleness check**: `is_stale()` compares the lockfile's manifest
  hash against the current `nanvix.toml`.
- **Version fallback**: For nanvix-suffixed deps, tries the exact
  version first, then falls back to the best available release.
- **Shallow mode**: `--shallow` resolves only direct dependencies.

### `buildroot.py` — Build-Time Dependencies

Manages the `.nanvix/buildroot` directory that collects headers and
static libraries required by consumers. Key types:

- **`Buildroot`**: Creates and populates the buildroot directory.
  Downloads and extracts release archives, installs headers into
  `include/` and libraries into `lib/`.
- **`Dependency`**: Describes a single library — name, GitHub repo, ref
  (version/tag/commitish/id), and optional scope.
- **`Ref` / `RefKind`**: Typed version reference (version, tag,
  commitish, or release ID).
- **Version helpers**: `suffix_dep()`, `extract_nanvix_version()`,
  `parse_semver_tuple()` for nanvix-specific version manipulation.

### `sysroot.py` — Runtime Sysroot

Downloads and verifies the Nanvix runtime sysroot from GitHub releases.
The sysroot contains the kernel, POSIX library, linker script, and
system binaries needed to run Nanvix applications. On Windows,
additionally downloads host-native binaries (`nanvixd.exe`,
`mkramfs.exe`). Supports overlaying local build artifacts via
`--with-nanvix PATH`.

### `github.py` — GitHub API Client

Downloads release assets from the GitHub API with:

- **Automatic retry** with exponential back-off (up to 5 retries).
- **`GH_TOKEN` authentication** to avoid rate limits.
- **Version resolution**: `resolve_release()` finds a release by
  semver, tag, commitish, or `"latest"`.
- **Fallback resolution**: `resolve_release_with_fallback()` tries the
  exact version, then falls back to the best available matching release.

### `release.py` — Release Packaging

Produces release archives from a source directory:

- Supports `.tar.gz`, `.tar.bz2`, and `.zip` formats.
- Consumer repos call `package()` from their `release()` hook.
- Archives are deterministic (sorted entries, fixed metadata).

### `log.py` — Structured Logging

All output goes through this module. Two modes:

- **Plain text**: Colored ANSI output (`info:`, `success:`, `warning:`,
  `error:`, `note:`, `hint:`). Enables Windows ANSI support
  automatically.
- **JSON mode** (`--json`): Each message is a single-line JSON object
  with `level`, `message`, optional `code` and `hint` fields.

`fatal()` logs an error and calls `sys.exit()` with the given exit code.

### `exitcodes.py` — Exit Codes

Deterministic exit codes shared across the ecosystem:

| Code | Constant | Meaning |
|---|---|---|
| 0 | `EXIT_SUCCESS` | Operation completed successfully |
| 1 | `EXIT_GENERAL_ERROR` | Unspecified error |
| 2 | `EXIT_INVALID_ARGS` | Invalid command-line arguments |
| 3 | `EXIT_MISSING_DEP` | Required dependency missing |
| 4 | `EXIT_NETWORK_ERROR` | Network operation failed |
| 5 | `EXIT_BUILD_FAILURE` | Build step failed |
| 6 | `EXIT_TEST_FAILURE` | Tests failed |
| 7 | `EXIT_DEGRADED_SETUP` | Setup completed with fallback deps |

### `info.py` — nanvix-info CLI

Standalone command that queries the GitHub Releases API for a Nanvix
release and emits metadata (version, commit SHA, asset URLs) as
`key=value` lines or JSON. Used in CI pipelines to resolve release
information.

### `resolve_cmd.py` — nanvix-zutil resolve CLI

Resolves the `nanvix.toml` manifest and emits release metadata as
`key=value` lines (for `$GITHUB_OUTPUT`) or JSON. Used in CI to
determine release tags and versions.

### `utils.py` — Shared Utilities

Contains the compiled `SEMVER_RE` regex for strict semver matching
(`MAJOR.MINOR.PATCH`), shared across `github.py`, `manifest.py`, and
other modules.

### `__main__.py` — CLI Entry Point

The `nanvix-zutil` command entry point. Routes to three categories:

1. **Consumer commands** (`setup`, `build`, `test`, etc.): Discovers
   the consumer's `.nanvix/z.py`, imports its `ZScript` subclass, and
   delegates to `ZScript.main()`.
2. **Standalone commands** (`info`, `resolve`): Handled
   directly without requiring a consumer script.
3. **Help/version**: Prints usage information.

### `__init__.py` — Public API

Re-exports all public types and functions so consumers can write:

```python
from nanvix_zutil import ZScript, Config, Buildroot, log
```

## Lifecycle Flow

### Bootstrap Chain

1. User runs `./z <command>` at a consumer repo root.
2. The bootstrap wrapper (`z`/`z.sh`/`z.ps1`) finds Python >= 3.12,
   creates `.venv/`, installs the pinned `nanvix-zutil` wheel,
   and re-execs under the venv Python.
3. `.nanvix/z.py` defines a `ZScript` subclass and calls
   `MyScript.main()`.
4. `main()` parses CLI args, configures Docker, and dispatches to the
   appropriate lifecycle hook.

### Setup Phase

```
setup
  ├── Download sysroot from GitHub releases
  ├── Download Windows host binaries (if on Windows)
  ├── Overlay local nanvix artifacts (if --with-nanvix)
  ├── Verify sysroot required files
  ├── Auto-suffix VERSION deps with nanvix version
  ├── For each dependency:
  │     ├── Try local artifacts (if --with-nanvix)
  │     ├── Resolve release (with version fallback)
  │     └── Install into .nanvix/buildroot
  ├── Persist config to .nanvix/env.json
  ├── Persist Docker image to config
  └── Sync canonical tool configs
```

### Build/Test Phase

```
build / test / release / clean
  ├── Load persisted Docker image from config
  ├── Configure DockerConfig with mounts
  └── Dispatch to consumer hook
        └── self.run("make", ...) → transparently wrapped in docker run
```

## Data Flow

```
nanvix.toml ──→ Manifest ──→ Resolver ──→ Lockfile ──→ nanvix.lock
                   │                         │
                   ▼                         ▼
               Dependency ──→ GitHub API ──→ Buildroot
                                             (headers + libs)

nanvix/nanvix releases ──→ Sysroot
                           (kernel + binaries + libposix)
```

## Confinement

`nanvix_zutil` creates no files outside `.nanvix/` in consumer repos.
All artifacts — sysroot, buildroot, venv, config, cache, and lockfile
— live under `.nanvix/`.

## Consumer Pattern

Every consumer repo follows this structure:

```
nanvix/<project>/
├── z              # Bash bootstrap
├── z.sh           # Bash bootstrap (alternative)
├── z.ps1          # PowerShell bootstrap
├── .venv/         # Auto-created virtualenv
└── .nanvix/
    ├── z.py       # ZScript subclass implementing hooks
    ├── nanvix.toml # Declarative dependencies
    ├── nanvix.lock # Pinned dependency graph (committed)
    ├── env.json   # Persistent config (generated)
    ├── sysroot/   # Downloaded runtime artifacts
    └── buildroot/ # Downloaded build-time deps
```

Minimal consumer script:

```python
from nanvix_zutil import ZScript

class MyBuild(ZScript):
    def build(self) -> None:
        self.run("make", "-f", "Makefile.nanvix", "all")

    def test(self) -> None:
        self.run("make", "-f", "Makefile.nanvix", "test")

if __name__ == "__main__":
    MyBuild.main()
```
