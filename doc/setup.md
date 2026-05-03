# Setup

Developer setup instructions for contributing to `nanvix-zutil`.

## Prerequisites

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/getting-started/installation/)** — fast Python package manager

Optional (for full linting):

- [shfmt](https://github.com/mvdan/sh) — shell script formatting
- [shellcheck](https://www.shellcheck.net/) — shell script analysis
- [pwsh](https://github.com/PowerShell/PowerShell) + PSScriptAnalyzer — PowerShell linting

## Clone and Install

```bash
git clone https://github.com/nanvix/zutils
cd zutils
uv sync                       # install project + dev dependencies
uv run tasks.py setup         # configure git hooks
```

`uv sync` creates a `.venv/` virtualenv and installs all dependencies
(runtime + dev group) declared in `pyproject.toml`.

## Git Hooks

`uv run tasks.py setup` points Git at the `.githooks/` directory, which
contains:

| Hook | What it does |
|------|-------------|
| `commit-msg` | Validates `[scope] T: Description` format (B, E, F, or W) |
| `pre-commit` | Runs formatting checks |
| `pre-push` | Runs formatting (black) and type checking (pyright) |

## Environment Variables

These are used by the library at runtime (in consumer repos), not during
development of `nanvix-zutil` itself:

| Variable | Default | Purpose |
|---|---|---|
| `NANVIX_TARGET` | `x86` | Target architecture |
| `NANVIX_MACHINE` | `microvm` | Target machine |
| `NANVIX_DEPLOYMENT_MODE` | `standalone` | Deployment mode |
| `NANVIX_MEMORY_SIZE` | `256mb` | Memory size for artifact naming |
| `NANVIX_SYSROOT` | *(set by setup)* | Path to runtime sysroot |
| `NANVIX_BUILDROOT` | *(set by setup)* | Path to build-time root |
| `GH_TOKEN` | *(none)* | GitHub token for API rate limits |

## Project Layout

```
zutils/
├── src/nanvix_zutil/   # Library source code
├── tests/              # Test suite (pytest)
├── templates/          # Bootstrap wrapper templates (z, z.sh, z.ps1)
├── examples/           # Example consumer repos
├── docs/               # Additional reference docs
├── doc/                # Developer documentation
├── tasks.py            # Dev task runner
├── pyproject.toml      # Project metadata + dependencies
└── .githooks/          # Git hooks (commit-msg, pre-push)
```

## IDE Configuration

The project uses `pyright` in strict mode. Configuration is in `pyproject.toml`:

```toml
[tool.pyright]
include = ["src", "tests"]
pythonVersion = "3.12"
typeCheckingMode = "strict"
venvPath = "."
venv = ".venv"
```

For VS Code, install the Pylance extension which uses pyright internally.
The `.venv` created by `uv sync` will be auto-detected.

## Next Steps

- [Build instructions](build.md)
- [Test instructions](test.md)
- [Design overview](design.md)
