# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Base class for Nanvix consumer build scripts.

Consumer repositories subclass :class:`ZScript`, implement the lifecycle
hooks they need, and call ``ZScript.main()`` as the entry point::

    from nanvix_zutil import ZScript
    from nanvix_zutil.docker import DockerConfig
    from nanvix_zutil.helpers import run

    class MyBuild(ZScript):
        def build(self, docker: DockerConfig) -> None:
            run("make", "-f", "Makefile.nanvix", "all", docker=docker)

    if __name__ == "__main__":
        MyBuild.main()

Invoke via the ``nanvix-zutil`` CLI::

    nanvix-zutil setup
    nanvix-zutil build
    nanvix-zutil test
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from nanvix_zutil import log
from nanvix_zutil import paths as _paths
from nanvix_zutil.buildroot import (
    Dependency,
    RefKind,
)
from nanvix_zutil.cli import CONFIG_FLAG_KEYS, build_parser
from nanvix_zutil.config import CFG_DOCKER_IMAGE, CFG_GH_TOKEN, CFG_SYSROOT, Config
from nanvix_zutil.docker import (
    SYSROOT_CONTAINER_PATH,
    WORKSPACE_CONTAINER_PATH,
    DockerConfig,
    Mount,
    is_windows,
    remove_build_volume,
)
from nanvix_zutil.exitcodes import EXIT_INVALID_ARGS, EXIT_MISSING_DEP
from nanvix_zutil.helpers import (
    check_docker,
    sync_configs,
)
from nanvix_zutil.lockfile import get_zutil_version, read_lockfile, write_lockfile
from nanvix_zutil.manifest import Manifest, load_manifest
from nanvix_zutil.paths import (
    manifest_path,
    nanvix_root,
    out_dir,
    repo_root,
    z_py_path,
)
from nanvix_zutil.resolver import BlockedResolution, is_stale, resolve
from nanvix_zutil.sysroot import Sysroot


def _build_docker_config(image: str, config: Config) -> DockerConfig:
    """Build the standard :class:`~nanvix_zutil.DockerConfig` for *image*.

    Mounts :func:`repo_root` at ``/mnt/workspace`` and, when configured,
    the sysroot at ``/mnt/sysroot``.

    This is a module-private function rather than a :class:`ZScript`
    method by design: only :meth:`ZScript.main` builds it, and only for
    the build step, so Docker never leaks into other hooks.  Consumers
    that need extra mounts or outputs replace fields on the ``docker``
    they receive in :meth:`ZScript.build` (e.g. via
    :func:`dataclasses.replace`).
    """
    mounts: list[Mount] = [
        Mount(
            host_path=repo_root(),
            container_path=WORKSPACE_CONTAINER_PATH,
            readonly=False,
        ),
    ]

    sysroot_str = config.get(CFG_SYSROOT)
    if sysroot_str:
        mounts.append(
            Mount(
                host_path=Path(str(sysroot_str)),
                container_path=SYSROOT_CONTAINER_PATH,
                readonly=False,
            )
        )

    return DockerConfig(
        image=image,
        mounts=mounts,
        workdir=WORKSPACE_CONTAINER_PATH,
        invalidation_inputs=[
            z_py_path(),
            manifest_path(),
            nanvix_root() / "nanvix.lock",
            nanvix_root() / "src",
            repo_root() / "Makefile.nanvix",
            nanvix_root() / "Makefile.nanvix",
        ],
    )


class ZScript:
    """Base class for consumer build scripts.

    Provides CLI dispatch, config management, subprocess execution, and
    structured logging.  Consumers subclass this and implement the lifecycle
    hooks they need.

    The :meth:`setup` and :meth:`lock` hooks are *auto-implemented* in
    the base class and are always available in the CLI.
    The remaining hooks (``build``, ``test``, ``benchmark``, ``clean``)
    only appear in the help menu when the subclass overrides them.

    Attributes:
        SYSROOT_REQUIRED_FILES: Files that must exist in the sysroot
            regardless of deployment mode.  Override in subclasses to add
            library-specific files.
        config: Persistent build configuration loaded from
            ``.nanvix/env.json`` and environment variables.
        log: The :mod:`nanvix_zutil.log` module, accessible as ``self.log``
            for structured logging in lifecycle hooks.
        targets: Arguments passed after ``--`` on the command line.
            Lifecycle hooks can use these to customise behavior
            (e.g. ``nanvix-zutil test -- smoke integration``).
        sysroot: The :class:`~nanvix_zutil.Sysroot` downloaded by
            :meth:`setup`.  Build-time dependencies are installed into it
            via :meth:`~nanvix_zutil.Sysroot.install_dep`.
    """

    SDK_RUNTIME_REQUIRED_FILES: tuple[str, ...] = (
        "bin/nanvixd.elf",
        "bin/kernel.elf",
        "bin/mkramfs.elf",
    )

    SDK_RUNTIME_REQUIRED_FILES_WINDOWS: tuple[str, ...] = (
        "bin/nanvixd.exe",
        "bin/kernel.elf",
        "bin/mkramfs.exe",
    )

    SYSROOT_STANDALONE_FILES: tuple[str, ...] = (
        "bin/mkimage.elf",
        "bin/procd.elf",
        "bin/memd.elf",
        "bin/vfsd.elf",
    )

    SYSROOT_STANDALONE_FILES_WINDOWS: tuple[str, ...] = (
        "bin/mkimage.exe",
        "bin/procd.elf",
        "bin/memd.elf",
        "bin/vfsd.elf",
    )

    #: Hooks that are auto-implemented in the base class and always
    #: available in the CLI, regardless of subclass overrides.
    AUTO_HOOKS: tuple[str, ...] = (
        "setup",
        "lock",
        "install",
        "help",
    )

    #: Consumer-defined hooks that appear in the CLI only when the
    #: subclass overrides the corresponding method.
    CONSUMER_HOOKS: tuple[str, ...] = (
        "build",
        "test",
        "benchmark",
        "clean",
    )

    def required_files_for_release(self) -> list[Path]:
        """
        Used by `package()` to verify that the release directory contains the expected files.
        Leave empty to skip release verification.
        Expects paths relative to the package root.
        """
        return []

    def sysroot_required_files(self) -> list[str]:
        """Return the sysroot files required for the current platform and mode.

        Uses Windows binary names (``nanvixd.exe``, ``mkramfs.exe``) on
        Windows; Linux names on other platforms.  Standalone mode
        additionally requires ``mkimage``, ``procd.elf``, ``memd.elf``,
        and ``vfsd.elf``.  Subclasses can extend by overriding the class
        attributes or this method.
        """
        if is_windows():
            files = list(self.SDK_RUNTIME_REQUIRED_FILES_WINDOWS)
        else:
            files = list(self.SDK_RUNTIME_REQUIRED_FILES)
        if self.config.deployment_mode == "standalone":
            if is_windows():
                files.extend(self.SYSROOT_STANDALONE_FILES_WINDOWS)
            else:
                files.extend(self.SYSROOT_STANDALONE_FILES)
        return files

    def __init__(self) -> None:
        self.config = Config()
        self.log = log
        self.targets: list[str] = []
        self.manifest: Manifest = load_manifest()
        self.sysroot: Sysroot | None = None
        self._offline: bool = False
        self._with_nanvix_path: str | None = None
        self.local_deps: dict[str, str] = {}
        """Map of dep name → sibling manifest path from ``--with-deps``.

        Populated at CLI parse time; empty when the flag was not passed.
        Read-only from consumer hooks (mutations after ``setup()`` returns
        have no effect).  Consumers that want to be override-aware can
        branch on ``if name in self.local_deps: ...``.
        """

    # ------------------------------------------------------------------
    # Hook classification helpers
    # ------------------------------------------------------------------

    def available_subcommands(self) -> tuple[str, ...]:
        """Return the subcommands to register in the CLI parser.

        :attr:`AUTO_HOOKS` are always included.  :attr:`CONSUMER_HOOKS`
        are included only when the concrete subclass overrides the
        corresponding method.

        Returns:
            Ordered tuple of subcommand names.
        """
        available: list[str] = list(self.AUTO_HOOKS)
        for name in self.CONSUMER_HOOKS:
            if getattr(type(self), name) is not getattr(ZScript, name):
                available.append(name)
        return tuple(available)

    # ------------------------------------------------------------------
    # Lifecycle hooks — auto-implemented
    # ------------------------------------------------------------------

    def setup(self) -> bool:
        """Prepare the build environment.

        The base implementation automatically downloads the Nanvix sysroot
        and all build-time dependencies declared in ``nanvix.toml``, then
        saves the resulting paths to :attr:`config`.

        Subclasses may override this to perform additional setup steps.
        Call ``super().setup()`` to retain the automatic download behaviour::

            def setup(self) -> bool:
                super().setup()
                # extra verification or configuration here
                return False

        Returns:
            ``False``. Degraded legacy setup no longer exists.
        """
        # Resolve sysroot. In offline mode, reuse whatever is already at
        # .nanvix/sysroot; --with-nanvix may then overlay artifacts.
        if self._offline:
            local_dir = _paths.sysroot()
            if local_dir.exists() and not local_dir.is_dir():
                log.fatal(
                    f"Sysroot path '{local_dir}' exists but is not a directory.",
                    code=EXIT_MISSING_DEP,
                    hint="Remove or rename this path and re-run `./z setup`.",
                )
            local_dir.mkdir(parents=True, exist_ok=True)
            cached_tag = self.config.get("sysroot_tag", "")
            self.sysroot = Sysroot(local_dir.resolve(), tag=cached_tag)
            log.info(f"Offline: using sysroot at {local_dir}")
        else:
            self.sysroot = Sysroot.download(
                machine=self.config.machine,
                deployment_mode=self.config.deployment_mode,
                memory_size=self.config.memory_size,
                tag=self.manifest.sysroot_ref.value,
                gh_token=self.config.get(CFG_GH_TOKEN),
                dest=_paths.sysroot(),
                config=self.config,
            )
        self.config.set(CFG_SYSROOT, str(self.sysroot.path))

        # On Windows, download host-native binaries (nanvixd.exe, mkramfs.exe)
        # BEFORE verifying required files — the base sysroot from
        # Sysroot.download() only has Linux .elf binaries.
        if is_windows():
            self.sysroot.download_windows_binaries(
                machine=self.config.machine,
                deployment_mode=self.config.deployment_mode,
                memory_size=self.config.memory_size,
                gh_token=self.config.get(CFG_GH_TOKEN),
                config=self.config,
            )

        # When --with-nanvix PATH is passed, overlay local build artifacts
        # (nanvixd.elf, mkramfs.elf, uservm.elf, libposix.a, etc.) on top
        # of the downloaded sysroot before verification.
        nanvix_local = self._with_nanvix_path
        if nanvix_local:
            self.sysroot.overlay_local_nanvix(Path(nanvix_local))

        # Snapshot --with-deps overrides for the install loop below.
        local_deps = dict(self.local_deps)
        if local_deps:
            log.info(f"Using {len(local_deps)} local dep override(s).")

        self.sysroot.verify(self.sysroot_required_files())

        deps: list[Dependency] = list(self.manifest.dependencies)
        # ``known`` is the set of names we understand at install time.
        # In offline mode this is just the manifest's direct deps;
        # online mode extends it with SDK-resolved transitives below.
        known: set[str] = {dep.name for dep in deps}

        sdk_releases: dict[str, dict[str, object]] = {}
        # Consumer's resolved version per package name (online only), used
        # to verify overridden siblings' build provenance below.
        consumer_versions: dict[str, str] = {}
        if not self._offline:
            # Windows resolves the digest-bound release tuple but cannot execute the
            # Linux provider image; CI verifies that image on its Linux job.
            resolution = resolve(
                self.manifest,
                gh_token=self.config.get(CFG_GH_TOKEN),
                verify_sdk_image=not is_windows(),
                local_overrides=local_deps,
            )
            if isinstance(resolution, BlockedResolution):
                log.fatal(
                    f"SDK dependency resolution blocked: {resolution.package}"
                    f" requires {resolution.requested_tag}",
                    code=EXIT_MISSING_DEP,
                    hint=resolution.reason,
                )
            packages = {pkg.name: pkg for pkg in resolution.packages}
            consumer_versions = {
                pkg.name: pkg.resolved_tag for pkg in resolution.packages
            }
            selected: list[str] = []
            pending = [dep.name for dep in deps]
            while pending:
                name = pending.pop(0)
                if name in selected:
                    continue
                package = packages.get(name)
                if package is None:
                    log.fatal(
                        f"Strict SDK lock has no package '{name}'",
                        code=EXIT_MISSING_DEP,
                    )
                selected.append(name)
                pending.extend(package.dependencies)
            known.update(selected)

            direct = {dep.name for dep in deps}
            for name in selected:
                package = packages[name]
                sdk_releases[name] = {
                    "tag_name": package.resolved_tag,
                    "target_commitish": package.resolved_commitish,
                    "id": package.release_id,
                    "assets": [
                        {
                            "id": asset.asset_id,
                            "name": asset.name,
                            "browser_download_url": asset.url,
                        }
                        for asset in package.assets
                    ],
                }
                if name not in direct:
                    deps.append(
                        Dependency(
                            name=package.name,
                            repo=package.repo,
                            ref=package.ref,
                        )
                    )

        # Warn+drop --with-deps overrides that are not part of the
        # known dep set.  Deferred until after SDK resolution so that
        # overriding a valid transitive dep (present in the SDK lock
        # but not in the manifest's direct [[dependencies]]) is not
        # spuriously rejected.
        if local_deps:
            unknown = sorted(set(local_deps) - known)
            for name in unknown:
                log.warning(
                    f"--with-deps: ignoring '{name}' — not in the resolved dep tree"
                )
                del local_deps[name]
            # Keep the public map in sync with the effective override set
            # so consumer hooks reading ``self.local_deps`` never see a
            # name that was warned-and-dropped.
            self.local_deps = dict(local_deps)

        # Transitive diamond check: each overridden sibling records the
        # versions it was built against in its own committed nanvix.lock.
        # Every dep shared with our resolution must agree, else the
        # sibling's prebuilt ``.a`` is frozen against a different version
        # of that dep than we will link.  (A sibling built with its own
        # --with-deps is not reflected in its lock; that nested case is
        # out of scope.)
        if local_deps and consumer_versions:
            mismatches: list[tuple[str, str, str, str]] = []
            for name, sibling_manifest in local_deps.items():
                sibling_lock = read_lockfile(
                    Path(sibling_manifest).parent / "nanvix.lock"
                )
                sib_versions = {
                    pkg.name: pkg.resolved_tag for pkg in sibling_lock.packages
                }
                for key in sorted(set(sib_versions) & set(consumer_versions)):
                    if sib_versions[key] != consumer_versions[key]:
                        mismatches.append(
                            (name, key, sib_versions[key], consumer_versions[key])
                        )
            if mismatches:
                lines = "\n".join(
                    f"  - via '{via}': '{key}' was built against {sib!r},"
                    f" but this build resolves it to {con!r}"
                    for via, key, sib, con in mismatches
                )
                log.fatal(
                    "Inconsistent local dep provenance:\n" + lines,
                    code=EXIT_MISSING_DEP,
                    hint=(
                        "The sibling was built against different dependency"
                        " versions than this build resolves. Rebuild the"
                        " sibling against a matching SDK, or drop the"
                        " conflicting override."
                    ),
                )

        if deps:
            sysroot = self.sysroot
            assert sysroot is not None
            for dep in deps:
                # --with-deps: copy from a sibling consumer's staged dev tree.
                if dep.name in local_deps:
                    sysroot.install_local_archive(
                        dep,
                        Path(local_deps[dep.name]),
                    )
                    continue
                # When --with-nanvix is active, try local artifacts first.
                # In offline mode, try for ALL deps (not just nanvix-owned).
                # In online mode, only try for nanvix-owned deps.
                if nanvix_local:
                    should_try_local = self._offline or dep.repo.startswith("nanvix/")
                    if should_try_local and sysroot.install_local_nanvix(
                        dep, Path(nanvix_local)
                    ):
                        continue

                # In offline mode, skip network install and warn.
                if self._offline:
                    hint = (
                        f"{nanvix_local}/deps/{dep.name}/"
                        if nanvix_local
                        else "pass --with-nanvix PATH"
                    )
                    log.warning(
                        f"Offline mode: no local artifacts found for '{dep.name}'."
                        f" Expected at: {hint}",
                    )
                    continue

                try:
                    release = sdk_releases.get(dep.name)
                    if release is None:
                        log.fatal(
                            f"Exact SDK release missing for '{dep.name}'",
                            code=EXIT_MISSING_DEP,
                        )

                    sysroot.install_dep(
                        dep=dep,
                        host=self.config.host,
                        target=self.config.target,
                        machine=self.config.machine,
                        deployment_mode=self.config.deployment_mode,
                        memory_size=self.config.memory_size,
                        gh_token=self.config.get(CFG_GH_TOKEN),
                        _release=release,
                    )
                except SystemExit:
                    log.note(
                        f"while installing dependency '{dep.name}'"
                        f" ({dep.repo}@{dep.ref.value})"
                    )
                    raise

        self.config.save()
        sync_configs()
        return False

    def install_artifacts(self, output: str) -> None:
        """Export build artifacts to a target directory.

        Copies the port's output ``.a`` libraries, headers, and binaries
        into ``<output>/{lib,include,bin}/`` from ``.nanvix/out/``.

        Note:
            This intentionally writes outside ``.nanvix/`` — it is the
            only subcommand that does so.

        Args:
            output: Absolute path to the target directory.
        """
        output_path = Path(output)
        output_path.mkdir(parents=True, exist_ok=True)

        # Source: .nanvix/out/{lib,include,bin}/
        for subdir in ("lib", "include", "bin"):
            src = out_dir() / subdir
            if src.is_dir():
                dst = output_path / subdir
                dst.mkdir(parents=True, exist_ok=True)
                for item in src.rglob("*"):
                    if item.is_file():
                        rel = item.relative_to(src)
                        dst_file = dst / rel
                        dst_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(item, dst_file)

        log.info(f"Artifacts exported to {output_path}")

    def lock(self, *, shallow: bool = False) -> None:
        """Resolve the dependency graph and write ``nanvix.lock``.

        Args:
            shallow: When ``True``, resolve only direct dependencies
                (skip transitive discovery).
        """
        lockfile = resolve(
            self.manifest,
            gh_token=self.config.get(CFG_GH_TOKEN),
            shallow=shallow,
        )
        if isinstance(lockfile, BlockedResolution):
            log.fatal(
                f"SDK dependency resolution blocked: {lockfile.package}"
                f" requires exact tag {lockfile.requested_tag}",
                code=EXIT_MISSING_DEP,
                hint=lockfile.reason,
            )
        lock_path = nanvix_root() / "nanvix.lock"
        write_lockfile(lockfile, lock_path)
        log.success(f"Wrote {lock_path}")

    def lock_check(self) -> None:
        """Verify that ``nanvix.lock`` is up-to-date.

        Exits with ``EXIT_MISSING_DEP`` if the lockfile does not exist, or
        ``EXIT_INVALID_ARGS`` if it is stale relative to ``nanvix.toml``.
        """
        lock_path = nanvix_root() / "nanvix.lock"
        lockfile = read_lockfile(lock_path)

        if is_stale(lockfile):
            log.fatal(
                "Lockfile is stale — nanvix.toml has changed since it was generated.",
                code=EXIT_INVALID_ARGS,
                hint="Run `nanvix-zutil lock` to regenerate the lockfile.",
            )

    # ------------------------------------------------------------------
    # Lifecycle hooks — override in subclass
    # ------------------------------------------------------------------

    def build(self, docker: DockerConfig) -> None:
        """Build the project.

        Override to invoke the project's build system.  The *docker*
        argument is the only handle to Docker in the lifecycle: pass it
        to :func:`~nanvix_zutil.helpers.run` to wrap commands in the
        toolchain container.
        """

    def test(self) -> None:
        """Run the project's test suite.

        Override to invoke the project's tests.
        """

    def benchmark(self) -> None:
        """Run the project's benchmarks.

        Override to invoke the project's benchmarks.
        """

    def clean(self) -> None:
        """Remove build artifacts.

        On Windows, common build artifacts are removed directly without
        invoking the build system (which would require Docker).  Override
        to customise the files cleaned.
        """
        # Drop the persistent build volume, if one is configured.  Docker is
        # scoped to ``build``, so reconstruct the standard config to recover
        # the deterministic volume name.
        docker = _build_docker_config(
            str(self.config.get(CFG_DOCKER_IMAGE) or ""), self.config
        )
        try:
            volume = docker.volume_name()
        except ValueError as exc:
            log.warning(f"Skipping build-volume removal: {exc}")
        else:
            if volume is not None:
                remove_build_volume(volume)
                log.info(f"Requested removal of build volume {volume}")
        if is_windows():
            # Common artifacts that consumers may produce.
            # Subclasses can override to add project-specific files.
            for name in (".nanvix-configured",):
                p = repo_root() / name
                if p.is_file():
                    p.unlink()
                    log.info(f"Removed {name}")

    # ------------------------------------------------------------------
    # CLI entry point
    # ------------------------------------------------------------------

    @classmethod
    def main(cls) -> None:
        """Parse command-line arguments and dispatch to the appropriate
        lifecycle hook."""
        argv = sys.argv[1:]

        # Split sys.argv on '--' to separate framework args from targets.
        if "--" in argv:
            sep = argv.index("--")
            framework_argv = argv[:sep]
            targets = argv[sep + 1 :]
        else:
            framework_argv = argv
            targets = []

        # Pre-parse --version before creating the instance so it can exit
        # cleanly without requiring a valid manifest.
        pre_parser = argparse.ArgumentParser(add_help=False)
        pre_parser.add_argument(
            "--version",
            action="version",
            version=f"%(prog)s (nanvix-zutil {get_zutil_version()})",
        )
        pre_parser.parse_known_args(framework_argv)

        # Detect --help/-h and the 'help' subcommand (or no subcommand at
        # all) BEFORE loading the manifest.  A missing nanvix.toml must not
        # prevent the user from reading help text.
        positional_args = [a for a in framework_argv if not a.startswith("-")]
        first_positional = positional_args[0] if positional_args else None
        help_requested = (
            "-h" in framework_argv
            or "--help" in framework_argv
            or first_positional in (None, "help")
        )
        if help_requested:
            # Build a full static parser (all subcommands) for help display.
            build_parser().parse_args(framework_argv)  # --help/-h → sys.exit(0)
            build_parser().print_help()  # 'help' subcommand or no args
            return

        instance = cls()
        instance.targets = targets

        # Build the parser dynamically: auto hooks are always registered;
        # consumer hooks only appear when the subclass overrides them.
        parser = build_parser(available=instance.available_subcommands())
        args = parser.parse_args(framework_argv)

        # ------------------------------------------------------------------
        # Apply NANVIX_* config flags (e.g. --machine, --mode) to the config.
        # These replace the legacy environment variables; GH_TOKEN stays an
        # environment variable.
        # ------------------------------------------------------------------
        for key in CONFIG_FLAG_KEYS:
            val = getattr(args, key, None)
            if val is not None:
                instance.config.set(key, val)

        # ------------------------------------------------------------------
        # Handle --offline, --with-nanvix from CLI.
        # ------------------------------------------------------------------
        if getattr(args, "offline", False):
            instance._offline = True
        if getattr(args, "with_nanvix", None):
            instance._with_nanvix_path = args.with_nanvix

        with_deps: dict[str, str] | None = getattr(args, "with_deps", None)
        if with_deps:
            instance.local_deps = with_deps

        # ------------------------------------------------------------------
        # Docker image handling for ``setup``: resolve the image, persist it,
        # and pre-pull it. ``build`` constructs its config later, where it is
        # dispatched; no other step touches Docker.
        #
        # On Windows the image is still persisted, but Docker is never
        # required to be installed (host-native binaries are used instead),
        # so ``check_docker`` is skipped.
        # ------------------------------------------------------------------

        if args.subcommand == "setup":
            requested_image: str | None = getattr(args, "with_docker", None)
            manifest_image = instance.manifest.toolchain.effective_build_ref
            allow_override = bool(getattr(args, "allow_local_docker_override", False))
            if (
                requested_image is not None
                and requested_image != manifest_image
                and not allow_override
            ):
                log.fatal(
                    f"--with-docker {requested_image!r} conflicts with the"
                    f" manifest build image {manifest_image!r}",
                    code=EXIT_INVALID_ARGS,
                    hint="Omit --with-docker, or use"
                    " --allow-local-docker-override for an intentional"
                    " local-development override.",
                )
            image = requested_image or manifest_image
            instance.config.set(CFG_DOCKER_IMAGE, image)
            instance.config.save()
            if not is_windows():
                check_docker(image)

        # ------------------------------------------------------------------
        # Dispatch to lifecycle hook
        # ------------------------------------------------------------------
        subcommand: str | None = args.subcommand

        # Fail fast on env.json / nanvix.toml sysroot version drift so
        # users don't build against a stale sysroot.
        # See https://github.com/nanvix/zutils/issues/263.
        if subcommand is not None and subcommand != "setup":
            pinned = instance.manifest.sysroot_ref
            if pinned.kind == RefKind.TAG and isinstance(pinned.value, str):
                cached = instance.config.get("sysroot_tag")
                if isinstance(cached, str) and cached:
                    expected = pinned.value
                    if cached.removeprefix("v") != expected.removeprefix("v"):
                        log.fatal(
                            f"Sysroot is stale: env.json={cached!r}, expected={expected!r}."
                            f" (nanvix.toml={pinned.value!r})."
                            " Run `./z setup` to refresh.",
                            code=EXIT_MISSING_DEP,
                        )

        # Special handling for lock subcommand (--check, --shallow flags).
        if subcommand == "lock":
            if args.check:
                instance.lock_check()
                log.success("Lockfile is up-to-date")
            else:
                instance.lock(shallow=args.shallow)
            return

        # Special handling for install subcommand (--output flag).
        if subcommand == "install":
            instance.install_artifacts(output=args.output)
            log.success("Install complete")
            return

        dispatch: dict[str, object] = {
            "setup": instance.setup,
            "test": instance.test,
            "benchmark": instance.benchmark,
            "clean": instance.clean,
        }

        if subcommand == "build":
            # Build is the sole Docker-aware hook: resolve the persisted
            # image, ensure it is available, and construct the config here,
            # where build is dispatched. On Windows host-native binaries are
            # used, so Docker is never required.
            image = instance.config.get(CFG_DOCKER_IMAGE)
            if image is None:
                log.fatal(
                    "No Docker image configured. Run setup first.",
                    code=EXIT_INVALID_ARGS,
                )
            if not is_windows():
                check_docker(image)
            instance.build(_build_docker_config(image, instance.config))
            log.success("Build complete")
            return

        handler = dispatch.get(subcommand) if subcommand is not None else None
        if callable(handler) and subcommand is not None:
            handler()
            log.success(f"{subcommand.capitalize()} complete")
        else:
            log.fatal(f"Unknown subcommand: {subcommand}", code=EXIT_INVALID_ARGS)
