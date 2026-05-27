# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

# Thin wrapper that delegates to the nanvix-zutil CLI.
# Self-bootstraps nanvix-zutil into .nanvix\venv\ if it is not already installed.

param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ZArgs
)

$ErrorActionPreference = 'Stop'

# z.ps1 lives at the repository root, so use its directory directly
# instead of relying on git to discover the top-level checkout directory.
$repoRoot = $PSScriptRoot
$versionFile = Join-Path $repoRoot ".zutils-version"

# Resolve the pinned nanvix-zutil version.
#
# Precedence:
#   1. NANVIX_ZUTIL_VERSION env var (explicit override).
#   2. .zutils-version file at repo root (source of truth).
#   3. Fetch the latest release from GitHub and pin it by writing
#      .zutils-version. The user is told to commit the file.
function Get-LatestZutilTag {
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        Write-Error -ErrorAction Continue "Error: .zutils-version not found and 'gh' is unavailable to fetch the latest release.`n       Create a .zutils-version file with a pinned version (e.g. 'v0.10.2') or install the GitHub CLI: https://cli.github.com"
        return $null
    }
    # --exclude-drafts/--exclude-pre-releases avoids accidentally pinning
    # an in-flight draft if the caller has write access to nanvix/zutils.
    $tag = $null
    try {
        $tag = & gh release list --repo nanvix/zutils `
            --exclude-drafts --exclude-pre-releases `
            -L 1 --json tagName --jq '.[0].tagName' 2>$null
    }
    catch {
        $tag = $null
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Error -ErrorAction Continue "Error: failed to fetch latest nanvix/zutils release via 'gh'. Are you authenticated? (gh auth login)`n       Alternatively, create a .zutils-version file with a pinned version (e.g. 'v0.10.2')."
        return $null
    }
    if ([string]::IsNullOrWhiteSpace($tag)) {
        Write-Error -ErrorAction Continue "Error: 'gh' returned no stable releases for nanvix/zutils."
        return $null
    }
    return $tag.Trim()
}

function Resolve-ZutilVersion {
    $source = $null
    $raw = $null
    $pinAfterValidation = $false
    if ($env:NANVIX_ZUTIL_VERSION) {
        $raw = $env:NANVIX_ZUTIL_VERSION
        $source = "NANVIX_ZUTIL_VERSION env var"
    }
    elseif (Test-Path -LiteralPath $versionFile) {
        $content = Get-Content -LiteralPath $versionFile -Raw
        $raw = if ($null -eq $content) { "" } else { $content.Trim() }
        $source = ".zutils-version"
        if ([string]::IsNullOrWhiteSpace($raw)) {
            throw "Error: $versionFile exists but is empty."
        }
    }
    else {
        $raw = Get-LatestZutilTag
        if (-not $raw) {
            # Get-LatestZutilTag already wrote an actionable error.
            exit 1
        }
        $source = "GitHub latest release"
        $pinAfterValidation = $true
    }
    if ($raw -notmatch '^v?\d+\.\d+\.\d+([.-][A-Za-z0-9.-]+)?$') {
        throw "Error: invalid nanvix-zutil version '$raw' from $source (expected vX.Y.Z)."
    }
    if ($pinAfterValidation) {
        Set-Content -LiteralPath $versionFile -Value $raw
        Write-Information "nanvix-zutil: no .zutils-version found; pinned to latest release $raw and wrote $versionFile." -InformationAction Continue
        Write-Information "             Commit this file to lock the version for your repo, or set NANVIX_ZUTIL_VERSION to skip this auto-pin next time." -InformationAction Continue
    }
    return $raw
}

$rawZutilVersion = Resolve-ZutilVersion
$zutilVersion = $rawZutilVersion -replace "^v", ""

$venvDir = Join-Path $repoRoot ".nanvix\venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$venvZutil = Join-Path $venvDir "Scripts\nanvix-zutil.exe"

# Windows compatibility shim: nanvix-zutil references os.getuid/os.getgid
# which are unavailable on Windows.  Stub them before importing the package.
# NOTE: Use single quotes inside the Python code so that PowerShell does not
# strip the quotes when passing the string to python.exe -c.
$ShimCode = @'
import os,sys;os.getuid=getattr(os,'getuid',lambda:0);os.getgid=getattr(os,'getgid',lambda:0);from nanvix_zutil.__main__ import main;sys.exit(main())
'@

$zutilGlobalVersion = try {
    & nanvix-zutil --version 2>$null
}
catch {
    $null
}

# Extract --with-zutils PATH and --with-nanvix PATH before forwarding to
# nanvix-zutil.
#
# --with-zutils PATH (optional): install nanvix-zutil from a local source
# tree (editable) instead of fetching the pinned wheel from GitHub Releases.
# Useful when iterating on zutils itself against a downstream consumer repo.
#
#   .\z.ps1 --with-zutils C:\src\zutils build
#   .\z.ps1 --with-zutils=C:\src\zutils build
#
# The path must point at a nanvix-zutil source checkout (a directory
# containing pyproject.toml).  The venv version-match check is bypassed; the
# editable install is rebuilt only when the recorded source path changes.
$withZutils = $null
$filteredArgs = [System.Collections.Generic.List[string]]::new()
$i = 0
while ($i -lt $ZArgs.Count) {
    if ($ZArgs[$i] -eq '--with-zutils') {
        if ($i + 1 -ge $ZArgs.Count) {
            throw "ERROR: --with-zutils requires a path argument"
        }
        $withZutils = $ZArgs[$i + 1]
        $i += 2
    }
    elseif ($ZArgs[$i] -match '^--with-zutils=(.+)$') {
        $withZutils = $Matches[1]
        $i++
    }
    elseif ($ZArgs[$i] -eq '--with-nanvix') {
        if ($i + 1 -ge $ZArgs.Count) {
            throw "ERROR: --with-nanvix requires a path argument"
        }
        $item = Get-Item -LiteralPath $ZArgs[$i + 1] -ErrorAction Stop
        if (-not $item.PSIsContainer) {
            throw "ERROR: --with-nanvix path is not a directory: $($ZArgs[$i + 1])"
        }
        $env:WITH_NANVIX = $item.FullName
        $i += 2
    }
    elseif ($ZArgs[$i] -match '^--with-nanvix=(.+)$') {
        $item = Get-Item -LiteralPath $Matches[1] -ErrorAction Stop
        if (-not $item.PSIsContainer) {
            throw "ERROR: --with-nanvix path is not a directory: $($Matches[1])"
        }
        $env:WITH_NANVIX = $item.FullName
        $i++
    }
    else {
        $filteredArgs.Add($ZArgs[$i])
        $i++
    }
}

if ($withZutils) {
    $resolved = Resolve-Path -LiteralPath $withZutils -ErrorAction SilentlyContinue
    if (-not $resolved) {
        throw "ERROR: --with-zutils path does not exist: $withZutils"
    }
    if (-not (Test-Path -LiteralPath $resolved.Path -PathType Container)) {
        throw "ERROR: --with-zutils is not a directory: $($resolved.Path)"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $resolved.Path 'pyproject.toml'))) {
        throw "ERROR: --with-zutils is not a Python source tree (no pyproject.toml): $($resolved.Path)"
    }
    $withZutils = $resolved.Path
}

function NewZutilVenv {
    # Discover a Python 3 interpreter and (re)create the venv.
    $venvArgs = @("-m", "venv")
    if (Test-Path $venvDir) {
        $venvArgs += "--clear"
    }
    $venvArgs += $venvDir

    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py -3 @venvArgs
    }
    elseif (Get-Command python -ErrorAction SilentlyContinue) {
        & python @venvArgs
    }
    elseif (Get-Command python3 -ErrorAction SilentlyContinue) {
        & python3 @venvArgs
    }
    else {
        throw "Python 3 not found. Install Python 3 and ensure py, python, or python3 is on PATH."
    }
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
        throw "venv creation failed (exit code $LASTEXITCODE)"
    }
}

function Bootstrap {
    param([string]$Reason = "not found")
    # Pin nanvix-zutil version for reproducible bootstrapping.
    # Override with NANVIX_ZUTIL_VERSION env var if needed.
    Write-Information "nanvix-zutil ${Reason} -- bootstrapping nanvix-zutil==${zutilVersion}..." -InformationAction Continue

    $wheelUrl = "https://github.com/nanvix/zutils/releases/download/v${zutilVersion}/nanvix_zutil-${zutilVersion}-py3-none-any.whl"
    NewZutilVenv
    & $venvPython -m pip install --quiet "nanvix-zutil[lint] @ $($wheelUrl)"
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
        throw "pip install failed (exit code $LASTEXITCODE)"
    }
}

function BootstrapLocal {
    # Install nanvix-zutil from $withZutils as an editable install.
    Write-Information "nanvix-zutil -- installing editable from ${withZutils}..." -InformationAction Continue
    NewZutilVenv
    & $venvPython -m pip install --quiet -e "$($withZutils)[lint]"
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) {
        throw "pip install (editable) failed (exit code $LASTEXITCODE)"
    }
    Set-Content -LiteralPath (Join-Path $venvDir '.with-zutils') -Value $withZutils -NoNewline
}

# Prefer the venv copy if it exists; otherwise use the global install.
$bin = $null
if ($withZutils) {
    $zutilsMarker = Join-Path $venvDir '.with-zutils'
    $recordedZutils = if (Test-Path -LiteralPath $zutilsMarker) {
        (Get-Content -LiteralPath $zutilsMarker -Raw).Trim()
    }
    else { $null }
    if ((-not (Test-Path $venvZutil)) -or ($recordedZutils -ne $withZutils)) {
        BootstrapLocal
    }
    if (-not (Test-Path $venvZutil)) {
        throw "BootstrapLocal completed but $venvZutil not found."
    }
    $bin = $venvZutil
}
elseif ((-not (Test-Path $venvDir)) -and (-not $zutilGlobalVersion)) {
    Bootstrap
    if (-not (Test-Path $venvZutil)) {
        throw "Bootstrap completed but $venvZutil not found."
    }
    $bin = $venvZutil
}
elseif (Test-Path $venvZutil) {
    # Use the shim to check version -- running the exe directly fails on
    # Windows because os.getuid/os.getgid are unavailable (see FIXME above).
    $venvVersion = try {
        & $venvPython -c $ShimCode --version 2>$null
    }
    catch {
        $null
    }
    if ($venvVersion -ne "nanvix-zutil ${zutilVersion}") {
        Write-Warning "Venv nanvix-zutil version mismatch. Expected ${zutilVersion}, found ${venvVersion}. Re-bootstrapping..."
        Bootstrap
        if (-not (Test-Path $venvZutil)) {
            throw "Bootstrap completed but $venvZutil not found."
        }
    }
    $bin = $venvZutil
}
elseif ((Test-Path $venvDir) -and (-not $zutilGlobalVersion)) {
    Write-Warning "Incomplete venv detected (binary missing). Re-running bootstrap..."
    Bootstrap
    if (-not (Test-Path $venvZutil)) {
        throw "Bootstrap completed but $venvZutil not found."
    }
    $bin = $venvZutil
}
else {
    $bin = "nanvix-zutil"
    if ($zutilGlobalVersion -ne "nanvix-zutil ${zutilVersion}") {
        Write-Warning "nanvix-zutil global install does not match expected version. Expected ${zutilVersion}, found ${zutilGlobalVersion}."
        Bootstrap "version mismatch"
        if (-not (Test-Path $venvZutil)) {
            throw "Bootstrap completed but $venvZutil not found."
        }
        $bin = $venvZutil
    }
}

$filteredArray = $filteredArgs.ToArray()

if ($bin -eq $venvZutil) {
    & $venvPython -c $ShimCode @filteredArray
}
else {
    & $bin @filteredArray
}

$ec = $LASTEXITCODE

# On Windows the venv's python.exe is locked while it runs, so the Python
# distclean command cannot delete it.  Now that the interpreter has exited the
# lock is released and the shell can safely remove the venv directory.
if ($filteredArray -and $filteredArray[0] -eq "distclean" -and (Test-Path $venvDir)) {
    Remove-Item $venvDir -Recurse -Force -ErrorAction SilentlyContinue
}

exit $ec
