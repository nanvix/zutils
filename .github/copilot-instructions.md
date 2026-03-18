# Copilot Instructions for `nanvix_zutil`

## What This Is

`nanvix_zutil` is a **stdlib-only Python 3.12+ library** that provides unified build orchestration for all Nanvix ecosystem repositories. It exposes a `ZScript` base class with lifecycle hooks (`setup`, `build`, `test`, `release`, `clean`), structured logging, config persistence, GitHub release artifact downloading, and deterministic exit codes. Consumer repos (e.g., `nanvix/zlib`, `nanvix/cpython`) subclass `ZScript` in a `.nanvix/z.py` file and invoke it via thin `z` / `z.ps1` bootstrap wrappers at the repo root.

The canonical specification lives in [Issue #1](https://github.com/nanvix/zutils/issues/1).

## Build, Test, and Lint

```bash
# Type checking (strict mode)
basedpyright

# Formatting
black --check .        # check
black .                # fix

# Tests (stdlib unittest)
python -m pytest       # full suite
python -m pytest tests/test_log.py              # single file
python -m pytest tests/test_log.py::TestInfo    # single class
python -m pytest tests/test_log.py::TestInfo::test_basic  # single test

# Build / install locally
pip install -e .
```

## Architecture

### Module Dependency Graph

```
script.py  ←  CLI entry point (ZScript.main)
  ├── cli.py       ←  argparse subcommand dispatch, --json, --help, --version
  ├── config.py    ←  .nanvix/env.json persistence, env var overrides
  ├── sysroot.py   ←  Sysroot + Dependency download/extraction/verification
  │     └── github.py  ←  GitHub release download with retry + GH_TOKEN
  └── log.py       ←  colored terminal output, --json mode, fatal() with hints
```

`script.py` (`ZScript`) is the public-facing orchestrator. Consumers interact almost exclusively with `ZScript`, `Config`, `Sysroot`, and `Dependency` — all re-exported from `__init__.py`.

### Bootstrap Chain

1. User runs `./z <command>` at a consumer repo root.
2. `z` (Bash) or `z.ps1` (PowerShell) finds Python ≥ 3.12 and execs `.nanvix/z.py`.
3. `z.py` self-bootstraps: creates `.nanvix/venv/`, installs the pinned `nanvix-zutil` version, re-execs under the venv Python.
4. Consumer's `ZScript` subclass dispatches to the appropriate lifecycle hook.

### Consumer Pattern

Every consumer repo follows the same structure:

```
nanvix/<project>/
├── z              # Bash bootstrap (repo root)
├── z.ps1          # PowerShell bootstrap (repo root)
└── .nanvix/
    ├── z.py       # Subclasses ZScript, implements hooks
    ├── venv/      # Auto-created virtualenv
    └── env.json   # Persistent config
```

## Key Conventions

- **Zero external dependencies.** The library uses only the Python standard library. This is a hard constraint — `nanvix_zutil` builds the ecosystem, so it cannot depend on it.
- **Python 3.12+ only.** Matches the Nanvix CPython port (3.12.3). Use modern syntax: `X | Y` unions, `list[str]` generics, etc.
- **Type-checked with `basedpyright` in strict mode.** All code must pass strict type checking. Every public function must have a complete type signature.
- **Formatted with `black`.** No configuration overrides.
- **All public functions must have docstrings.**
- **Deterministic exit codes 0–6:** 0=success, 1=general error, 2=invalid args, 3=missing dependency, 4=network error, 5=build failure, 6=test failure.
- **`--json` mode** for all output — errors emit structured JSON with `level`, `code`, `message`, and optional `hint`.
- **Confinement:** `nanvix_zutil` creates no files outside `.nanvix/` in consumer repos.
- **Default branch is `dev`**, not `main`.

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `NANVIX_PLATFORM` | `hyperlight` | Target platform |
| `NANVIX_PROCESS_MODE` | `multi-process` | Process mode |
| `NANVIX_MEMORY_SIZE` | `128mb` | Memory size for artifact naming |
| `NANVIX_HOME` | *(set by setup)* | Path to sysroot |
| `GH_TOKEN` | *(none)* | GitHub token for API rate limits |
