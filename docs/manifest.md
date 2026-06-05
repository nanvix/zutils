# `nanvix.toml` manifest reference

Every Nanvix consumer repository contains a `.nanvix/nanvix.toml` file
that declares package metadata, the target Nanvix sysroot version, and
build-time / runtime dependencies.

## Minimal example

```toml
[package]
name = "hello-world"
version = "0.1.0"
nanvix-version = "0.12.257"
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

If that exact tag is not found, the resolver scans the repo for the
newest release matching `1.2.3-nanvix-*` and uses it instead (e.g.
`1.2.3-nanvix-0.12.291`).

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
nanvix-version = "0.12.257"

[dependencies]
zlib = "1.2.3"
bzip2 = "1.0.0"

[system-dependencies]
```
