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

The default branch is **`dev`** — all feature work targets `dev`.

## Dev Commands

| Command                         | Description                                     |
| ------------------------------- | ----------------------------------------------- |
| `uv run tasks.py lint`          | Lint (black, shfmt, shellcheck, yamllint, …)    |
| `uv run tasks.py format`        | Auto-format (black)                             |
| `uv run tasks.py typecheck`     | Strict type checking (pyright)                  |
| `uv run tasks.py test`          | Run test suite (pytest)                         |
| `uv run tasks.py clean`         | Remove caches and build artifacts               |
| `uv run tasks.py release`       | Bump, validate, commit, tag, and push a release |

## Code Style

- **Formatting** — `black`, configured in `pyproject.toml` 
- **License header** — source files begin with:

  ```python
  # Copyright(c) The Maintainers of Nanvix.
  # Licensed under the MIT License.
  ```

- **Section separators** — long modules use `# ---` banner comments to
  group related definitions.

## Type Safety

- `from __future__ import annotations` at the top of every module
  that uses forward references or non-runtime annotations.
- `pyright` in **strict** mode. Every public function has a complete
  type signature.
- Use `typing.cast()` when narrowing loosely-typed values (e.g. TOML
  dicts) rather than `# type: ignore`.

## Error Handling

User-facing errors go through `log.fatal()`, which prints a structured
message and calls `sys.exit()`:

```python
from nanvix_zutil import log
from nanvix_zutil.exitcodes import EXIT_MISSING_DEP

log.fatal("cargo not found", code=EXIT_MISSING_DEP, hint="install rustup")
```

`fatal()` is typed `NoReturn`. Reserve normal exceptions for internal
programmer errors.

Exit codes live in `nanvix_zutil.exitcodes` as `EXIT_*` constants — use
them instead of bare integers. Consumers rely on the deterministic
codes, so add new ones there rather than inventing values at call sites.

## Data Modeling

Prefer `@dataclass(frozen=True)` for value objects (manifests, refs,
resolved packages). Use `field(default_factory=...)` for mutable
defaults. Reach for a mutable dataclass only when the object genuinely
represents mutable state.

## Logging

Use the `log` module (`log.info`, `log.warning`, `log.fatal`, …) —
never bare `print()`. Log output goes to stderr.

## Docstrings

Google-style with `Args:`, `Returns:`, `Raises:` sections. All public
functions must have a docstring.

## Testing

- `unittest.TestCase` classes run under `pytest`.
- One test module per source module (`tests/test_<module>.py`);
  supplementary `tests/test_<module>_<slice>.py` files are fine when a
  single file gets unwieldy.
- Test `fatal()` paths with `assertRaises(SystemExit)` and assert on
  `ctx.exception.code`.
- Shared helpers live in `tests/testutils.py`.
- Functional tests require the immutable Nanvix C/Clang SDK Docker
  image.

## Imports

`from __future__ import annotations` comes first. Then stdlib, then
intra-package imports (`from nanvix_zutil import log`). No relative
imports.

## Commit Messages

```
[scope] T: Short title
```

- **Scope:** `zutils`, `ci`, `doc`, `git`, `tests`, `build`, `examples`.
- **Type:** `F` (feature), `B` (bugfix), `E` (enhancement), `W` (work in progress).
- **Title:** ≤ 50 characters.

Examples:

```
[zutils] F: Add lockfile staleness check
[tests] B: Fix pyright strict errors
[ci] E: Cache uv dependencies
```

## Keeping Docs Current

`AGENTS.md`, `CONTRIBUTING.md`, `README.md`, and `docs/` should be
reviewed with every PR and updated when the code they describe changes.

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

   Or via the GitHub UI: **Actions → Release → Run workflow**, then
   choose `patch`, `minor`, or `major`.

2. **The workflow** automatically:

   - Validates `dev` is clean and computes the next version.
   - Validates the tag does not already exist on `origin`.
   - Runs lint, type checking, and tests.
   - Builds the wheel (`.whl`) and source distribution (`.tar.gz`).
   - Commits the version bump, creates and pushes `v<version>`.
   - Creates a GitHub release with artifacts and auto-generated notes.

3. **Verify the release** on the
   [GitHub releases page](https://github.com/nanvix/zutils/releases).

### Building Locally (Dry Run)

Preview what would be cut without modifying the repo:

```bash
uv run tasks.py release --dry-run
```

This runs precondition checks, bumps the version, runs full validation
(lint, typecheck, tests), prints what would be released, then resets
the version bump without committing.

## License

By contributing you agree that your contributions will be licensed
under the [MIT License](LICENSE).
