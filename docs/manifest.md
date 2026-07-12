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
sdk-version = "v0.20.0-sdk.1"
sdk-image = "ghcr.io/nanvix/nanvix-sdk-c-clang"
sdk-digest = "sha256:f61737cb0780e6a2058c6d0bdf8ae5562db18de437173b2bcbbe6973abd3689f"
```

## `[package]`

| Key | Required | Description |
|---|---|---|
| `name` | yes | Package name (e.g. `"hello-world"`). |
| `version` | yes | Package version string. |
| `nanvix-version` | yes | Nanvix sysroot version — semver `X.Y.Z`, or `"latest"`. |

`nanvix-version` is validated at parse time.  Tables and non-semver
strings are rejected, with the exception of the literal `"latest"`,
which resolves to the newest available sysroot release.

## `[toolchain]`

The preferred SDK mode pins the release version, provider, and immutable image
as one typed coordinate:

```toml
[toolchain]
kind = "nanvix-sdk"
provider = "c-clang"
sdk-version = "v0.20.0-sdk.1"
sdk-image = "ghcr.io/nanvix/nanvix-sdk-c-clang"
sdk-digest = "sha256:f61737cb0780e6a2058c6d0bdf8ae5562db18de437173b2bcbbe6973abd3689f"
```

The SDK version's runtime must equal `package.nanvix-version`. Optional
`build-image` and `build-digest` fields select a different immutable image for
builds; otherwise the SDK image is the effective build image. The early
`type/version/image/digest` spelling remains readable for one transition
release. Omitting `[toolchain]` selects legacy resolution.

## `[dependencies]` and `[system-dependencies]`

Both sections are optional TOML tables.  Each key is the short library
name (e.g. `zlib`); the repo is inferred as `nanvix/<name>`.

### Version specifiers

| Syntax | RefKind | Suffixed? | Resolution |
|---|---|---|---|
| `dep = "1.2.3"` | `VERSION` | yes | Tag `1.2.3-nanvix-{nv}` |
| `dep = { version = "1.2.3" }` | `VERSION` | yes | Tag `1.2.3-nanvix-{nv}` |
| `dep = { tag = "v1.0" }` | `TAG` | no | Tag `v1.0` (exact) |
| `dep = { commitish = "abc1234" }` | `COMMITISH` | no | Search `target_commitish` |
| `dep = { id = 12345678 }` | `ID` | no | `GET /releases/12345678` |
| *(env override is a path)* | `LOCAL` | no | Filesystem path — no GitHub resolution |

Only **one** specifier key is allowed per table.

### Auto-suffix

`VERSION` refs (plain string or `{ version = "..." }`) are automatically
suffixed with `-nanvix-{nanvix-version}`.  For example, with
`nanvix-version = "0.12.257"`:

```toml
zlib = "1.2.3"
# → resolves tag "1.2.3-nanvix-0.12.257"
```

Legacy mode retains its existing fallback behavior. SDK mode derives the exact
tag `1.2.3-nanvix-0.20.0-sdk.1`; it never falls back. A missing release yields
a machine-readable blocked result.

The generated `.nanvix/nanvix.lock` is canonical provenance and must be
committed. Update lock/journal files are transient and remain ignored.

## CI resolver output

`nanvix-zutil resolve` emits stable `key=value` lines. Legacy manifests retain
the existing `nanvix_*` and `package_*` output exactly. SDK locks additionally
emit:

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

`TAG`, `COMMITISH`, `ID`, and `LOCAL` refs are never suffixed — they
resolve exactly as written.

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
sdk-version = "v0.20.0-sdk.1"
sdk-image = "ghcr.io/nanvix/nanvix-sdk-c-clang"
sdk-digest = "sha256:f61737cb0780e6a2058c6d0bdf8ae5562db18de437173b2bcbbe6973abd3689f"

[dependencies]
zlib = "1.2.3"
bzip2 = "1.0.0"

[system-dependencies]
```
