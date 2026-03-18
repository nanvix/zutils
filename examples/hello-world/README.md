# Hello World Example

Cross-compiles a C program for Nanvix — demonstrates the full `nanvix_zutil`
lifecycle with sysroot download, cross-compilation, and testing.

## Structure

```
hello-world/
├── z                # Bash bootstrap wrapper
├── z.ps1            # PowerShell bootstrap wrapper
├── Makefile.nanvix  # Cross-compilation rules (i686-nanvix-gcc + Docker fallback)
├── .nanvix/
│   └── z.py         # ZScript subclass (build orchestration)
├── src/
│   └── hello.c      # Hello world C program
└── README.md
```

## Prerequisites

One of:
- **Native toolchain** at `/opt/nanvix/` (`i686-nanvix-gcc`)
- **Docker** with `nanvix/toolchain:latest-minimal` image (auto-detected fallback)

## Running

```bash
./z setup    # download Nanvix sysroot from GitHub releases
./z build    # cross-compile hello.c → hello.elf
./z test     # run tests (smoke, integration, functional via nanvixd.elf)
./z clean    # remove build artifacts
```

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `NANVIX_MACHINE` | `hyperlight` | Target platform (`hyperlight`, `microvm`) |
| `NANVIX_DEPLOYMENT_MODE` | `multi-process` | Deployment mode |
| `NANVIX_MEMORY_SIZE` | `128mb` | Memory configuration |
| `NANVIX_TOOLCHAIN` | `/opt/nanvix` | Path to cross-compiler toolchain |
| `GH_TOKEN` | *(none)* | GitHub token (avoids API rate limits) |

## How It Works

1. **`./z setup`** downloads the Nanvix runtime sysroot from
   `nanvix/nanvix` GitHub releases. The sysroot contains `libposix.a`,
   `libc.a`, `user.ld`, and the `nanvixd.elf` runtime.
2. **`./z build`** invokes `make -f Makefile.nanvix` which cross-compiles
   `src/hello.c` using `i686-nanvix-gcc` and links it statically against
   the Nanvix POSIX layer.
3. **`./z test`** runs smoke tests (binary exists), integration tests
   (valid ELF), and functional tests (executes on `nanvixd.elf`).

