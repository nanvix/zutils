# nanvix-zutil

Build orchestration utilities for the [Nanvix](https://github.com/nanvix/nanvix) ecosystem.

`nanvix_zutil` is a Python 3.12+ library that provides a unified `ZScript`
base class with lifecycle hooks, structured logging, config persistence,
GitHub release artifact downloading, lockfile-based dependency resolution, and
deterministic exit codes.

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
./z format --check   # verify formatting without writing (non-zero on diff)
./z distclean        # remove all generated artifacts
```

Or directly with `nanvix-zutil`:

```bash
nanvix-zutil lock    # resolve dependency graph → nanvix.lock
nanvix-zutil setup   # download sysroot + install deps
nanvix-zutil build   # cross-compile
nanvix-zutil test    # run tests
nanvix-zutil lint            # lint .nanvix/*.py
nanvix-zutil format          # format .nanvix/*.py
nanvix-zutil format --check  # verify formatting (non-zero on diff)
nanvix-zutil update-nanvix --check  # latest verified SDK release
nanvix-zutil update-zutils --to v0.15.0 --templates-dir /path/to/templates --dry-run
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

SDK manifests pin a verified GitHub Release contract and immutable OCI digest.
`update-nanvix` resolves the latest authoritative release by default (or an
exact tag/contract with `--to`), verifies its image, strictly resolves exact
dependency releases, and atomically writes `nanvix.toml` plus the
provenance-bearing `nanvix.lock`. Missing dependency provenance emits a blocked
JSON result and writes nothing. It updates canonical SDK revisions only;
unrelated scripts and workflows are not migration targets.

Canonical `.nanvix/nanvix.lock` files are committed. Only update transaction
state (`.nanvix-zutil-update.lock` and its journal/sidecars) is ignored.

`update-zutils` verifies the exact release template archive, or a local source
passed with `--templates-dir`, then atomically replaces `.zutils-version`, all
three bootstrap scripts, and `.nanvix/.gitignore` while preserving modes and
line endings. Both commands support `--dry-run`, `--check`,
`--output-format json`, and `--output`; neither invokes Git.

## Examples

* [`examples/lib-hello/`](examples/lib-hello/) — cross-compiles a static library (`libhello.a`).
* [`examples/bin-hello/`](examples/bin-hello/) — depends on `lib-hello` and cross-compiles a binary.

## License

MIT — see [LICENSE](LICENSE).
