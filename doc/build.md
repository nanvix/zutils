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

## Building Release Artifacts

Build a wheel (`.whl`) and source distribution (`.tar.gz`):

```bash
uv run tasks.py release
```

This produces artifacts in `dist/` for inspection. The actual release
process is handled by the CI workflow (see [CONTRIBUTING.md](../CONTRIBUTING.md)).

## Version Bumping

Bump the version across `pyproject.toml` and all template files:

```bash
uv run tasks.py version patch       # 0.7.30 → 0.7.31
uv run tasks.py version minor       # 0.7.30 → 0.8.0
uv run tasks.py version major       # 0.7.30 → 1.0.0
uv run tasks.py version patch --dry-run  # preview without writing
```

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
| `uv run tasks.py release` | Build wheel + sdist |
| `uv run tasks.py version` | Bump version |
| `uv run tasks.py shell-lint` | Lint shell scripts |
| `uv run tasks.py shell-format` | Format shell scripts |
| `uv run tasks.py yaml-lint` | Lint YAML files |
