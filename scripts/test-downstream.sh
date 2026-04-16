#!/usr/bin/env bash
# test-downstream.sh — Validate nanvix-zutil against downstream consumers.
#
# Builds a wheel from the current branch, resolves consumer repos using
# configurable checkout strategies (bare/clone/shallow), installs the wheel
# into a fresh venv per consumer, and runs setup / build / test.
#
# Configuration is read from downstream.json (auto-generated on first run).
#
# Supports two platforms:
#   linux   — runs natively by default; pass --with-docker to use Docker
#   windows — always uses --with-docker (launches pwsh.exe from WSL)
#
# Usage:
#   bash test-downstream.sh [options] [consumer...]
#
# Options:
#   --platform linux|windows|both   Platform to test (default: both)
#   --with-docker                   Run build/test inside Docker (Linux only;
#                                   always used on Windows)
#   --config FILE                   Path to downstream.json (default: scripts/downstream.json)
#   --force-fallback                Force dependency version fallback (exit 7)
#   --setup-only                    Only run setup
#   --skip-build                    Skip wheel build (reuse existing)
#   -h, --help                      Show this help
#
# Examples:
#   bash test-downstream.sh                            # Both platforms, all consumers (native)
#   bash test-downstream.sh --with-docker              # Build/test inside Docker
#   bash test-downstream.sh --setup-only sqlite        # Setup only, sqlite
#   bash test-downstream.sh --force-fallback sqlite    # Test fallback reporting
#   bash test-downstream.sh --platform both            # Run on both platforms
#   bash test-downstream.sh --platform windows cpython # Windows only, cpython
#
# Requirements:
#   - Python 3.12+, git, gh (GitHub CLI), GH_TOKEN env var
#   - Docker (only when --with-docker is passed, or on Windows)
#   - Windows mode: WSL with pwsh.exe accessible, Docker Desktop on host

set -euo pipefail

# --- Configuration -----------------------------------------------------------

CONSUMERS_URL="https://raw.githubusercontent.com/nanvix/workflows/refs/heads/main/consumer-repos.json"
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
CONSUMERS_CACHE="$SCRIPT_DIR/consumer-repos.json"

ZUTILS_ROOT="$(pwd)"
CONFIG_FILE=""

PLATFORM=""
SETUP_ONLY=false
SKIP_BUILD=false
FORCE_FALLBACK=false
WITH_DOCKER=false
CONSUMERS=()
USER_CONSUMERS=false

# --- Helpers ------------------------------------------------------------------

log() { printf '\033[1;34m>>>\033[0m %s\n' "$*"; }
ok() { printf '\033[1;32m OK\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31mFAIL\033[0m %s\n' "$*"; }

TOTAL_FAILED=0

# --- Config generation --------------------------------------------------------

# ensure_config <config_file>
#   Generate downstream.json from consumer-repos.json on first run.
ensure_config() {
    local config_file="$1"
    if [[ -f "$config_file" ]]; then
        return 0
    fi

    log "No downstream.json found — generating from consumer-repos.json..."

    # Try remote first.
    local json=""
    if json=$(curl -fsSL "$CONSUMERS_URL" 2>/dev/null); then
        echo "$json" > "$CONSUMERS_CACHE"
        log "  Fetched consumer list from remote"
    elif [[ -f "$CONSUMERS_CACHE" ]]; then
        json=$(cat "$CONSUMERS_CACHE")
        log "  Using cached consumer-repos.json"
    else
        fail "Cannot fetch consumer list and no cache at $CONSUMERS_CACHE"
        return 1
    fi

    # Transform string array into downstream.json structure.
    echo "$json" | jq '{
        "$schema": "./downstream.schema.json",
        "defaults": {
            "checkout_strategy": "shallow",
            "repos_root": "~/repos",
            "win_repos_root": null,
            "branch_pattern": "nanvix/v*"
        },
        "consumers": [.[] | {"repo": .}]
    }' > "$config_file"

    log "Generated $config_file — customize as needed."
}

# --- Parse args ---------------------------------------------------------------

while [[ $# -gt 0 ]]; do
    case "$1" in
    --platform)
        PLATFORM="$2"
        shift 2
        ;;
    --config)
        CONFIG_FILE="$2"
        shift 2
        ;;
    --setup-only)
        SETUP_ONLY=true
        shift
        ;;
    --with-docker)
        WITH_DOCKER=true
        shift
        ;;
    --skip-build)
        SKIP_BUILD=true
        shift
        ;;
    --force-fallback)
        FORCE_FALLBACK=true
        SETUP_ONLY=true
        shift
        ;;
    --help | -h)
        head -38 "$0" | grep '^#' | sed 's/^# \?//'
        exit 0
        ;;
    *)
        CONSUMERS+=("$1")
        USER_CONSUMERS=true
        shift
        ;;
    esac
done

# Default config path.
CONFIG_FILE="${CONFIG_FILE:-$SCRIPT_DIR/downstream.json}"

# Ensure config exists (auto-generate on first run).
if ! ensure_config "$CONFIG_FILE"; then
    fail "Could not generate or find config at $CONFIG_FILE"
    exit 1
fi

# Read config.
REPOS_ROOT=$(jq -r '.defaults.repos_root // "~/repos"' "$CONFIG_FILE")
REPOS_ROOT="${REPOS_ROOT/#\~/$HOME}"
WIN_REPOS_ROOT=$(jq -r '.defaults.win_repos_root // empty' "$CONFIG_FILE" 2>/dev/null || true)
DEFAULT_STRATEGY=$(jq -r '.defaults.checkout_strategy // "shallow"' "$CONFIG_FILE")
BRANCH_PATTERN=$(jq -r '.defaults.branch_pattern // "nanvix/v*"' "$CONFIG_FILE")

# Auto-detect WIN_REPOS_ROOT if not set in config.
if [[ -z "$WIN_REPOS_ROOT" ]]; then
    _win_userprofile="$(cmd.exe /C 'echo %USERPROFILE%' 2>/dev/null | tr -d '\r' || true)"
    _win_home=""
    if [[ -n "$_win_userprofile" ]]; then
        _win_home="$(wslpath -u "$_win_userprofile" 2>/dev/null || true)"
    fi
    WIN_REPOS_ROOT="${_win_home:-/mnt/c/Users/$USER}/repos"
    unset _win_userprofile _win_home
fi

if [[ ${#CONSUMERS[@]} -eq 0 ]]; then
    mapfile -t CONSUMERS < <(jq -r '.consumers[].repo' "$CONFIG_FILE")
fi

# Auto-detect platform if not specified
if [[ -z "$PLATFORM" ]]; then
    PLATFORM="both"
fi

# --- Wheel build (shared) ----------------------------------------------------

build_wheel() {
    local work_dir="$1"
    local wheel_dir="$work_dir/wheel"

    if [[ "$SKIP_BUILD" == true ]]; then
        WHEEL_FILE="$(find "$wheel_dir" -name '*.whl' -print -quit 2>/dev/null || true)"
        if [[ -z "$WHEEL_FILE" ]]; then
            fail "No wheel found in $wheel_dir. Run without --skip-build first."
            exit 1
        fi
        ok "Reusing: $(basename "$WHEEL_FILE")"
        return
    fi

    log "Building nanvix-zutil wheel from $ZUTILS_ROOT"
    mkdir -p "$wheel_dir"
    rm -f "$wheel_dir"/*.whl

    if command -v pip &>/dev/null; then
        pip wheel --no-deps --wheel-dir "$wheel_dir" "$ZUTILS_ROOT" 2>&1 | tail -3
    elif command -v uv &>/dev/null; then
        uv pip wheel --no-deps --wheel-dir "$wheel_dir" "$ZUTILS_ROOT" 2>&1 | tail -3
    else
        python3 -m pip wheel --no-deps --wheel-dir "$wheel_dir" "$ZUTILS_ROOT" 2>&1 | tail -3
    fi

    WHEEL_FILE="$(find "$wheel_dir" -name '*.whl' -print -quit)"
    if [[ -z "$WHEEL_FILE" ]]; then
        fail "Wheel build produced no .whl file"
        exit 1
    fi
    ok "Built: $(basename "$WHEEL_FILE")"
}

# --- Resolve local repos (multi-strategy) ------------------------------------

# detect_checkout_strategy <repo_path>
#   Auto-detect the checkout strategy for an existing repo path.
#   Returns: bare, clone, or shallow (default for new/unknown).
detect_checkout_strategy() {
    local repo_path="$1"
    if [[ ! -e "$repo_path" ]]; then
        echo "shallow"
        return
    fi
    if [[ -f "$repo_path/HEAD" && ! -d "$repo_path/.git" ]]; then
        echo "bare"
        return
    fi
    if [[ -f "$repo_path/.git" ]]; then
        # .git is a file → gitdir pointer → worktree of a bare repo
        echo "bare"
        return
    fi
    if [[ -d "$repo_path/.git" ]]; then
        echo "clone"
        return
    fi
    echo "shallow"
}

# resolve_branch <consumer> <repo_path> <strategy> <branch_pattern>
#   Resolve the target branch for a consumer repo.
#   For bare: check local refs. For clone/shallow: use git ls-remote.
#   Prints the resolved branch name.
resolve_branch() {
    local consumer="$1"
    local repo_path="$2"
    local strategy="$3"
    local branch_pattern="${4:-nanvix/v*}"

    local target_ref=""

    if [[ "$strategy" == "bare" && -d "$repo_path" ]]; then
        # Check local refs in bare repo.
        target_ref=$(git -C "$repo_path" for-each-ref --sort=version:refname \
            --format='%(refname:short)' "refs/heads/$branch_pattern" "refs/remotes/origin/$branch_pattern" \
            2>/dev/null | tail -1)
        target_ref="${target_ref#origin/}"

        # Fallback: bare repo HEAD.
        if [[ -z "$target_ref" ]]; then
            local symref
            symref=$(git -C "$repo_path" symbolic-ref HEAD 2>/dev/null || true)
            target_ref="${symref#refs/heads/}"
        fi
    else
        # Use git ls-remote for clone/shallow or bare repos that don't exist yet.
        target_ref=$(git ls-remote --heads "https://github.com/$consumer.git" "$branch_pattern" 2>/dev/null \
            | awk '{print $2}' | sed 's|refs/heads/||' | sort -V | tail -1)

        # Fallback: default branch via ls-remote HEAD.
        if [[ -z "$target_ref" ]]; then
            target_ref=$(git ls-remote --symref "https://github.com/$consumer.git" HEAD 2>/dev/null \
                | awk '/^ref:/{print $2}' | sed 's|refs/heads/||')
        fi
    fi

    echo "$target_ref"
}

# resolve_repo_bare <consumer> <repo_path> <branch>
#   Bare-repo + worktree strategy (original behavior).
#   Prints the worktree directory path.
resolve_repo_bare() {
    local consumer="$1"
    local repo_path="$2"
    local branch="$3"

    # Clone if missing.
    if [[ ! -d "$repo_path" ]]; then
        log "  $consumer: cloning bare repo to $repo_path" >&2
        mkdir -p "$(dirname "$repo_path")"
        if ! git clone --bare "https://github.com/$consumer.git" "$repo_path" >&2; then
            fail "  $consumer: git clone --bare failed" >&2
            return 1
        fi
        git -C "$repo_path" config remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'
        ok "  $consumer: cloned" >&2
    fi

    # Ensure fetch refspec is correct.
    local cur_fetch
    cur_fetch=$(git -C "$repo_path" config --get remote.origin.fetch 2>/dev/null || true)
    if [[ "$cur_fetch" != '+refs/heads/*:refs/remotes/origin/*' ]]; then
        git -C "$repo_path" config remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'
    fi

    # Fetch latest.
    log "  $consumer: fetching latest" >&2
    git -C "$repo_path" fetch origin --prune >&2 || true

    # Look for existing nanvix/v* worktree directories.
    local candidates
    candidates=("$repo_path"/nanvix/v*)
    if [[ -d "${candidates[0]}" ]]; then
        local wt_dir="${candidates[-1]}"
        local cur_branch
        cur_branch=$(git -C "$wt_dir" rev-parse --abbrev-ref HEAD 2>/dev/null || true)
        log "  $consumer: updating worktree at $wt_dir" >&2
        git -C "$wt_dir" fetch origin >&2 || true
        if [[ -n "$cur_branch" ]]; then
            git -C "$wt_dir" reset --hard "origin/$cur_branch" >&2 || true
        fi
        echo "$wt_dir"
        return 0
    fi

    local wt_dir="$repo_path/$branch"

    # Update existing worktree.
    if [[ -d "$wt_dir" ]]; then
        local cur_branch
        cur_branch=$(git -C "$wt_dir" rev-parse --abbrev-ref HEAD 2>/dev/null || true)
        log "  $consumer: updating worktree at $wt_dir" >&2
        git -C "$wt_dir" fetch origin >&2 || true
        if [[ -n "$cur_branch" ]]; then
            git -C "$wt_dir" reset --hard "origin/$cur_branch" >&2 || true
        fi
        echo "$wt_dir"
        return 0
    fi

    log "  $consumer: creating worktree for $branch" >&2
    git -C "$repo_path" worktree add "$wt_dir" "$branch" >&2
    echo "$wt_dir"
}

# resolve_repo_clone <consumer> <repo_path> <branch>
#   Standard clone strategy.
#   Prints the repo directory path.
resolve_repo_clone() {
    local consumer="$1"
    local repo_path="$2"
    local branch="$3"

    if [[ ! -d "$repo_path" ]]; then
        log "  $consumer: cloning to $repo_path" >&2
        mkdir -p "$(dirname "$repo_path")"
        if ! git clone "https://github.com/$consumer.git" "$repo_path" >&2; then
            fail "  $consumer: git clone failed" >&2
            return 1
        fi
        ok "  $consumer: cloned" >&2
    fi

    log "  $consumer: fetching and checking out $branch" >&2
    git -C "$repo_path" fetch origin >&2 || true
    git -C "$repo_path" checkout "$branch" >&2 2>/dev/null || git -C "$repo_path" checkout -b "$branch" "origin/$branch" >&2
    git -C "$repo_path" reset --hard "origin/$branch" >&2 || true
    echo "$repo_path"
}

# resolve_repo_shallow <consumer> <repo_path> <branch>
#   Shallow clone strategy (default for new repos — fastest).
#   Prints the repo directory path.
resolve_repo_shallow() {
    local consumer="$1"
    local repo_path="$2"
    local branch="$3"

    if [[ ! -d "$repo_path" ]]; then
        log "  $consumer: shallow clone ($branch) to $repo_path" >&2
        mkdir -p "$(dirname "$repo_path")"
        if ! git clone --depth 1 -b "$branch" "https://github.com/$consumer.git" "$repo_path" >&2; then
            fail "  $consumer: shallow clone failed" >&2
            return 1
        fi
        ok "  $consumer: cloned (shallow)" >&2
        echo "$repo_path"
        return 0
    fi

    log "  $consumer: updating shallow clone" >&2
    git -C "$repo_path" fetch --depth 1 origin "$branch" >&2 || true
    git -C "$repo_path" reset --hard "origin/$branch" >&2 || true
    echo "$repo_path"
}

# resolve_repo_dir <consumer> <repos_root> [strategy] [branch] [branch_pattern]
#   Resolve a consumer repo to a working directory path.
#   Auto-detects strategy and branch if not provided.
resolve_repo_dir() {
    local consumer="$1"
    local repos_root="${2:-$REPOS_ROOT}"
    local strategy="${3:-}"
    local branch="${4:-}"
    local branch_pattern="${5:-nanvix/v*}"
    local repo_path="$repos_root/$consumer"

    # Auto-detect strategy if not specified.
    if [[ -z "$strategy" ]]; then
        strategy=$(detect_checkout_strategy "$repo_path")
        log "  $consumer: auto-detected strategy: $strategy" >&2
    fi

    # Resolve branch if not specified.
    if [[ -z "$branch" ]]; then
        branch=$(resolve_branch "$consumer" "$repo_path" "$strategy" "$branch_pattern")
    fi

    if [[ -z "$branch" ]]; then
        fail "  $consumer: cannot determine target branch" >&2
        return 1
    fi

    log "  $consumer: strategy=$strategy branch=$branch" >&2

    case "$strategy" in
    bare)
        resolve_repo_bare "$consumer" "$repo_path" "$branch"
        ;;
    clone)
        resolve_repo_clone "$consumer" "$repo_path" "$branch"
        ;;
    shallow)
        resolve_repo_shallow "$consumer" "$repo_path" "$branch"
        ;;
    *)
        fail "  $consumer: unknown checkout strategy: $strategy" >&2
        return 1
        ;;
    esac
}

# export_fallback_env <repo_dir>
#   Reads [dependencies] from nanvix.toml and exports
#   NANVIX_VERSION_<NAME>=<version>-nanvix-99.99.99 for each dep.
#   This forces resolve_release_with_fallback() to miss the exact tag
#   and fall back to the best available release.
export_fallback_env() {
    local repo_dir="$1"
    local manifest="$repo_dir/.nanvix/nanvix.toml"

    if [[ ! -f "$manifest" ]]; then
        fail "  No nanvix.toml at $manifest"
        return 1
    fi

    # Parse lines like: zlib = "1.3.1"
    # between [dependencies] and the next section header.
    local in_deps=false
    while IFS= read -r line; do
        if [[ "$line" =~ ^\[dependencies\] ]]; then
            in_deps=true
            continue
        fi
        if [[ "$line" =~ ^\[ ]]; then
            in_deps=false
            continue
        fi
        if $in_deps && [[ "$line" =~ ^([a-zA-Z0-9_-]+)[[:space:]]*=[[:space:]]*\"([^\"]+)\" ]]; then
            local name="${BASH_REMATCH[1]}"
            local version="${BASH_REMATCH[2]}"
            local env_key="NANVIX_VERSION_${name^^//-/_}"
            local env_val="${version}-nanvix-99.99.99"
            export "$env_key=$env_val"
            log "  $env_key=$env_val"
        fi
    done <"$manifest"
}

# =============================================================================
# Linux runner
# =============================================================================

run_linux() {
    local work_dir="${DOWNSTREAM_WORK_DIR:-/tmp/nanvix-downstream-test}"
    local failed=0
    local results=()

    log "=== Linux Downstream Test ==="
    build_wheel "$work_dir"

    echo ""
    log "Wheel: $WHEEL_FILE"
    log "Repos root: $REPOS_ROOT"
    log "Consumers: ${CONSUMERS[*]}"
    log "Setup only: $SETUP_ONLY"
    echo ""

    for consumer in "${CONSUMERS[@]}"; do
        log "--- Testing $consumer ---"

        # Read per-consumer overrides from config.
        local consumer_strategy
        consumer_strategy=$(jq -r --arg c "$consumer" \
            '.consumers[] | select(.repo == $c) | .strategy // empty' "$CONFIG_FILE" 2>/dev/null || true)
        local consumer_branch
        consumer_branch=$(jq -r --arg c "$consumer" \
            '.consumers[] | select(.repo == $c) | .branch // empty' "$CONFIG_FILE" 2>/dev/null || true)
        local consumer_path
        consumer_path=$(jq -r --arg c "$consumer" \
            '.consumers[] | select(.repo == $c) | .path // empty' "$CONFIG_FILE" 2>/dev/null || true)

        local repo_dir
        if [[ -n "$consumer_path" ]]; then
            repo_dir="$consumer_path"
        elif ! repo_dir=$(resolve_repo_dir "$consumer" "$REPOS_ROOT" \
                "${consumer_strategy:-$DEFAULT_STRATEGY}" "$consumer_branch" "$BRANCH_PATTERN"); then
            results+=("$consumer: FAIL (not found)")
            failed=$((failed + 1))
            continue
        fi
        log "  Using: $repo_dir"

        # Clean and recreate venv
        local venv_dir="$repo_dir/.nanvix/venv"
        [[ -d "$venv_dir" ]] && rm -rf "$venv_dir"

        log "  Creating venv and installing local wheel..."
        if ! python3 -m venv "$venv_dir" 2>&1; then
            fail "  $consumer: venv creation failed"
            results+=("$consumer: FAIL (venv)")
            failed=$((failed + 1))
            continue
        fi

        local venv_python="$venv_dir/bin/python"
        [[ ! -x "$venv_python" ]] && venv_python="$venv_dir/Scripts/python"

        if ! "$venv_python" -m pip install --quiet "$WHEEL_FILE" 2>&1; then
            "$venv_python" -m ensurepip --default-pip 2>/dev/null || true
            if ! "$venv_python" -m pip install --quiet "$WHEEL_FILE" 2>&1; then
                fail "  $consumer: wheel install failed"
                results+=("$consumer: FAIL (pip install)")
                failed=$((failed + 1))
                continue
            fi
        fi

        local ver
        ver=$("$venv_python" -c "import nanvix_zutil; print('OK')" 2>&1)
        log "  nanvix_zutil import: $ver"

        # Phase 1: setup
        local phases=""
        local setup_env=()
        if [[ "$FORCE_FALLBACK" == true ]]; then
            log "  Forcing dependency fallback..."
            # Clean cached artifacts so downloads are forced.
            rm -rf "$repo_dir/.nanvix/buildroot" "$repo_dir/.nanvix/cache"
            log "  Cleaned buildroot and cache"
            export_fallback_env "$repo_dir"
        fi
        log "  Running: nanvix-zutil setup"
        local setup_rc=0
        local setup_output
        setup_output=$(cd "$repo_dir" && "$venv_python" -m nanvix_zutil setup 2>&1) || setup_rc=$?
        echo "$setup_output"

        if [[ "$FORCE_FALLBACK" == true ]]; then
            # Detect fallback via exit code (base-class setup returns 7)
            # OR via log output (consumer overrides that do their own
            # fallback resolution log "fallback for" messages).
            local fallback_detected=false
            if [[ $setup_rc -eq 7 ]]; then
                fallback_detected=true
            elif echo "$setup_output" | grep -qi "fallback for"; then
                fallback_detected=true
            fi

            if [[ "$fallback_detected" == true ]]; then
                ok "  $consumer setup: fallback detected (exit $setup_rc)"
                results+=("$consumer: OK (fallback verified)")
            elif [[ $setup_rc -eq 0 ]]; then
                fail "  $consumer setup: no fallback detected (exit 0, no fallback log)"
                results+=("$consumer: FAIL (fallback not triggered)")
                failed=$((failed + 1))
            else
                fail "  $consumer setup: unexpected exit $setup_rc (no fallback log)"
                results+=("$consumer: FAIL (setup exit $setup_rc)")
                failed=$((failed + 1))
            fi
            continue
        fi

        if [[ $setup_rc -ne 0 ]]; then
            fail "  $consumer setup failed"
            results+=("$consumer: FAIL (setup)")
            failed=$((failed + 1))
            continue
        fi
        ok "  $consumer setup: OK"
        phases="setup"

        if [[ "$SETUP_ONLY" == true ]]; then
            log "  (skipping build/test — setup-only mode)"
            results+=("$consumer: OK ($phases)")
            continue
        fi

        # Build docker flag for subcommands.
        local docker_flag=""
        if [[ "$WITH_DOCKER" == true ]]; then
            if ! command -v docker &>/dev/null; then
                log "  --with-docker requested but Docker not available — skipping build/test"
                results+=("$consumer: OK ($phases, no docker)")
                continue
            fi
            docker_flag="--with-docker"
        fi

        # Phase 2: build
        log "  Running: nanvix-zutil $docker_flag build"
        if (cd "$repo_dir" && "$venv_python" -m nanvix_zutil $docker_flag build 2>&1); then
            ok "  $consumer build: OK"
            phases="$phases,build"
        else
            fail "  $consumer build failed"
            results+=("$consumer: FAIL (build)")
            failed=$((failed + 1))
            continue
        fi

        # Phase 3: test
        log "  Running: nanvix-zutil $docker_flag test"
        if (cd "$repo_dir" && "$venv_python" -m nanvix_zutil $docker_flag test 2>&1); then
            ok "  $consumer test: OK"
            phases="$phases,test"
        else
            fail "  $consumer test failed"
            results+=("$consumer: FAIL (test)")
            failed=$((failed + 1))
            continue
        fi

        results+=("$consumer: OK ($phases)")
    done

    echo ""
    log "=== Linux Results ==="
    for r in "${results[@]}"; do echo "  $r"; done
    echo ""

    if [[ $failed -gt 0 ]]; then
        fail "Linux: $failed consumer(s) FAILED"
    else
        ok "Linux: All consumers passed!"
    fi
    TOTAL_FAILED=$((TOTAL_FAILED + failed))
}

# =============================================================================
# Windows runner (via WSL → pwsh.exe)
# =============================================================================

run_windows() {
    local work_dir="/mnt/c/tmp/nanvix-downstream-test"

    log "=== Windows Downstream Test ==="
    build_wheel "$work_dir"

    local wheel_win
    wheel_win=$(wslpath -w "$WHEEL_FILE")

    # Copy the PowerShell script and config into the work dir.
    local ps_src
    ps_src="$(dirname "$(readlink -f "$0")")/test-downstream.ps1"
    local ps_script="$work_dir/run-tests.ps1"
    cp "$ps_src" "$ps_script"
    ok "Copied $ps_script"

    # Copy config file to work dir so PS1 can read it natively.
    local config_copy="$work_dir/downstream.json"
    cp "$CONFIG_FILE" "$config_copy"
    local config_win
    config_win=$(wslpath -w "$config_copy")

    # Launch PowerShell — let it handle repo resolution with native Git for Windows.
    log "Launching pwsh.exe..."
    log "Setup only: $SETUP_ONLY"
    echo ""

    local ps_script_win
    ps_script_win=$(wslpath -w "$ps_script")

    local results_file="$work_dir/results.json"
    local results_win
    results_win=$(wslpath -w "$results_file")

    local setup_flag=""
    [[ "$SETUP_ONLY" == true ]] && setup_flag="-SetupOnly"

    local fallback_flag=""
    [[ "$FORCE_FALLBACK" == true ]] && fallback_flag="-ForceFallback"

    local consumers_flag=()
    if [[ "$USER_CONSUMERS" == true ]]; then
        local consumers_csv
        consumers_csv=$(IFS=','; echo "${CONSUMERS[*]}")
        consumers_flag=(-Consumers "$consumers_csv")
    fi

    pwsh.exe -NoProfile -ExecutionPolicy Bypass -File "$ps_script_win" \
        -WheelPath "$wheel_win" \
        -ConfigFile "$config_win" \
        -ResultsFile "$results_win" \
        "${consumers_flag[@]}" \
        $setup_flag $fallback_flag

    local rc=$?
    echo ""
    if [[ $rc -eq 0 ]]; then
        ok "Windows: All consumers passed!"
    else
        local win_failed=1
        if [[ -f "$results_file" ]]; then
            win_failed=$(jq '[.[] | select(.status != "OK")] | length' "$results_file" 2>/dev/null || echo 1)
        fi
        fail "Windows: $win_failed consumer(s) failed. See $results_file"
        TOTAL_FAILED=$((TOTAL_FAILED + win_failed))
    fi
}

# =============================================================================
# Main dispatch
# =============================================================================

case "$PLATFORM" in
linux)
    run_linux
    ;;
windows)
    run_windows
    ;;
both)
    run_linux
    echo ""
    echo "================================================================"
    echo ""
    run_windows
    ;;
*)
    fail "Unknown platform: $PLATFORM (use linux, windows, or both)"
    exit 1
    ;;
esac

echo ""
if [[ $TOTAL_FAILED -gt 0 ]]; then
    fail "Overall: $TOTAL_FAILED failure(s)"
    exit 1
else
    ok "Overall: All platforms passed!"
    exit 0
fi
