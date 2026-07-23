# nanvix-zutil

Build orchestration utilities for the [Nanvix](https://github.com/nanvix/nanvix) ecosystem.

`nanvix_zutil` is a Python 3.12+ library that provides a unified `ZScript`
base class with lifecycle hooks, structured logging, config persistence,
GitHub release artifact downloading, lockfile-based dependency resolution,
and deterministic exit codes.

## Installation

Install from the [GitHub Releases](https://github.com/nanvix/zutils/releases)
page — pick the `.whl` URL for the version you need:

```bash
pip install "https://github.com/nanvix/zutils/releases/download/v<VERSION>/nanvix_zutil-<VERSION>-py3-none-any.whl"
```

Or with `uvx` for zero-install usage:

```bash
uvx nanvix-zutil build
```

Consumer repos typically don't install manually — the bootstrap wrappers
(`z`, `z.sh`, `z.ps1`) auto-create a virtualenv and install the pinned
version into `.nanvix/venv/`.

## Quick Start

Consumer repositories subclass `ZScript` in `.nanvix/z.py`:

```python
from nanvix_zutil import ZScript
from nanvix_zutil.helpers import run

class MyBuild(ZScript):
    def build(self) -> None:
        run("make", "-f", "Makefile.nanvix", "all", docker=self.docker)

    def test(self) -> None:
        run("make", "-f", "Makefile.nanvix", "test", docker=self.docker)
```

Then invoke via the bootstrap wrapper:

```bash
./z setup     # download sysroot + install dependencies
./z build     # cross-compile (Docker auto-enabled)
./z test      # run tests
```

See `nanvix-zutil --help` for the full command set.

## Developer Setup

Contributor documentation lives in [CONTRIBUTING.md](CONTRIBUTING.md).
Quick version:

```bash
git clone https://github.com/nanvix/zutils
cd zutils
uv sync
uv run tasks.py setup
```

## Documentation

| Document                                        | Description                                |
| ----------------------------------------------- | ------------------------------------------ |
| [Design Overview](docs/design.md)               | Architecture, module graph, data flow      |
| [Setup](docs/setup.md)                          | Developer environment setup                |
| [Build](docs/build.md)                          | How to build the project                   |
| [Test](docs/test.md)                            | How to run tests                           |
| [Manifest Reference](docs/manifest.md)          | `nanvix.toml` format and options           |
| [Local Development](docs/with-nanvix.md)        | Using local Nanvix builds (`--with-nanvix`)|
| [Local Deps](docs/with-deps.md)                 | One-shot local dep overrides (`--with-deps`)|
| [Troubleshooting](docs/troubleshooting.md)      | Solutions to common problems               |
| [Contributing](CONTRIBUTING.md)                 | Contribution guidelines and release process|

## Examples

- [`examples/lib-hello/`](examples/lib-hello/) — cross-compiles a static library (`libhello.a`).
- [`examples/bin-hello/`](examples/bin-hello/) — depends on `lib-hello` and cross-compiles a binary.

## License

MIT — see [LICENSE](LICENSE).
