# Troubleshooting

Solutions to common problems when developing or using `nanvix-zutil`.

## Development Issues

### `uv sync` fails

**Symptom:** `uv sync` reports dependency resolution errors.

**Solution:** Ensure you have Python 3.12+ installed and `uv` is up to date:

```bash
uv self update
uv sync
```

### pyright reports errors after pulling changes

**Symptom:** Type checking fails with import errors or missing attributes.

**Solution:** Re-sync dependencies and clear caches:

```bash
uv sync
uv run tasks.py clean
uv run tasks.py typecheck
```

### Git hooks not running

**Symptom:** Commits go through without validation.

**Solution:** Re-run setup to configure the hooks path:

```bash
uv run tasks.py setup
```

Verify the hooks directory:

```bash
git config --local core.hooksPath
# Should output: .githooks
```

### `shfmt` / `shellcheck` not found

**Symptom:** `uv run tasks.py lint` skips shell checks.

**Solution:** Install the tools:

```bash
# Ubuntu/Debian
sudo apt-get install shfmt shellcheck

# Or via Go (shfmt)
go install mvdan.cc/sh/v3/cmd/shfmt@latest
```

These are optional — Python linting still runs without them.

## Consumer / Runtime Issues

### `nanvix-zutil setup` fails with network errors

**Symptom:** Exit code 4, messages about failed downloads.

**Solutions:**

1. Check internet connectivity.
2. Set `GH_TOKEN` to avoid GitHub API rate limits:
   ```bash
   export GH_TOKEN=$(gh auth token)
   nanvix-zutil setup
   ```
3. Retry — the library has automatic retries with exponential backoff,
   but persistent network issues may still fail.

### Exit code 7 (degraded setup)

**Symptom:** Setup completes but exits with code 7.

**Explanation:** The exact dependency version was not found and the resolver
fell back to the best available release. The build may still work but is
not using the pinned versions.

**Solutions:**

1. Update `nanvix.toml` to reference an available version.
2. Run `nanvix-zutil lock` to refresh the lockfile.
3. Check GitHub releases for the dependency to confirm available versions.

### Docker permission denied

**Symptom:** `docker run` fails with permission errors during build/test.

**Solutions:**

1. Ensure your user is in the `docker` group:
   ```bash
   sudo usermod -aG docker $USER
   # Log out and back in
   ```
2. Check Docker is running:
   ```bash
   docker info
   ```

### Sysroot verification fails

**Symptom:** Setup reports missing required files in the sysroot.

**Solutions:**

1. Clean and re-download:
   ```bash
   rm -rf .nanvix/sysroot .nanvix/env.json
   nanvix-zutil setup
   ```
2. If using `--with-nanvix`, ensure the local build has all required
   artifacts in `bin/` and `lib/`.

### Lockfile is stale

**Symptom:** `nanvix-zutil lock --check` exits with code 2.

**Solution:** Regenerate the lockfile:

```bash
nanvix-zutil lock
git add .nanvix/nanvix.lock
git commit -m "[project] E: Update lockfile"
```

### Bootstrap wrapper can't find Python 3.12+

**Symptom:** `./z` or `./z.sh` fails with "Python 3.12+ required".

**Solutions:**

1. Install Python 3.12+:
   ```bash
   # Ubuntu/Debian
   sudo apt-get install python3.12
   ```
2. Ensure it's on PATH or set `PYTHON` environment variable:
   ```bash
   PYTHON=python3.12 ./z setup
   ```

### Windows: paths not translating correctly

**Symptom:** Docker mounts fail or paths contain backslashes inside containers.

**Explanation:** `nanvix_zutil` automatically translates Windows paths to
Docker-compatible MSYS-style paths (`C:\foo` → `/c/foo`). On Windows, a
tar-copy strategy is used instead of bind mounts.

**Solution:** Ensure Docker Desktop is running and WSL integration is enabled.

## Exit Code Reference

| Code | Meaning | Common Cause |
|------|---------|-------------|
| 0 | Success | — |
| 1 | General error | Unhandled exception |
| 2 | Invalid arguments | Wrong CLI flags or stale lockfile |
| 3 | Missing dependency | Dependency not found on GitHub |
| 4 | Network error | GitHub API unreachable or rate-limited |
| 5 | Build failure | Compiler error in consumer code |
| 6 | Test failure | Tests did not pass |
| 7 | Degraded setup | Version fallback was used |
