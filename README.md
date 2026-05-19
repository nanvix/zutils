# nanvix-zutil

Build orchestration utilities for the [Nanvix](https://github.com/nanvix/nanvix) ecosystem.

`nanvix_zutil` is a Python 3.12+ library that provides a unified `ZScript`
base class with lifecycle hooks (`setup`, `distclean`, `build`, `test`, `benchmark`,
`release`, `clean`, `lock`, `lint`, `format`), structured logging, config persistence,
GitHub release artifact downloading, lockfile-based dependency resolution, and
deterministic exit codes.

## Quick Start

Consumer repositories subclass `ZScript` in `.nanvix/z.py`:

```python
from nanvix_zutil import ZScript

class MyBuild(ZScript):
    def build(self) -> None:
        self.run("make", "-f", "Makefile.nanvix", "all")

    def test(self) -> None:
        self.run("make", "-f", "Makefile.nanvix", "test")

if __name__ == "__main__":
    MyBuild.main()
```

Then invoke via the bootstrap wrapper or the `nanvix-zutil` CLI:

```bash
./z setup            # download sysroot + install dependencies
./z build            # cross-compile (Docker auto-enabled)
./z test             # run tests
./z lint             # black --check + pyright on .nanvix/*.py
./z format           # auto-format .nanvix/*.py with black
./z distclean        # remove all generated artifacts
```

Or directly with `nanvix-zutil`:

```bash
nanvix-zutil lock    # resolve dependency graph → nanvix.lock
nanvix-zutil setup   # download sysroot + install deps
nanvix-zutil build   # cross-compile
nanvix-zutil test    # run tests
nanvix-zutil lint    # lint .nanvix/*.py
nanvix-zutil format  # format .nanvix/*.py
```

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

To iterate on `nanvix-zutil` itself against a downstream consumer, point the
bootstrapper at a local source checkout (a directory containing
`pyproject.toml`) via the `--with-zutils` flag.  An editable install is
materialised in `.nanvix/venv/` and the pinned-version check is bypassed:

Linux/macOS:

```bash
./z.sh --with-zutils ~/src/zutils build
```

Windows:

```powershell
.\z.ps1 --with-zutils C:\src\zutils build
```

The override is re-applied when the recorded source path changes or when
the venv is missing, so repeated invocations stay fast.  `./z distclean`
removes the venv, so the flag must be passed again on the next bootstrap.

## Documentation

| Document | Description |
| ---------- | ------------- |
| [Design Overview](docs/design.md) | Architecture, module graph, data flow |
| [Setup](docs/setup.md) | Developer environment setup |
| [Build](docs/build.md) | How to build the project |
| [Test](docs/test.md) | How to run tests |
| [Troubleshooting](docs/troubleshooting.md) | Solutions to common problems |

Additional references:

| Document | Description |
| ---------- | ------------- |
| [Manifest Reference](docs/manifest.md) | `nanvix.toml` format and options |
| [Local Development (`--with-nanvix`)](docs/with-nanvix.md) | Using local Nanvix builds |
| [Contributing](CONTRIBUTING.md) | Contribution guidelines and release process |

## Examples

* [`examples/lib-hello/`](examples/lib-hello/) — cross-compiles a static library (`libhello.a`).
* [`examples/bin-hello/`](examples/bin-hello/) — depends on `lib-hello` and cross-compiles a binary.

## License

MIT — see [LICENSE](LICENSE).
