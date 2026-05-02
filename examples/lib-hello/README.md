# lib-hello Example

Cross-compiles a static library (`libhello.a`) for Nanvix — demonstrates the
full `nanvix_zutil` lifecycle with sysroot download, cross-compilation, and
testing.

## Structure

```
lib-hello/
├── z                # Bash bootstrap wrapper
├── z.ps1            # PowerShell bootstrap wrapper
├── z.sh             # Bash bootstrap wrapper
├── .nanvix/
│   └── z.py         # ZScript subclass (build orchestration)
├── src/
│   ├── hello.c      # Library implementation
│   └── hello.h      # Public header
└── README.md
```

## Prerequisites

One of:
- **Native toolchain** — `i686-nanvix-gcc` (default path: `/opt/nanvix/`)
- **Docker** with `nanvix/toolchain:latest-minimal` image (auto-detected fallback)

If you built the Nanvix toolchain locally (e.g. from `nanvix/nanvix`), point
`NANVIX_TOOLCHAIN` at it:

```bash
export NANVIX_TOOLCHAIN=~/repos/nanvix/nanvix/toolchain
```

In CI, the workflow runs inside the `nanvix/toolchain:latest-minimal` Docker
container where the toolchain is pre-installed at `/opt/nanvix`.

## Running

```bash
./z setup    # download Nanvix sysroot from GitHub releases
./z build    # cross-compile hello.c → libhello.a
./z test     # run tests (smoke, integration)
./z clean    # remove build artifacts
```

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `NANVIX_MACHINE` | `microvm` | Target platform (`hyperlight`, `microvm`) |
| `NANVIX_DEPLOYMENT_MODE` | `standalone` | Deployment mode |
| `NANVIX_MEMORY_SIZE` | `256mb` | Memory configuration |
| `NANVIX_TOOLCHAIN` | `/opt/nanvix` | Path to cross-compiler toolchain |
| `GH_TOKEN` | *(none)* | GitHub token (avoids API rate limits) |

## How It Works

1. **`./z setup`** downloads the Nanvix runtime sysroot from
   `nanvix/nanvix` GitHub releases.
2. **`./z build`** cross-compiles `src/hello.c` using `i686-nanvix-gcc`
   and archives the object into `libhello.a`.
3. **`./z test`** runs smoke tests (archive exists) and integration tests
   (valid ar archive magic).
