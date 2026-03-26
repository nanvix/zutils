# nanvix-zutil

Build orchestration utilities for the [Nanvix](https://github.com/nanvix/nanvix) ecosystem.

`nanvix_zutil` is a Python 3.12+ library that provides a unified `ZScript`
base class with lifecycle hooks (`setup`, `build`, `test`, `benchmark`,
`release`, `clean`), structured logging, config persistence, GitHub release
artifact downloading, lockfile-based dependency resolution, and deterministic
exit codes.

## Installation

```bash
pip install nanvix-zutil
```

## Usage

Consumer repositories subclass `ZScript` in `.nanvix/z.py`:

```python
from nanvix_zutil import ZScript, Buildroot, Sysroot

class MyBuild(ZScript):
    def setup(self) -> None:
        sysroot = Sysroot.download(
            machine=self.config.machine,
            deployment_mode=self.config.deployment_mode,
            memory_size=self.config.memory_size,
            tag="v1.0.0",
        )
        self.config.set("NANVIX_SYSROOT", str(sysroot.path))
        self.config.save()

    def build(self) -> None:
        self.run("make", "-f", "Makefile.nanvix", "all")

if __name__ == "__main__":
    MyBuild.main()
```

Then invoke via the bootstrap wrapper at the repo root:

```bash
./z lock
./z setup
./z build
./z test
```

### Lockfile

`./z lock` resolves the full dependency graph (including transitive
dependencies) and writes a pinned `nanvix.lock` file to `.nanvix/`.
Use `--check` in CI to verify the lockfile is up-to-date, or `--shallow`
to resolve only direct dependencies (for publishing as a release asset).

```bash
./z lock              # resolve all deps and write nanvix.lock
./z lock --check      # verify lockfile is fresh (exit 3 if missing, 2 if stale)
./z lock --shallow    # resolve direct deps only (for CI release assets)
```

## Examples

- [`examples/hello-world/`](examples/hello-world/) — builds a trivial C project
  using `ZScript`.
- [`examples/hello-zlib/`](examples/hello-zlib/) — downloads zlib via
  `nanvix.toml` and cross-compiles a program that uses it.
- [`examples/hello-transitive/`](examples/hello-transitive/) — demonstrates
  transitive dependency resolution via the lockfile system.

## Developer Setup

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
git clone https://github.com/nanvix/zutils
cd zutils
uv sync                       # install project + dev dependencies
uv run tasks.py setup         # configure git hooks
```

### Dev Commands

| Command                       | Description                        |
|-------------------------------|------------------------------------|
| `uv run tasks.py lint`       | Check formatting (black)           |
| `uv run tasks.py format`    | Fix formatting (black)             |
| `uv run tasks.py typecheck` | Strict type checking (basedpyright)|
| `uv run tasks.py test`      | Run test suite (pytest)            |
| `uv run tasks.py clean`     | Remove caches and build artifacts  |

## License

MIT — see [LICENSE](LICENSE).
