_The following is a copilot generated build schema for this repository. It goes over the functionality requirements for this library._

# `nanvix_zutil` — Build Schema (v2)

## Context

- https://github.com/nanvix/nanvix
- https://github.com/nanvix/zlib
- https://github.com/nanvix/bzip2
- https://github.com/nanvix/cpython
- https://github.com/ada-x64/nanvix.obs/blob/main/copilot/repo-notes/nanvix/nanvix/nanvix-build-system.md

## Overview

`nanvix_zutil` is a **stdlib-only Python 3.12+ library** with two responsibilities:

1. **Dependency management** — Download and verify the Nanvix runtime sysroot and library release artifacts from GitHub.
2. **Programmatic API / CLI** — Provide a `ZScript` base class with lifecycle hooks (`setup`, `build`, `test`, `release`, `clean`), argument parsing, structured logging, config persistence, and deterministic exit codes.

`nanvix_zutil` is agnostic to what a consumer repository builds. It does not know or care whether the output is a static library, an ELF binary, or a distribution tarball. All build logic — toolchain resolution, compiler invocation, Docker wrapping, make targets — lives in the consumer `z.py` script, which subclasses `ZScript` and implements the lifecycle hooks.

Each consumer repository ships thin bootstrap wrappers — `z` (Bash) and `z.ps1` (PowerShell) — at the repository root. These locate a Python interpreter and delegate to `.nanvix/z.py`, following the same pattern as [Rust's `x` / `x.ps1`](https://rustc-dev-guide.rust-lang.org/building/how-to-build-and-run.html#what-is-xpy). Users invoke `./z <subcommand>` on any platform.

### Goals

1. **Consistency** — Every Nanvix repo uses the same `ZScript` API with identical subcommands.
2. **Maintainability** — Type-checked (`basedpyright` strict), formatted (`black`), testable.
3. **Discoverability** — `--help` on every command, auto-generated docs on Read the Docs.
4. **Automatability** — Programmatic API for CI and LLM agents. Structured JSON output. Deterministic exit codes. No interactive prompts.

### Non-goals

`nanvix_zutil` does **not** own:

- Toolchain resolution or Docker integration (consumer logic)
- Compiler flags, linker scripts, or make targets (consumer logic, typically in `Makefile.nanvix`)
- Knowledge of what is being built (library vs binary vs distribution)

---

## Constraints

| Constraint | Value | Rationale |
|---|---|---|
| Python version | **3.12+** | Matches the Nanvix CPython port (3.12.3) |
| Dependencies | **stdlib-only** | `nanvix_zutil` builds the ecosystem; it cannot depend on it |
| Distribution | **PyPI** (`pip install nanvix-zutil==X.Y.Z`) | Standard; consumer scripts pin a specific version |
| Type checker | **basedpyright** (strict) | Strict type safety |
| Formatter | **black** | Opinionated, standard |
| Platform | **Linux, Windows** | Linux native; Windows via PowerShell bootstrap (`z.ps1`) |

---

## Architecture

### Repository layout (`nanvix/zutil`)

```
nanvix/zutil/
├── pyproject.toml
├── README.md
├── LICENSE
├── src/
│   └── nanvix_zutil/
│       ├── __init__.py        # Public API re-exports
│       ├── cli.py             # Argument parser, subcommand dispatch, --json, --help
│       ├── config.py          # Environment persistence (.nanvix/env.json)
│       ├── github.py          # GitHub release download, retry, GH_TOKEN
│       ├── log.py             # Colored logging, --json mode, structured errors
│       ├── script.py          # ZScript base class (lifecycle hooks + self.run())
│       └── sysroot.py         # Sysroot + Dependency download, extraction, verification
├── templates/
│   ├── z                      # Bootstrap wrapper template (Bash)
│   └── z.ps1                  # Bootstrap wrapper template (PowerShell)
├── tests/
│   └── ...
└── docs/
    └── conf.py                # Read the Docs / Sphinx configuration
```

### Consumer repository layout

The consumer `z.py` and all Nanvix state live inside `.nanvix/`. The only files at the repository root are the bootstrap wrappers `z` and `z.ps1`.

```
nanvix/<any-repo>/
├── z                              # Bash bootstrap wrapper (finds Python, runs .nanvix/z.py)
├── z.ps1                          # PowerShell bootstrap wrapper
├── .nanvix/
│   ├── z.py                       # Consumer build script (subclasses ZScript)
│   └── venv/                      # Auto-created virtual environment (contains nanvix_zutil)
├── Makefile.nanvix                # Existing build rules (compile, link, test, package)
└── ...                            # Upstream / project files
```

### Bootstrap chain

Invocation flows through two layers:

#### Layer 1 — Bootstrap wrappers (`z` / `z.ps1`)

Small scripts (~50 lines) at the repo root whose only job is to find Python ≥ 3.12 and exec `.nanvix/z.py`. Modeled on [Rust's `x`](https://github.com/rust-lang/rust/blob/main/x) and [`x.ps1`](https://github.com/rust-lang/rust/blob/main/x.ps1).

**`z` (Bash)** — searches `python3`, `python`, `py -3` in `$PATH`, handles `$OSTYPE` for Cygwin/MSYS.

**`z.ps1` (PowerShell)** — searches `py`, `python3`, `python` via `Get-Command`, passes `-3` to `py`.

If PowerShell script execution is disabled:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

#### Layer 2 — Self-bootstrapping in `z.py`

The top ~20 lines of `z.py` (stdlib only: `subprocess`, `sys`, `os`, `venv`) handle:

1. Check if `.nanvix/venv/` exists.
2. If not, create it with `python3 -m venv .nanvix/venv`.
3. Install the **pinned** `nanvix-zutil` version (`pip install nanvix-zutil==X.Y.Z`).
4. Re-execute itself under `.nanvix/venv/bin/python`.
5. On subsequent runs, skip straight to execution.

**Invocation:**

```bash
./z setup
./z build
./z test
./z release
./z clean
./z --help
```

---

## API

### `ZScript` (`nanvix_zutil.script`)

```python
class ZScript:
    """Base class for consumer build scripts.

    Provides CLI dispatch, config management, subprocess execution,
    and structured logging. Consumers subclass this and implement
    the lifecycle hooks.
    """

    config: Config
    repo_root: Path            # Root of the consumer repository
    nanvix_dir: Path           # Path to .nanvix/ directory

    # --- Lifecycle hooks (override in subclass) ---
    def setup(self) -> None: ...
    def build(self) -> None: ...
    def test(self) -> None: ...
    def release(self) -> None: ...
    def clean(self) -> None: ...

    # --- Provided by nanvix_zutil ---
    def run(
        self, *args: str,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run a subprocess. Logs the command, raises on non-zero exit."""
        ...

    @classmethod
    def main(cls) -> None:
        """CLI entry point. Parses args and dispatches to the
        appropriate lifecycle hook."""
        ...
```

### `Config` (`nanvix_zutil.config`)

```python
class Config:
    """Persistent key-value configuration stored at .nanvix/env.json.

    Reads defaults from environment variables, then from the persisted file.
    Environment variables always win.
    """

    platform: str              # Default: "hyperlight" (from NANVIX_PLATFORM)
    process_mode: str          # Default: "multi-process" (from NANVIX_PROCESS_MODE)
    memory_size: str           # Default: "128mb" (from NANVIX_MEMORY_SIZE)

    def get(self, key: str, default: str | None = None) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def save(self) -> None: ...
    def load(self) -> None: ...
```

### `Sysroot` (`nanvix_zutil.sysroot`)

```python
class Sysroot:
    """Manages the Nanvix sysroot directory."""

    path: Path

    @staticmethod
    def download(
        platform: str,
        process_mode: str,
        memory_size: str,
        gh_token: str | None = None,
        dest: Path | None = None,
    ) -> "Sysroot":
        """Download and extract the Nanvix runtime artifact from GitHub releases."""
        ...

    def install_dep(self, dep: "Dependency") -> None:
        """Download a dependency release and install .a + headers into the sysroot."""
        ...

    def verify(self, required_libs: list[str]) -> None:
        """Assert that all required files are present. Raises on failure."""
        ...
```

### `Dependency` (`nanvix_zutil.sysroot`)

```python
@dataclass
class Dependency:
    """A library dependency fetched from a GitHub release."""

    name: str                  # e.g., "sqlite"
    repo: str                  # e.g., "nanvix/sqlite"
    artifact_pattern: str = "{name}-{platform}-{mode}-{mem}.tar.bz2"
    install_libs: list[str] | None = None
    install_headers: list[str] | None = None
```

### `log` (`nanvix_zutil.log`)

```python
def info(msg: str) -> None: ...
def success(msg: str) -> None: ...
def warning(msg: str) -> None: ...
def error(msg: str, hint: str | None = None) -> None: ...
def fatal(msg: str, hint: str | None = None) -> NoReturn: ...
def set_json_mode(enabled: bool) -> None: ...
```

### CLI (`nanvix_zutil.cli`)

Built internally by `ZScript.main()`. Consumer scripts do not construct it manually.

```
usage: ./z [-h] [--json] [--version] {setup,build,test,release,clean} ...

Nanvix build script.

positional arguments:
  {setup,build,test,release,clean}
    setup               Prepare the build environment
    build               Build the project
    test                Run tests
    release             Package a release
    clean               Remove build artifacts

options:
  -h, --help            Show this help message and exit
  --json                Emit machine-parseable JSON output
  --version             Show nanvix_zutil version
```

Consumer scripts can register additional CLI arguments per subcommand via the `ZScript` API.

---

## Consumer example

All consumers follow the same pattern. The `setup()` hook uses `nanvix_zutil` to download dependencies. The remaining hooks call `self.run()` to invoke whatever build system the repository uses — typically `make -f Makefile.nanvix`.

```python
from nanvix_zutil import ZScript, Sysroot, Dependency

class MyBuild(ZScript):
    """Build script for nanvix/<repo>."""

    # Optional: declare library dependencies needed from other Nanvix repos.
    # Leaf libraries (zlib, bzip2) have none. CPython depends on several.
    DEPS: list[Dependency] = [
        # Dependency(name="zlib", repo="nanvix/zlib"),
        # Dependency(name="bzip2", repo="nanvix/bzip2"),
    ]

    def setup(self) -> None:
        sysroot = Sysroot.download(
            platform=self.config.platform,
            process_mode=self.config.process_mode,
            memory_size=self.config.memory_size,
            gh_token=self.config.get("GH_TOKEN"),
        )
        for dep in self.DEPS:
            sysroot.install_dep(dep)
        sysroot.verify(required_libs=["libposix.a"])
        self.config.set("NANVIX_HOME", str(sysroot.path))
        self.config.save()

    def build(self) -> None:
        self.config.load()
        self.run(
            "make", "-f", "Makefile.nanvix",
            "CONFIG_NANVIX=y",
            f"NANVIX_HOME={self.config.get('NANVIX_HOME')}",
            "all",
            cwd=self.repo_root,
        )

    def test(self) -> None:
        self.config.load()
        self.run(
            "make", "-f", "Makefile.nanvix",
            "CONFIG_NANVIX=y",
            f"NANVIX_HOME={self.config.get('NANVIX_HOME')}",
            "test",
            cwd=self.repo_root,
        )

    def release(self) -> None:
        self.config.load()
        self.run(
            "make", "-f", "Makefile.nanvix",
            "CONFIG_NANVIX=y",
            f"NANVIX_HOME={self.config.get('NANVIX_HOME')}",
            "package",
            cwd=self.repo_root,
        )

    def clean(self) -> None:
        self.run("make", "-f", "Makefile.nanvix", "clean", cwd=self.repo_root)

if __name__ == "__main__":
    MyBuild.main()
```

The only thing that varies between consumers is the `DEPS` list and whatever extra logic their hooks need. `nanvix_zutil` handles everything else.

---

## Environment Variables

`Config` reads these at startup. Environment variables override `.nanvix/env.json`.

| Variable | Default | Purpose |
|---|---|---|
| `NANVIX_PLATFORM` | `hyperlight` | Target platform |
| `NANVIX_PROCESS_MODE` | `multi-process` | Process mode |
| `NANVIX_MEMORY_SIZE` | `128mb` | Memory size for artifact naming |
| `NANVIX_HOME` | *(auto-set by setup)* | Path to sysroot |
| `GH_TOKEN` | *(none)* | GitHub token for API rate limit avoidance |

---

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error |
| 2 | Invalid arguments / CLI usage error |
| 3 | Missing dependency (sysroot, library) |
| 4 | Network error (download failure after retries) |
| 5 | Build failure (subprocess returned non-zero) |
| 6 | Test failure |

In `--json` mode, errors are emitted as JSON:

```json
{"level": "error", "code": 3, "message": "libz.a not found in sysroot", "hint": "Run `./z setup` to download dependencies."}
```

---

## Acceptance Criteria

### Functional

- `./z setup && ./z build` succeeds in any migrated Nanvix consumer repo
- `./z test` runs the repository's test suite
- `./z release` produces the repository's release artifact
- `./z --help` and `./z <subcommand> --help` print accurate help
- All commands return 0 on success, non-zero on failure with structured error
- `./z` finds Python on Linux, macOS, WSL, and Windows PowerShell

### Confinement

- `nanvix_zutil` creates no files outside `.nanvix/`
- The consumer `z.py` lives at `.nanvix/z.py`
- Bootstrap wrappers `z` and `z.ps1` are the only Nanvix files at the repository root
- The venv lives at `.nanvix/venv/`; config at `.nanvix/env.json`

### Quality

- `basedpyright` passes in strict mode
- `black --check` passes
- All public functions have docstrings

---

## Platform Matrix

All Nanvix consumer repos build across the same set of platform configurations. The `z.py` script reads these from `Config` (environment variables or `.nanvix/env.json`):

| Configuration | `NANVIX_PLATFORM` | `NANVIX_PROCESS_MODE` | `NANVIX_MEMORY_SIZE` |
|---|---|---|---|
| hyperlight-multi-process-128mb | `hyperlight` | `multi-process` | `128mb` |
| hyperlight-single-process-128mb | `hyperlight` | `single-process` | `128mb` |
| microvm-single-process-128mb | `microvm` | `single-process` | `128mb` |
| microvm-multi-process-128mb | `microvm` | `multi-process` | `128mb` |
| microvm-standalone-128mb | `microvm` | `standalone` | `128mb` |

---

## Implementation Plan

### Phase 1: Scaffold (`nanvix/zutil` repo)

1. Create the `nanvix/zutil` repository
2. Set up `pyproject.toml` (hatchling, `requires-python >=3.12`, no deps, black + basedpyright config)
3. Create `src/nanvix_zutil/` package structure
4. Set up GitHub Actions CI (type check, lint, format, test)

### Phase 2: Core library

1. `log.py` — colored output, `--json` mode, `fatal()` with hints
2. `config.py` — load/save/get/set against `.nanvix/env.json`, env var override
3. `github.py` — download release artifacts with retry + exponential backoff + `GH_TOKEN`
4. `sysroot.py` — `Sysroot` and `Dependency` classes
5. `cli.py` — argparse subcommand dispatch, `--json`, `--help`, `--version`
6. `script.py` — `ZScript` base class with `main()`, `run()`, lifecycle hooks

### Phase 3: Testing

1. Unit tests for each module (stdlib `unittest`)
2. Integration test: mock consumer `z.py` exercising the full lifecycle
3. Validate `basedpyright --strict`, `black --check` in CI

### Phase 4: Publish and bootstrap wrappers

1. Publish `nanvix-zutil` 0.1.0 to PyPI
2. Write the `z.py` bootstrap preamble template (~20-line self-bootstrapping header)
3. Write `z` (Bash) — modeled on [Rust's `x`](https://github.com/rust-lang/rust/blob/main/x)
4. Write `z.ps1` (PowerShell) — modeled on [Rust's `x.ps1`](https://github.com/rust-lang/rust/blob/main/x.ps1)
5. Test on Linux, macOS, WSL, Windows PowerShell, Git Bash

### Phase 5: First consumers (`nanvix/zlib`, `nanvix/bzip2`)

1. Write `.nanvix/z.py` and add `z`/`z.ps1` to each repo
2. Validate `./z setup && ./z build && ./z test && ./z release`
3. Run existing `nanvix-ci.yml` against `./z` for all 5 platform configurations
4. Wire CI to use `./z` instead of raw `make -f Makefile.nanvix`

### Phase 6: CPython and beyond

1. `nanvix/cpython` — same pattern, with library `DEPS`
2. `nanvix/nanvix-python` — same pattern, more complex hooks
3. Remove old Bash `z` scripts from all migrated repos

