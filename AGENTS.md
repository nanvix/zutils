# nanvix-zutil

Python 3.12+ build orchestration for the Nanvix ecosystem. Consumer repos
subclass `ZScript` in `.nanvix/z.py` and invoke it through the bootstrap
wrappers (`z`, `z.sh`, `z.ps1`) or the `nanvix-zutil` CLI.

Default branch: **`dev`**.

## Toolchain

[uv](https://docs.astral.sh/uv/), `pyright` (strict), `black`, `pytest`.

## Dev Commands

| Command                         | Description                                   |
| ------------------------------- | --------------------------------------------- |
| `uv run tasks.py lint`          | Lint (black, shfmt, shellcheck, yamllint, …)  |
| `uv run tasks.py format`        | Auto-format (black)                           |
| `uv run tasks.py typecheck`     | Strict type checking (pyright)                |
| `uv run tasks.py test`          | Run test suite (pytest)                       |
| `uv run tasks.py clean`         | Remove caches and build artifacts             |

## Commit Format

```
[scope] T: Short title
```

Scope: `zutils`, `ci`, `doc`, `git`, `tests`, `build`, `examples`.
Type: `F` (feature), `B` (bugfix), `E` (enhancement), `W` (work in progress).
Title ≤ 50 characters.

## Pointers

- [`CONTRIBUTING.md`](CONTRIBUTING.md) — code conventions, patterns, release process.
- [`docs/`](docs/) — architecture, setup, manifest reference, troubleshooting.
- [`README.md`](README.md) — end-user overview and installation.

Review `AGENTS.md`, `CONTRIBUTING.md`, `README.md`, and `docs/` alongside
code changes in every PR to keep them in sync.
