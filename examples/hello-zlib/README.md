# hello-zlib — Nanvix Dependency Download Example

A minimal C program that uses [zlib](https://github.com/nanvix/zlib) to
compress and decompress a string, demonstrating how `nanvix.toml`
manages build-time dependencies.

## Structure

```
hello-zlib/
├── .nanvix/
│   ├── nanvix.toml             # Declarative dependencies
│   └── z.py                    # HelloZlib(ZScript) — lifecycle hooks
├── Makefile.nanvix             # Cross-compilation rules
├── src/
│   └── hello-zlib.c            # Compress / decompress round-trip
├── z                           # Bash bootstrap
├── z.ps1                       # PowerShell bootstrap
└── README.md
```

## Prerequisites

- **Python 3.12+**
- **Nanvix cross-compiler** — one of:
  - Native install at `/opt/nanvix/` (or `$NANVIX_TOOLCHAIN`)
  - Docker image `nanvix/toolchain:latest-minimal`
- **KVM** (`/dev/kvm`) — for functional tests only

## Usage

```bash
./z setup      # Download sysroot + zlib
./z build      # Cross-compile hello-zlib.c → hello-zlib.elf
./z test       # Run smoke, integration, and functional tests
./z clean      # Remove build artifacts
```

## How It Works

1. `./z setup` parses `.nanvix/nanvix.toml`, which declares:
   - `nanvix-version = "0.12.267"` — the Nanvix sysroot (downloaded via `Sysroot.download()`)
   - `zlib = { tag = "05d0b65-nanvix-3854291" }` — the zlib library (downloaded via `Buildroot.install_dep()`)

2. `./z build` cross-compiles `src/hello-zlib.c` with `-I .nanvix/buildroot/include`
   and links against `.nanvix/buildroot/lib/libz.a`.

3. `./z test` runs the binary on the Nanvix microkernel via `nanvixd.elf`.

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `NANVIX_MACHINE` | `microvm` | Target machine |
| `NANVIX_DEPLOYMENT_MODE` | `standalone` | Deployment mode |
| `NANVIX_MEMORY_SIZE` | `256mb` | Memory size |
| `NANVIX_TOOLCHAIN` | `/opt/nanvix` | Toolchain path |
| `GH_TOKEN` | *(none)* | GitHub token for API rate limits |
