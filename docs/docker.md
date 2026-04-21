# Docker integration

`nanvix-zutil` can transparently wrap consumer commands in `docker run`,
so a build script written against the host shell will also work inside
a pinned toolchain image without any per-call changes.

## Usage

Docker mode is opt-in per invocation, selected by one of three mutually
exclusive flags **after** the subcommand name:

```bash
nanvix-zutil build --with-docker             # consumer's default image
nanvix-zutil build --with-minimal-docker     # fixed minimal toolchain image
nanvix-zutil build --docker-image <ref>      # arbitrary image
nanvix-zutil release --with-docker           # same flags on release
```

Without one of these flags the command runs on the host. The flags only
appear on subcommands that opt in (see [Defaults](#defaults-and-why));
out of the box that means `build` and `release`.

The build script itself never references Docker — it just calls
`self.run(...)`. Whether that becomes a host process or a `docker run
... <image> <cmd>` invocation is decided by the active flag for the
current subcommand.

## Defaults and why

Two design choices are baked into the defaults:

**Docker is build-time only.** The default opt-in set is `build` and
`release`. Compilation needs a reproducible toolchain (cross compilers,
sysroot layout, libc version), and Docker is the cleanest way to pin
that across developer machines, CI runners, and downstream consumers
without forcing each contributor to install a Nanvix toolchain locally.

**`test` is intentionally excluded.** Tests run natively because:

- **KVM access.** Functional tests boot a Nanvix image under
  `nanvixd.elf`, which requires `/dev/kvm`. Passing the device into a
  container works on Linux but is a portability and permissions
  liability — group-add for `kvm`, device passthrough, and
  Linux-only semantics. Running natively keeps the host responsible
  for KVM and avoids the cross-platform footguns.
- **Test-runner overhead.** Smoke and integration phases want fast,
  unfiltered access to the build artifacts in the worktree; bouncing
  through a container per phase adds latency without buying isolation
  the build step already provided.
- **Benchmarks.** Where `benchmark` is added by a consumer, it stays
  off the docker list for the same reason — virtualised timing is
  not what you want to measure.

The third flag, `--with-minimal-docker`, exists so CI and downstream
test runners can pin the *exact* toolchain image regardless of what a
consumer's `docker_image()` override returns. Use it when you need a
known-good image; use `--with-docker` when you trust the consumer's
default; use `--docker-image <ref>` when you're debugging an image
mismatch.

## Downstream implementors

Consumer build scripts (`.nanvix/z.py`) subclass `ZScript`. Three knobs
control docker behaviour, in increasing order of intrusiveness:

### Choosing the default image

Override `docker_image()` to point `--with-docker` at a project-specific
image:

```python
class MyConsumer(ZScript):
    def docker_image(self) -> str:
        return "ghcr.io/myorg/my-toolchain:pinned"
```

`--with-minimal-docker` and `--docker-image` ignore this hook.

### Extending the docker-eligible subcommand set

Override the `DOCKER_SUBCOMMANDS` class attribute to register the docker
flags on additional subcommands:

```python
class MyConsumer(ZScript):
    # Add 'test' alongside the defaults if you really want containerised
    # tests (KVM caveats apply -- see above).
    DOCKER_SUBCOMMANDS = ("build", "release", "test")
```

Anything not in this tuple is parsed without docker flags, so passing
`--with-docker` to it will be rejected by argparse with a clear error.
Removing entries is also fine if a consumer has no use for one.

### Customising the container

Override `docker_config(image)` to add mounts, environment variables,
or other `docker run` arguments. The default config mounts the repo
root and sysroot read-only and sets a sensible workdir; subclass it
when you need extra paths visible inside the container.

For functional tests that need KVM, pass `kvm=True` to `self.run(...)`.
The framework adds `--device /dev/kvm` and the right group automatically
when docker is active, and runs natively otherwise. This is the
recommended way to write a test that should "just work" both on a
contributor's laptop and in a Docker-wrapped CI job.

### Environment variables

There is currently no environment variable that toggles docker mode or
the docker-eligible subcommand list — those are CLI flags and a class
attribute by design, so behaviour is reproducible from the command
line alone. The standard Nanvix config variables (`NANVIX_VERSION`,
`NANVIX_DEPLOYMENT_MODE`, etc.) propagate into the container the same
way they affect a host run; nothing docker-specific is required to
pass them through.

If you need to inject extra variables into the container, do it in
`docker_config()` rather than relying on inherited host env — explicit
mounts and env keep the build reproducible.
