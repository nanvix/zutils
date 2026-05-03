# Design Overview

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

## Key Modules

### `script.py` — ZScript

The public-facing orchestrator and base class for all consumer build scripts.

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

### `cli.py` — Argument Parsing

Builds the `argparse` parser for `ZScript.main()`. Registers subcommands
dynamically based on which hooks the consumer overrides. Handles `--json`,
`--version`, `--mode`, and `--with-docker` (registered only on `setup`).
Docker is auto-enabled for `setup`, `build`, `release`, and `clean`;
`test` and `benchmark` always run on the host.

### `config.py` — Configuration

Persistent key-value store backed by `.nanvix/env.json`. Three-tier precedence:

1. **Environment variables** (highest)
2. **Persisted `.nanvix/env.json`**
3. **Built-in defaults** (lowest)

Standard keys: `NANVIX_TARGET`, `NANVIX_MACHINE`, `NANVIX_DEPLOYMENT_MODE`,
`NANVIX_MEMORY_SIZE`, `NANVIX_SYSROOT`, `NANVIX_TOOLCHAIN`, `NANVIX_DOCKER_IMAGE`.

### `docker.py` — Docker Integration

Per-command Docker wrapping for cross-compilation. Key types:

- **`DockerConfig`**: Image name, mounts, UID/GID, workdir, extra env vars.
- **`Mount`**: Host-to-container volume mount (path + readonly flag).
- **Path translation**: Maps host paths to container paths.

Container path constants:

| Constant | Path |
|---|---|
| `WORKSPACE_CONTAINER_PATH` | `/mnt/workspace` |
| `SYSROOT_CONTAINER_PATH` | `/mnt/sysroot` |
| `BUILDROOT_CONTAINER_PATH` | `/mnt/buildroot` |
| `TOOLCHAIN_CONTAINER_PATH` | `/opt/nanvix` |

### `manifest.py` — Manifest Parser

Parses `.nanvix/nanvix.toml`. Supports sysroot version, dependency specifiers
(`version`, `tag`, `commitish`, `id`), and environment variable overrides.

### `lockfile.py` — Lockfile

Fully resolved dependency graph with pinned asset URLs. TOML serialization.

### `resolver.py` — Dependency Resolver

BFS-based resolution with cycle detection, staleness checks, and version
fallback.

### `exitcodes.py` — Exit Codes

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

## Lifecycle Flow

### Bootstrap Chain

1. User runs `./z <command>` at a consumer repo root.
2. The bootstrap wrapper (`z`/`z.sh`/`z.ps1`) finds Python ≥ 3.12,
   creates `.venv/`, installs the pinned `nanvix-zutil` wheel,
   and re-execs under the venv Python.
3. `.nanvix/z.py` defines a `ZScript` subclass and calls `MyScript.main()`.
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

### Build/Release Phase

```
setup / build / release / clean   (Docker auto-enabled)
  ├── Load persisted Docker image from config
  ├── Configure DockerConfig with mounts
  └── Dispatch to consumer hook
        └── self.run("make", ...) → transparently wrapped in docker run

test / benchmark                  (always run on host)
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
