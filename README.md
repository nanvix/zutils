# nanvix-zutil

Build orchestration utilities for the [Nanvix](https://github.com/nanvix/nanvix) ecosystem.

`nanvix_zutil` is a stdlib-only Python 3.12+ library that provides a unified
`ZScript` base class with lifecycle hooks (`setup`, `build`, `test`,
`benchmark`, `release`, `clean`), structured logging, config persistence,
GitHub release artifact downloading, and deterministic exit codes.

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
./z setup
./z build
./z test
```

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
