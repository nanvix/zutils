#!/usr/bin/env python3
# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Bump the patch version in pyproject.toml and print the result.

Used by the release workflow to auto-increment the version on every
merge to the default branch.
"""

import subprocess
import sys
import tomllib


def main() -> int:
    result = subprocess.run(["uv", "version", "--bump", "patch"], check=False)
    if result.returncode != 0:
        print("error: failed to bump version", file=sys.stderr)
        return 1

    with open("pyproject.toml", "rb") as f:
        version = tomllib.load(f)["project"]["version"]

    print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
