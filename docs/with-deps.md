# Using `--with-deps` for Local Dep Overrides

`./z setup --with-deps 'name=path,name=path'` overrides one or more
dependencies with **manifest paths** to sibling consumers.  Each
entry's staged dev tree is copied into the sysroot in place of the
released archive from GitHub.

The override applies to this `setup` run only — nothing is persisted
to `.nanvix/env.json`.  Pass `--with-deps` again on the next `setup`
to reapply, and re-run it after every sibling rebuild.

## Path Semantics

Each `path` points at a sibling consumer's manifest file (typically
`.nanvix/nanvix.toml`).  Setup reads the sibling's staged dev tree:

```
<path>/../out/staging/dev/lib/*.a
<path>/../out/staging/dev/include/**/*.h
```

This is the same tree the sibling's `release` step packs into the
dev archive; contents are byte-identical.  Routing (`.a` → `lib/`,
`.h` → `include/`) and `install_libs` / `install_headers` filters
from the current manifest are applied exactly as they are during
archive extraction.

If the staging tree is missing, setup fails with a hint to run
`./z build` against that manifest first.  There is no auto-build.

## Example

Build zlib once, then point sqlite at its manifest:

```bash
(cd ~/repos/nanvix/zlib/default && ./z build)

cd ~/repos/nanvix/sqlite/default
./z setup --with-deps 'zlib=~/repos/nanvix/zlib/default/.nanvix/nanvix.toml'
./z build
```

Paths are expanded (`~`) and canonicalised at parse time.  Empty
entries (trailing or adjacent commas) are tolerated for easier shell
composition.

## Errors

- Staged dev tree not present under `<path>/../out/staging/dev/`:
  fatal, with a hint to build the sibling first.

## Public API

`ZScript.local_deps: dict[str, str]` — consumer setup hooks can
branch on `if name in self.local_deps: ...` to be override-aware.

## Relationship to `--with-nanvix`

`--with-nanvix PATH` overlays a Nanvix-native build's `bin/` and
`lib/` on top of the sysroot by direct file copy.  `--with-deps`
copies a sibling consumer's staged dev tree instead, honouring
`install_libs` / `install_headers` filtering from the manifest.
Both flags are one-shot.
