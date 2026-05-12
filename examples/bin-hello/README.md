# bin-hello Example

Cross-compiles a binary for Nanvix that depends on `lib-hello` — demonstrates
dependency downloading with `nanvix.toml` and cross-compilation with build-time
dependencies.

## Structure

```
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
- **Native toolchain** — `i686-nanvix-gcc` (default path: `/opt/nanvix/`)
- **Docker** with `ghcr.io/nanvix/toolchain-gcc:sha-34a3641` image (pass via `./z setup --with-docker IMAGE`)

## Running

```bash
./z setup    # download Nanvix sysroot + lib-hello from GitHub releases
./z build    # cross-compile main.c → hello.elf (links against libhello.a)
./z test     # run tests (smoke, integration, functional via nanvixd.elf)
./z clean    # remove build artifacts
```

## Dependency Chain

```
bin-hello
  └── lib-hello (libhello.a + hello.h)
```

`bin-hello` declares `lib-hello` as a dependency in `nanvix.toml`.
Running `./z setup` downloads `libhello.a` and its headers into the
buildroot, then `./z build` compiles `main.c` and links against
`libhello.a`.
