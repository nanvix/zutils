# Downstream consumer testing

The downstream test runner validates that a locally-built `nanvix-zutil`
wheel works correctly against all downstream consumer repositories.  It
checks out each consumer, installs the wheel into an isolated venv, and
runs the setup/build/test lifecycle.

## Quick start

```bash
# Setup-only (fast -- no docker required)
uv run tasks.py downstream --setup-only

# Full build+test with docker
uv run tasks.py downstream --with-docker

# Single consumer
uv run tasks.py downstream --setup-only nanvix/zlib

# Linux only (skip Windows even if WSL is available)
uv run tasks.py downstream --platform linux --setup-only
```

Run `uv run tasks.py downstream --help` for all available flags.

## How it works

1. **Wheel build** -- builds `nanvix-zutil` from the current working tree.
2. **Checkout** -- for each consumer, resolves the repo location and
   branch, fetches the latest, and resets to the target branch.  If the
   worktree has uncommitted changes, the reset is skipped to avoid
   clobbering local work.
3. **Lifecycle** -- creates a fresh venv, installs the wheel, then runs
   `nanvix-zutil setup` (and optionally `build` and `test`).
4. **Platform dispatch** -- on WSL, the runner can test both Linux and
   Windows in a single invocation and prints a combined summary at the end.

## Configuration: `downstream.json`

The config file lives at `scripts/downstream/downstream.json`.  It is
auto-generated from `consumer-repos.json` on first run if missing.

A JSON schema is provided at `scripts/downstream/downstream.schema.json`
for editor validation.

```json
{
    "$schema": "./downstream.schema.json",
    "defaults": {
        "checkout_strategy": "shallow",
        "repos_root": "~/repos",
        "win_repos_root": null,
        "branch_pattern": "nanvix/v*"
    },
    "consumers": [
        { "repo": "nanvix/zlib" },
        { "repo": "nanvix/cpython", "branch": "nanvix/v3.12.3" }
    ]
}
```

The `defaults` section sets the checkout strategy, repos root, and branch
pattern for all consumers.  Individual consumers can override `strategy`,
`branch`, or provide an absolute `path` to skip resolution entirely.

When a repo already exists on disk, the runner auto-detects its type
(bare+worktree vs clone) and overrides the configured strategy.  This
means `"checkout_strategy": "shallow"` works as a CI default while still
doing the right thing on a developer machine with bare repos.

## Running the unit tests

```bash
uv run tasks.py test-downstream
```
