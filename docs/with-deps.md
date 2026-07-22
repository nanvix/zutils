# Using `--with-deps` for Local Dep Overrides

`./z setup --with-deps 'name=path,name=path'` overrides one or more
dependencies with **manifest paths** to sibling consumers.  The
override applies to this `setup` run only: each entry's pre-built
dev archive is installed into the sysroot in place of the released
one from GitHub.

Pass `--with-deps` again on the next `setup` to reapply.  Every
`./z setup` is an explicit, one-shot override — nothing is persisted
to `.nanvix/env.json`.

## Path Semantics

Each `path` points at a sibling consumer's manifest file (typically
`.nanvix/nanvix.toml`).  Setup looks for the dev archive relative to
that manifest:

```
<path>/../out/dist/<name>-<host>-<target>-<machine>-<mode>-<mem>-dev.<ext>
```

Extensions are probed in the same order as the online download path:
`.tar.bz2`, then `.tar.gz`, then `.zip` (first match wins).

If the archive is missing, setup fails with a hint to run `./z build`
against that manifest first.  There is no auto-build.

## Example

Build zlib once, then point sqlite at its manifest:

```bash
(cd ~/repos/nanvix/zlib/default && ./z build)

cd ~/repos/nanvix/sqlite/default
./z setup --with-deps 'zlib=~/repos/nanvix/zlib/default/.nanvix/nanvix.toml'
./z build
```

Paths are expanded (`~`) and canonicalised at parse time.

## Errors

- Name not in the manifest's `[dependencies]`: warned and ignored.
  Without a manifest entry we cannot compute the expected archive name.
- Archive not present under `<path>/../out/dist/`: fatal, with
  a hint to build the sibling first.

## Precedence

Locally-overridden deps are authoritative.  In online mode, absence
from the SDK lock is *not* fatal for overridden names — the local
archive wins.  Transitive deps of an overridden dep are resolved from
the SDK lock when the overridden dep is present there; otherwise
transitive discovery is skipped and the user is responsible for
overriding any transitives they need with additional `--with-deps`
entries.

## Relationship to `--with-nanvix`

`--with-nanvix PATH` overlays a Nanvix-native build's `bin/` and
`lib/` directly on top of the sysroot.  `--with-deps` routes through
the same archive-extraction path used for released deps, honouring
`install_libs` / `install_headers` filtering from the manifest.
Both flags are one-shot.
