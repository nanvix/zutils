# Contributing to nanvix-zutil

## Getting Started

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/) and
Python 3.12+.

```bash
git clone https://github.com/nanvix/zutils
cd zutils
uv sync                       # install project + dev dependencies
uv run tasks.py setup         # configure git hooks
```

## Development Workflow

The default branch is **`dev`** — all feature work targets `dev`.

### Dev Commands

| Command                      | Description                          |
|------------------------------|--------------------------------------|
| `uv run tasks.py lint`      | Lint (black, shfmt, shellcheck, PSScriptAnalyzer, yamllint) |
| `uv run tasks.py format`   | Fix formatting (black)               |
| `uv run tasks.py typecheck` | Strict type checking (pyright)      |
| `uv run tasks.py test`     | Run test suite (pytest)              |
| `uv run tasks.py ci`       | Run CI locally via `gh act`          |
| `uv run tasks.py clean`    | Remove caches and build artifacts    |
| `uv run tasks.py release`  | Bump, validate, commit, tag, and push a release |

### Code Quality Requirements

All code must pass before merging:

- **Formatting** — `black` with no configuration overrides.
- **Type checking** — `pyright` in strict mode. Every public function
  needs a complete type signature.
- **Tests** — `pytest`. Functional tests require the `ghcr.io/nanvix/toolchain-gcc:sha-34a3641`
  Docker image.
- **Docstrings** — all public functions must have docstrings.

### Running CI Locally

```bash
uv run tasks.py ci            # run all CI jobs (lint + test)
uv run tasks.py ci lint       # lint & type check only
uv run tasks.py ci test       # tests only
```

Requires Docker with the `ghcr.io/nanvix/toolchain-gcc:sha-34a3641` image available
locally, plus the `gh act` extension (`gh extension install nektos/gh-act`).

## Cutting a Release

Releases are triggered manually via
[workflow dispatch](/.github/workflows/release.yml). There is no
branch-push automation.

### Prerequisites

- All changes for the release are merged to `dev` and CI passes.
- The working tree on `dev` is clean.
- The tag `v<next-version>` does **not** already exist on `origin`.

### Steps

1. **Dispatch the Release workflow** from the CLI or GitHub UI:

   ```bash
   # bump type: patch (default), minor, or major
   gh workflow run release.yml -f bump=patch
   ```

   Or via the GitHub UI: **Actions → Release → Run workflow**, then choose
   `patch`, `minor`, or `major`.

2. **The workflow** automatically:

   - Validates `dev` is clean and computes the next version.
   - Validates the tag does not already exist on `origin`.
   - Runs lint, type checking, and tests.
   - Builds the wheel (`.whl`) and source distribution (`.tar.gz`).
   - Commits the version bump, creates and pushes the git tag `v<version>`.
   - Creates a GitHub release with the artifacts and auto-generated release notes.

3. **Verify the release** on the
   [GitHub releases page](https://github.com/nanvix/zutils/releases).

### Building Locally (Dry Run)

To preview what version would be cut without modifying the repo:

```bash
uv run tasks.py release --dry-run
```

This runs precondition checks, bumps the version, runs full validation
(lint, typecheck, tests), and prints what would be released — then resets
the version bump without committing.

### Post-Release

After the release is published:

- The git tag `v<version>` is pushed automatically by the workflow.
- Consumer repos can pin the new version in their bootstrap wrappers.

## Commit Message Format

```
[scope] T: Short title
```

- **Scope:** `zutils`, `ci`, `doc`, `git`, `tests`, `build`, `examples`.
- **Type:** `F` (feature), `B` (bugfix), `E` (enhancement), `W` (work in progress).
- **Title:** ≤ 50 characters.

Examples:

```
[zutils] F: Add lockfile staleness check
[tests] B: Fix basedpyright strict errors
[ci] E: Cache uv dependencies
```

## License

By contributing you agree that your contributions will be licensed under the
[MIT License](LICENSE).
