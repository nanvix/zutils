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

## Dependency Groups

| Group | Scope | Contents |
| ----- | ----- | -------- |
| `[project] dependencies` | Runtime | `tomli-w` |
| `[project.optional-dependencies] lint` | Consumer repos | `black`, `pyright` — installed in consumer venvs via `nanvix-zutil[lint]` |
| `[dependency-groups] dev` | Dev only | `black`, `pyright`, `pytest`, `yamllint` |

## Git Hooks

`uv run tasks.py setup` points Git at the `.githooks/` directory, which
contains:

| Hook | What it does |
| ------ | ------------- |
| `commit-msg` | Validates `[module] (B\|E\|F\|W): Description` format. Valid modules: `zutils`, `ci`, `doc`, `git`, `tests`, `build`, `examples` |
| `pre-commit` | Runs `tasks.py lint` (black, shfmt, shellcheck, PSScriptAnalyzer, yamllint) + `tasks.py typecheck` (pyright) |
| `pre-push` | Runs `tasks.py lint` + `tasks.py typecheck` (same checks as pre-commit) |

## Environment Variables

These are used by the library at runtime (in consumer repos), not during
development of `nanvix-zutil` itself:

| Variable | Default | Purpose |
| --- | --- | --- |
| `NANVIX_TARGET` | `x86` | Target architecture |
| `NANVIX_MACHINE` | `microvm` | Target machine |
| `NANVIX_DEPLOYMENT_MODE` | `standalone` | Deployment mode |
| `NANVIX_MEMORY_SIZE` | `256mb` | Memory size for artifact naming |
| `NANVIX_SYSROOT` | *(set by setup)* | Path to runtime sysroot |
| `GH_TOKEN` | *(none)* | GitHub token for API rate limits |

## Project Layout

```text
zutils/
├── src/nanvix_zutil/          # Library source code
│   └── configs/               # Canonical tool configs synced to consumers
├── tests/                     # Test suite (pytest)
├── templates/                 # Bootstrap wrapper templates (z, z.sh, z.ps1)
├── examples/                  # Example consumer repos
├── docs/                      # Additional reference docs
├── docs/                      # Developer documentation
├── tasks.py                   # Dev task runner
├── pyproject.toml             # Project metadata + dependencies
└── .githooks/                 # Git hooks (commit-msg, pre-commit, pre-push)
```

## IDE Configuration

The project uses `pyright` in strict mode. Configuration lives in `pyrightconfig.json`

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
