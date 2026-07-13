# bin-hello Example

Cross-compiles a binary for Nanvix that depends on `lib-hello` — demonstrates
dependency downloading with `nanvix.toml` and cross-compilation with build-time
dependencies.

## Structure

```text
bin-hello/
├── z                # Bash bootstrap wrapper
├── z.ps1            # PowerShell bootstrap wrapper
├── z.sh             # Bash bootstrap wrapper
├── .nanvix/
│   ├── nanvix.toml  # Declares lib-hello dependency
│   └── z.py         # ZScript subclass (build orchestration)
├── src/
│   └── main.c       # Main program (calls hello() from libhello.a)
└── README.md
```

## Prerequisites

One of:

- **Native SDK** — Clang targeting `i686-unknown-nanvix` (default prefix: `/opt/nanvix/`)
- **Docker** with the immutable `ghcr.io/nanvix/nanvix-sdk-c-clang@sha256:880ed7e6a20fe9bf2536b1b3ba9bdbbd067a48f043ec9131d3dd398c65f11f35` image

## Running

```bash
./z setup    # download Nanvix sysroot + lib-hello from GitHub releases
./z build    # cross-compile main.c → hello.elf (links against libhello.a)
./z test     # run tests (smoke, integration, functional via nanvixd.elf)
./z clean    # remove build artifacts
```

For standalone deployment mode (produces an initrd image with system daemons):

```bash
NANVIX_DEPLOYMENT_MODE=standalone ./z setup
NANVIX_DEPLOYMENT_MODE=standalone ./z build   # also produces hello.img
```

## Dependency Chain

```text
bin-hello
  └── lib-hello (libhello.a + hello.h)
```

`bin-hello` declares `lib-hello` as a dependency in `nanvix.toml`.
Running `./z setup` downloads `libhello.a` and its headers into the
buildroot, then `./z build` compiles `main.c` and links against
`libhello.a`.
