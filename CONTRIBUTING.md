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
| `uv run tasks.py lint`      | Check formatting (black)             |
| `uv run tasks.py format`   | Fix formatting (black)               |
| `uv run tasks.py typecheck` | Strict type checking (pyright)      |
| `uv run tasks.py test`     | Run test suite (pytest)              |
| `uv run tasks.py ci`       | Run CI locally via `gh act`          |
| `uv run tasks.py clean`    | Remove caches and build artifacts    |
| `uv run tasks.py release`  | Build wheel + sdist locally          |

### Code Quality Requirements

All code must pass before merging:

- **Formatting** — `black` with no configuration overrides.
- **Type checking** — `pyright` in strict mode. Every public function
  needs a complete type signature.
- **Tests** — `pytest`. Functional tests require the `nanvix/toolchain`
  Docker image and `/dev/kvm`.
- **Docstrings** — all public functions must have docstrings.

### Running CI Locally

```bash
uv run tasks.py ci            # run all CI jobs (lint + test)
uv run tasks.py ci lint       # lint & type check only
uv run tasks.py ci test       # tests only
```

Requires Docker with the `nanvix/toolchain:latest-minimal` image available
locally, plus the `gh act` extension (`gh extension install nektos/gh-act`).

## Cutting a Release

### Prerequisites

- All changes for the release are merged to `dev`.
- `pyproject.toml` has the **exact version** you intend to release (e.g.
  `version = "0.3.0"`).
- The tag `v<version>` does **not** already exist on `origin`.
- CI passes on `dev`.

### Steps

1. **Bump the version** in `pyproject.toml` on `dev` (if not already done).

2. **Create the release branch** from `dev`:

   ```bash
   git checkout dev
   git pull origin dev
   git checkout -b release/v0.3.0   # must be release/vX.Y.Z
   git push origin release/v0.3.0
   ```

   > The release workflow triggers on `push` to branches matching
   > `release/v*`.

3. **Wait for the workflow.** Pushing the branch automatically triggers the
   [Release workflow](/.github/workflows/release.yml), which:

   - Runs lint, type checking, and tests.
   - Validates the version in `pyproject.toml` matches the branch name.
   - Validates the tag does not already exist.
   - Builds the wheel (`.whl`) and source distribution (`.tar.gz`).
   - Creates and pushes the git tag `v<version>`.
   - Creates a GitHub release with the artifacts and auto-generated release notes.

4. **Verify the release** on the
   [GitHub releases page](https://github.com/nanvix/zutils/releases).

### Alternative: Manual Workflow Dispatch

Instead of pushing a branch, you can trigger the release from the GitHub UI:

1. Go to **Actions → Release → Run workflow**.
2. Enter the version (must match `pyproject.toml`).
3. The workflow runs the same pipeline as above.

### Building Locally (Dry Run)

To build artifacts without publishing:

```bash
uv run tasks.py release
```

This produces the wheel and sdist in `dist/` for inspection.

### Post-Release

After the release is published:

- The git tag `v<version>` is pushed automatically by the workflow.
- Consumer repos can pin the new version in their bootstrap wrappers.

> **Note:** PyPI publishing is not yet active — the publish step is present in
> the workflow but commented out.

## Commit Message Format

```
[scope] T: Short title
```

- **Scope:** `zutils`, `tests`, `examples`, `ci`, etc.
- **Type:** `F` (feature), `B` (bugfix), `E` (enhancement).
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
