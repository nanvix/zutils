# nanvix-zutil

Build orchestration utilities for the [Nanvix](https://github.com/nanvix/nanvix) ecosystem.

`nanvix_zutil` is a Python 3.12+ library that provides a unified `ZScript`
base class with lifecycle hooks (`setup`, `build`, `test`, `benchmark`,
`release`, `clean`), structured logging, config persistence, GitHub release
artifact downloading, lockfile-based dependency resolution, and deterministic
exit codes.

## Installation

Install from the latest [GitHub Release](https://github.com/nanvix/zutils/releases):

```bash
# Requires gh CLI (https://cli.github.com)
WHEEL_URL=$(gh api repos/nanvix/zutils/releases/latest \
  --jq '.assets[] | select(.name | endswith(".whl")) | .browser_download_url')
pip install "$WHEEL_URL"
```

Or install a specific version directly:

```bash
pip install "https://github.com/nanvix/zutils/releases/download/v0.3.0/nanvix_zutil-0.3.0-py3-none-any.whl"
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

Then invoke via the `nanvix-zutil` CLI:

```bash
nanvix-zutil lock
nanvix-zutil setup
nanvix-zutil build
nanvix-zutil test
```

Or with `uvx` for zero-install usage:

```bash
uvx nanvix-zutil build
```

### Lockfile

`nanvix-zutil lock` resolves the full dependency graph (including transitive
dependencies) and writes a pinned `nanvix.lock` file to `.nanvix/`.
Use `--check` in CI to verify the lockfile is up-to-date, or `--shallow`
to resolve only direct dependencies (for publishing as a release asset).

```bash
nanvix-zutil lock              # resolve all deps and write nanvix.lock
nanvix-zutil lock --check      # verify lockfile is fresh (exit 3 if missing, 2 if stale)
nanvix-zutil lock --shallow    # resolve direct deps only (for CI release assets)
```

## Docker Integration

Consumer build scripts can run `build` and `release` commands inside a
Docker container by passing one of the three mutually exclusive Docker flags
**after the subcommand**:

| Flag | Image used |
| --- | --- |
| `--with-docker` | `docker_image()` (defaults to `nanvix/toolchain:latest-minimal`) |
| `--with-minimal-docker` | `nanvix/toolchain:latest-minimal` |
| `--docker-image <name>` | Custom image |

Docker flags are only available on `build` and `release` subcommands.
`setup()` always runs on the host, and `test` runs natively (with optional
KVM access).

```bash
nanvix-zutil setup                          # download sysroot on host
nanvix-zutil build --with-docker            # cross-compile inside Docker
nanvix-zutil test                           # run tests natively (KVM available)
nanvix-zutil clean                          # clean (host)
```


### How it works

`ZScript.run()` accepts two extra keyword arguments:

* `docker=False` — opt out of Docker for a single command (e.g. `clean`)
* `kvm=True` — add `/dev/kvm` for functional/VM tests

The default `DockerConfig` mounts:

* `repo_root` → `/mnt/workspace` (writable, default workdir)
* sysroot path → `/mnt/sysroot` (read-only; writable in KVM mode)
* `.nanvix/buildroot` → `/mnt/buildroot` (read-only; auto-added when present)

Use `self.translate_path(host_path)` to obtain the container-equivalent of any
host path when Docker is active.

### Customising Docker config

Override `docker_image()` to change the default image, or override
`docker_config(image)` to add extra mounts or environment variables:

```python
from nanvix_zutil import DockerConfig, Mount, ZScript
from pathlib import Path

class MyBuild(ZScript):
    def docker_image(self) -> str:
        return "my-registry/nanvix-toolchain:v2"

    def docker_config(self, image: str) -> DockerConfig:
        cfg = super().docker_config(image)
        cfg.extra_env["MY_VAR"] = "value"
        return cfg
```

## Examples

* [`examples/hello-world/`](examples/hello-world/) — builds a trivial C project
  using `ZScript`.
* [`examples/hello-zlib/`](examples/hello-zlib/) — downloads zlib via
  `nanvix.toml` and cross-compiles a program that uses it.
* [`examples/hello-transitive/`](examples/hello-transitive/) — demonstrates
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

| Command                      | Description                         |
| ---------------------------- | ----------------------------------- |
| `uv run tasks.py lint`       | Check formatting (black)            |
| `uv run tasks.py format`     | Fix formatting (black)              |
| `uv run tasks.py typecheck`  | Strict type checking (basedpyright) |
| `uv run tasks.py test`       | Run test suite (pytest)             |
| `uv run tasks.py clean`      | Remove caches and build artifacts   |

### Git Hooks

`uv run tasks.py setup` points Git at the `.githooks/` directory, which
contains:

| Hook         | What it does                                                    |
| ------------ | --------------------------------------------------------------- |
| `commit-msg` | Validates `[module] type: Description` format (B, E, F, or W)   |
| `pre-push`   | Runs formatting (black) and type checking (basedpyright) checks |

## License

MIT — see [LICENSE](LICENSE).
