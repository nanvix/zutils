# Test

How to run the `nanvix-zutil` test suite.

## Running All Tests

```bash
uv run tasks.py test
```

This runs `pytest` with verbose output against the `tests/` directory.

## Running Specific Tests

Run a single test file:

```bash
uv run pytest tests/test_config.py -v
```

Run a specific test function:

```bash
uv run pytest tests/test_config.py::TestConfig::test_get_default -v
```

Run tests matching a keyword:

```bash
uv run pytest tests/ -k "docker" -v
```

## Test Structure

```
tests/
├── test_buildroot.py     # Buildroot + Dependency tests
├── test_cli.py           # CLI argument parsing tests
├── test_cli_lock.py      # Lock subcommand CLI tests
├── test_config.py        # Config persistence tests
├── test_docker.py        # Docker integration tests
├── test_examples.py      # Example project integration tests
├── test_github.py        # GitHub API client tests
├── test_info.py          # nanvix-info CLI tests
├── test_integration.py   # End-to-end integration tests
├── test_lockfile.py      # Lockfile read/write tests
├── test_log.py           # Structured logging tests
├── test_main.py          # __main__ entry point tests
├── test_manifest.py      # Manifest parser tests
├── test_release.py       # Release packaging tests
├── test_resolve_cmd.py   # Resolve CLI tests
├── test_resolver.py      # Dependency resolver tests
├── test_script.py        # ZScript base class tests
├── test_sysroot.py       # Sysroot download/verify tests
└── testutils.py          # Shared test utilities
```

## Test Categories

### Unit Tests

Most test files are unit tests that mock external dependencies (GitHub API,
filesystem, Docker). These run quickly without network access or Docker.

### Integration Tests

`test_examples.py` and `test_integration.py` exercise the full lifecycle
with real example projects. These may require:

- Network access (for GitHub API calls)
- Docker with the `ghcr.io/nanvix/toolchain-gcc:sha-34a3641` image

### Running CI Locally

To run the full CI pipeline locally (requires Docker + `gh act`):

```bash
uv run tasks.py ci            # run all CI jobs
uv run tasks.py ci lint       # lint & typecheck only
uv run tasks.py ci test       # tests only
```

This requires:
- Docker with `ghcr.io/nanvix/toolchain-gcc:sha-34a3641` image available locally
- The `gh` CLI with the `act` extension (`gh extension install nektos/gh-act`)

## Code Coverage

To run tests with coverage (install `pytest-cov` first):

```bash
uv run pytest tests/ --cov=src/nanvix_zutil --cov-report=term-missing -v
```

## Pre-Push Checks

The `pre-push` git hook automatically runs formatting and type checking
before pushing. To run these manually:

```bash
uv run tasks.py lint          # formatting + shell + yaml checks
uv run tasks.py typecheck     # strict pyright
```
