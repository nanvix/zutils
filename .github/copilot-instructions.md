# Copilot Instructions for `nanvix_zutil`

## What This Is

`nanvix_zutil` is a **Python 3.12+ library** that provides unified build orchestration for all Nanvix ecosystem repositories. It exposes a `ZScript` base class with lifecycle hooks (`setup`, `distclean`, `build`, `test`, `benchmark`, `release`, `clean`, `lock`, `lint`, `format`), structured logging, config persistence, GitHub release artifact downloading, lockfile-based dependency resolution with transitive discovery, and deterministic exit codes. Consumer repos (e.g., `nanvix/zlib`, `nanvix/cpython`) subclass `ZScript` in a `.nanvix/z.py` file and invoke it via thin `z` / `z.sh` / `z.ps1` bootstrap wrappers at the repo root.

The canonical specification lives in [Issue #1](https://github.com/nanvix/zutils/issues/1).

## Validation

After making code changes, always validate by invoking the `/validate` skill. This runs the full test suite and pre-push checks (formatting + type checking) in a single step.

## Architecture

### Module Dependency Graphs

#### CLI Entry Point

```
__main__.py    ←  nanvix-zutil CLI entry point
  ├── script.py      ←  ZScript base class, CLI dispatch, lifecycle orchestration
  │     ├── cli.py         ←  argparse subcommand dispatch, --json, --version
  │     ├── config.py      ←  .nanvix/env.json persistence, env var overrides
  │     ├── buildroot.py   ←  Buildroot + Dependency (build-time deps: headers, static libs)
  │     ├── sysroot.py     ←  Sysroot download/extraction/verification (run-time artifacts)
  │     ├── github.py      ←  GitHub release download with retry + GH_TOKEN
  │     │     └── utils.py       ←  shared utilities (semver regex)
  │     ├── lockfile.py    ←  Lockfile dataclasses, TOML read/write, release asset download
  │     ├── resolver.py    ←  BFS dependency resolution, cycle detection, staleness check
  │     ├── manifest.py    ←  nanvix.toml parser (package metadata + dependencies)
  │     ├── log.py         ←  colored terminal output, --json mode, fatal() with hints
  │     ├── docker.py      ←  Docker integration (per-command wrapping, mounts, image mgmt)
  │     └── exitcodes.py   ←  deterministic exit code constants (0–7)
  ├── info.py        ←  nanvix-info CLI (query Nanvix release metadata)
  └── resolve_cmd.py ←  nanvix-zutil resolve CLI (emit resolved metadata)
```

#### Library API

```
__init__.py    ←  public library API (re-exports all public symbols)
  ├── script.py      ←  ZScript base class (subtree as above)
  ├── buildroot.py   ←  Buildroot, Dependency, suffix_dep, version helpers
  ├── config.py      ←  Config
  ├── docker.py      ←  DockerConfig, Mount
  ├── exitcodes.py   ←  EXIT_* constants
  ├── github.py      ←  resolve_release, resolve_release_with_fallback
  ├── lockfile.py    ←  Lockfile, ResolvedPackage, read_lockfile, write_lockfile
  ├── manifest.py    ←  Manifest, load_manifest
  ├── release.py     ←  package, ArchiveFormat, DEFAULT_FORMATS (.tar.gz, .zip)
  ├── resolver.py    ←  resolve, is_stale
  ├── sysroot.py     ←  Sysroot
  └── info.py        ←  NanvixInfo, get_nanvix_info
```

`script.py` (`ZScript`) is the public-facing orchestrator. Consumers interact almost exclusively with `ZScript`, `Config`, `Buildroot`, `Sysroot`, `Dependency`, `Lockfile`, `DockerConfig`, `NanvixInfo`, and `resolve` — all re-exported from `__init__.py`.

Hooks `setup`, `distclean`, `lock`, `lint`, `format`, and `help` are auto-implemented in the base class and always available in the CLI. Consumer hooks (`build`, `test`, `benchmark`, `release`, `clean`) only appear when the subclass overrides them.

### Bootstrap Chain

1. User runs `./z <command>` at a consumer repo root.
2. `z` (Bash), `z.sh` (Bash), or `z.ps1` (PowerShell) finds Python ≥ 3.12 and execs `.nanvix/z.py`.
3. `z.py` self-bootstraps: creates `.nanvix/venv/`, installs the pinned `nanvix-zutil` version, re-execs under the venv Python.
4. Consumer's `ZScript` subclass dispatches to the appropriate lifecycle hook.

### Consumer Pattern

Every consumer repo follows the same structure:

```
nanvix/<project>/
├── z              # Bash bootstrap (repo root)
├── z.sh           # Bash bootstrap (repo root, alternative)
├── z.ps1          # PowerShell bootstrap (repo root)
└── .nanvix/
    ├── z.py       # Subclasses ZScript, implements hooks
    ├── nanvix.toml # Declarative dependencies
    ├── nanvix.lock # Pinned dependency graph (committed to VCS)
    ├── venv/      # Auto-created virtualenv
    └── env.json   # Persistent config
```

## Key Conventions

- **Python 3.12+ only.** Matches the Nanvix CPython port (3.12.3). Use modern syntax: `X | Y` unions, `list[str]` generics, etc.
- **External dependencies are allowed.** Add runtime dependencies to `[project] dependencies` in `pyproject.toml`.
- **Type-checked with `pyright` in strict mode.** All code must pass strict type checking. Every public function must have a complete type signature.
- **Formatted with `black`.** No configuration overrides.
- **All public functions must have docstrings.**
- **Deterministic exit codes 0–7:** 0=success, 1=general error, 2=invalid args, 3=missing dependency, 4=network error, 5=build failure, 6=test failure, 7=degraded setup.
- **`--json` mode** for all output — errors emit structured JSON with `level`, `code`, `message`, and optional `hint`.
- **Confinement:** `nanvix_zutil` creates no files outside `.nanvix/` in consumer repos.
- **Default branch is `dev`**, not `main`.

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `NANVIX_TARGET` | `x86` | Target architecture |
| `NANVIX_MACHINE` | `microvm` | Target machine |
| `NANVIX_DEPLOYMENT_MODE` | `standalone` | Deployment mode (`single-process`, `multi-process`, `standalone`) |
| `NANVIX_MEMORY_SIZE` | `256mb` | Memory size for artifact naming |
| `NANVIX_SYSROOT` | *(set by setup)* | Path to runtime sysroot |
| `NANVIX_TOOLCHAIN` | *(set by setup)* | Path to cross-compilation toolchain |
| `NANVIX_DOCKER_IMAGE` | *(set by setup)* | Docker image (set by `setup --with-docker`) |
| `GH_TOKEN` | *(none)* | GitHub token for API rate limits |
