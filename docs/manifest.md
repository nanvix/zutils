# `nanvix.toml` manifest reference

Every Nanvix consumer repository contains a `.nanvix/nanvix.toml` file
that declares package metadata, the target Nanvix sysroot version, and
build-time / runtime dependencies.

## Minimal example

```toml
[package]
name = "hello-world"
version = "0.1.0"
nanvix-version = "0.20.0"

[toolchain]
kind = "nanvix-sdk"
provider = "c-clang"
sdk-version = "v0.20.0-sdk.2"
sdk-image = "ghcr.io/nanvix/nanvix-sdk-c-clang"
sdk-digest = "sha256:880ed7e6a20fe9bf2536b1b3ba9bdbbd067a48f043ec9131d3dd398c65f11f35"
```

## `[package]`

| Key | Required | Description |
|---|---|---|
| `name` | yes | Package name (e.g. `"hello-world"`). |
| `version` | yes | Package version string. |
| `nanvix-version` | yes | Nanvix runtime version — semver `X.Y.Z`. |

`nanvix-version` is validated at parse time. Tables, non-semver strings, and
`"latest"` are rejected because the value must match the immutable SDK runtime.

## `[toolchain]`

Every manifest must pin the SDK release version, provider, and immutable image
as one typed coordinate:

```toml
[toolchain]
kind = "nanvix-sdk"
provider = "c-clang"
sdk-version = "v0.20.0-sdk.2"
sdk-image = "ghcr.io/nanvix/nanvix-sdk-c-clang"
sdk-digest = "sha256:880ed7e6a20fe9bf2536b1b3ba9bdbbd067a48f043ec9131d3dd398c65f11f35"
```

All five fields shown above are required, and alternate spellings, nested SDK
tables, legacy kinds, embedded image digests, and omitted toolchain tables are
rejected. The SDK version's runtime must equal `package.nanvix-version`. Optional
`build-image` and `build-digest` fields select a different immutable image for
builds; otherwise the SDK image is the effective build image.

## `[dependencies]` and `[system-dependencies]`

Both sections are optional TOML tables.  Each key is the short library
name (e.g. `zlib`); the repo is inferred as `nanvix/<name>`.

### Version specifiers

| Syntax | RefKind | Suffixed? | Resolution |
|---|---|---|---|
| `dep = "1.2.3"` | `VERSION` | yes | Tag `1.2.3-nanvix-{runtime}-sdk.N` |
| `dep = { version = "1.2.3" }` | `VERSION` | yes | Tag `1.2.3-nanvix-{runtime}-sdk.N` |

These are the only accepted manifest forms. `tag`, `commitish`, `id`, and local
path dependency refs are rejected. Operational offline/local artifact overrides
remain available through the setup CLI.

### Auto-suffix

`VERSION` refs (plain string or `{ version = "..." }`) are automatically
suffixed with the exact SDK release coordinate. For example, with
`sdk-version = "v0.20.0-sdk.2"`:

```toml
zlib = "1.2.3"
# → resolves tag "1.2.3-nanvix-0.20.0-sdk.2"
```

Resolution derives the exact SDK-aware tag; it never falls
back. A missing release yields a machine-readable blocked result.

The generated `.nanvix/nanvix.lock` is canonical provenance and must be
committed. Its complete `[metadata.sdk]` table is mandatory; pre-SDK locks are
rejected. Update lock/journal files are transient and remain ignored.

## CI resolver output

`nanvix-zutil resolve` emits stable `key=value` lines from strict SDK
resolution. Alongside `nanvix_*` and `package_*`, it emits:

```text
sdk_version
sdk_provider_id
sdk_provider
sdk_image
sdk_digest
sdk_image_ref
sdk_c_abi
sdk_libc_tag
sdk_libc_commit
sdk_sysroot_sha256
```

These values come from verified lockfile provenance and are suitable for
appending directly to `$GITHUB_OUTPUT`.

Refs that already contain `-nanvix-` are rejected to prevent accidental
double-suffixing.

## Full example

The `nanvix/cpython` consumer manifest:

```toml
[package]
name = "cpython"
version = "3.12.3"
nanvix-version = "0.20.0"

[toolchain]
kind = "nanvix-sdk"
provider = "c-clang"
sdk-version = "v0.20.0-sdk.2"
sdk-image = "ghcr.io/nanvix/nanvix-sdk-c-clang"
sdk-digest = "sha256:880ed7e6a20fe9bf2536b1b3ba9bdbbd067a48f043ec9131d3dd398c65f11f35"

[dependencies]
zlib = "1.2.3"
bzip2 = "1.0.0"

[system-dependencies]
```
