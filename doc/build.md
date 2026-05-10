# Build

How to build `nanvix-zutil` for development and release.

## Development Build

After [setup](setup.md), the project is already installed in editable mode
via `uv sync`. There is no separate build step required for development —
source changes are immediately reflected.

## Formatting

Format all Python source code with `black`:

```bash
uv run tasks.py format
```

Check formatting without modifying files:

```bash
uv run tasks.py lint
```

The `lint` command checks Python formatting (black), shell script formatting
(shfmt), shell correctness (shellcheck), PowerShell linting (PSScriptAnalyzer),
and YAML linting (yamllint).

## Type Checking

Run strict type checking with `pyright`:

```bash
uv run tasks.py typecheck
```

All code must pass strict type checking. Every public function must have a
complete type signature.

## Shell Scripts

Format shell scripts:

```bash
uv run tasks.py shell-format
```

Lint shell scripts (shfmt + shellcheck + PSScriptAnalyzer):

```bash
uv run tasks.py shell-lint
```

## YAML Linting

```bash
uv run tasks.py yaml-lint
```

## Releasing

Cut a new release (bump version, validate, commit, push):

```bash
uv run tasks.py release              # patch bump (default)
uv run tasks.py release minor        # minor bump
uv run tasks.py release major        # major bump
uv run tasks.py release --dry-run    # preview without committing
```

This checks preconditions (dev branch, clean tree), bumps the version in
`pyproject.toml`, runs validation (lint, typecheck, tests), ensures the
tag is available, then commits and pushes. CI then builds artifacts,
creates the GitHub Release, and updates downstream consumers.

In CI mode (`--ci`), the command additionally builds wheel/sdist, patches
and packages templates, creates the GitHub Release, and triggers consumer
updates directly.

## All Dev Commands

| Command | Description |
|---------|-------------|
| `uv run tasks.py setup` | Configure git hooks |
| `uv run tasks.py lint` | Run all linters (black, shfmt, shellcheck, PSScriptAnalyzer, yamllint) |
| `uv run tasks.py format` | Fix Python formatting (black) |
| `uv run tasks.py typecheck` | Strict type checking (pyright) |
| `uv run tasks.py test` | Run test suite (pytest) |
| `uv run tasks.py ci` | Run CI locally via `gh act` |
| `uv run tasks.py clean` | Remove caches and build artifacts |
| `uv run tasks.py release` | Cut a new release (bump, validate, tag, push) |
| `uv run tasks.py shell-lint` | Lint shell scripts |
| `uv run tasks.py shell-format` | Format shell scripts |
| `uv run tasks.py yaml-lint` | Lint YAML files |
