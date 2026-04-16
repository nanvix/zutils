# test-downstream.ps1 — Windows downstream test runner.
#
# Installs a local nanvix-zutil wheel into a fresh venv per consumer repo
# and runs setup / build / test.
#
# Works in two modes:
#   1. Standalone (native Windows) — resolves repos from $ReposRoot using
#      the bare-repo / worktree layout, builds the wheel if needed.
#   2. WSL-launched — receives pre-resolved $RepoPaths and $WheelPath from
#      test-downstream.sh via pwsh.exe.
#
# Usage:
#   # Standalone — resolve repos and build wheel automatically:
#   pwsh test-downstream.ps1 -ZutilsRoot C:\path\to\zutils
#
#   # Standalone — setup only, specific consumer:
#   pwsh test-downstream.ps1 -ZutilsRoot C:\path\to\zutils -Consumers sqlite -SetupOnly
#
#   # WSL-launched — paths pre-resolved:
#   pwsh test-downstream.ps1 -WheelPath C:\tmp\wheel.whl -RepoPaths C:\repos\... -Consumers sqlite

param(
    [string]$WheelPath,
    [string]$ZutilsRoot,
    [string[]]$Consumers,
    [string[]]$RepoPaths,
    [string]$ReposRoot = (Join-Path $env:USERPROFILE "repos"),
    [string]$ResultsFile = (Join-Path $env:TEMP "nanvix-downstream-results.json"),
    [switch]$SetupOnly,
    [switch]$SkipBuild,
    [switch]$ForceFallback
)

# ForceFallback implies SetupOnly (same as bash script).
if ($ForceFallback)
{ $SetupOnly = [switch]::Present 
}

$ErrorActionPreference = 'Stop'
$results = @{}

# --- Fetch consumer list ------------------------------------------------------

$ConsumersUrl = "https://raw.githubusercontent.com/nanvix/workflows/refs/heads/main/consumer-repos.json"
$ConsumersCache = Join-Path $PSScriptRoot "consumer-repos.json"

if (-not $Consumers) {
    try {
        $json = Invoke-RestMethod -Uri $ConsumersUrl -ErrorAction Stop
        $json | ConvertTo-Json | Out-File -FilePath $ConsumersCache -Encoding utf8
        $Consumers = @($json)
    } catch {
        if (Test-Path $ConsumersCache) {
            Write-Host "Using cached consumer list" -ForegroundColor DarkGray
            $Consumers = @(Get-Content $ConsumersCache | ConvertFrom-Json)
        } else {
            Write-Host "ERROR: Cannot fetch consumer list and no cache at $ConsumersCache" -ForegroundColor Red
            exit 1
        }
    }
} elseif ($Consumers.Count -eq 1 -and $Consumers[0] -match ',') {
    # Comma-separated list passed from WSL bash — split it.
    $Consumers = $Consumers[0] -split ','
}

# Ensure GnuWin32 Make is on PATH (common install location)
$gnuwin32 = "C:\Program Files (x86)\GnuWin32\bin"
if ((Test-Path $gnuwin32) -and ($env:PATH -notlike "*GnuWin32*"))
{
    $env:PATH = "$gnuwin32;$env:PATH"
    Write-Host "Injected GnuWin32 into PATH: $gnuwin32" -ForegroundColor DarkGray
}

# --- Resolve-RepoDir ----------------------------------------------------------
# Mirrors resolve_repo_dir() from test-downstream.sh.
# Clones the bare repo from GitHub if it doesn't exist locally.
# Looks for existing nanvix\v* worktree directories, creates one if none exist.
function Resolve-RepoDir
{
    param(
        [string]$Consumer,
        [string]$Root
    )

    $bareRoot = Join-Path $Root $Consumer

    # Clone if missing.
    if (-not (Test-Path $bareRoot))
    {
        Write-Host "  $Consumer`: cloning bare repo to $bareRoot" -ForegroundColor DarkGray
        New-Item -ItemType Directory -Path $Root -Force | Out-Null
        $cloneUrl = "https://github.com/$Consumer.git"
        git clone --bare $cloneUrl $bareRoot 2>&1 |
            ForEach-Object { Write-Host "    $_" }
        if ($LASTEXITCODE -ne 0)
        {
            Write-Host "  $Consumer`: git clone --bare failed" -ForegroundColor Red
            return $null
        }
        # Set up fetch refspec (remote-tracking avoids checked-out-branch conflicts).
        git -C $bareRoot config remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'
        Write-Host "  $Consumer`: cloned" -ForegroundColor Green
    }

    # Ensure fetch refspec is correct (remote-tracking avoids checked-out-branch conflicts).
    $curFetch = git -C $bareRoot config --get remote.origin.fetch 2>$null
    if ($curFetch -ne '+refs/heads/*:refs/remotes/origin/*') {
        git -C $bareRoot config remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'
    }

    # Fetch latest from origin.
    Write-Host "  $Consumer`: fetching latest" -ForegroundColor DarkGray
    git -C $bareRoot fetch origin --prune 2>&1 | ForEach-Object { Write-Host "    $_" }

    # Look for existing nanvix\v* worktree directories.
    $wtParent = Join-Path $bareRoot "nanvix"
    if (Test-Path $wtParent)
    {
        $candidates = Get-ChildItem -Path $wtParent -Directory -Filter "v*" |
            Sort-Object Name
        if ($candidates.Count -gt 0)
        {
            $wtDir = $candidates[-1].FullName
            # Fetch and reset to latest from origin.
            $branch = git -C $wtDir rev-parse --abbrev-ref HEAD 2>$null
            Write-Host "  $Consumer`: updating worktree at $wtDir" -ForegroundColor DarkGray
            git -C $wtDir fetch origin 2>&1 | ForEach-Object { Write-Host "    $_" }
            if ($branch)
            {
                git -C $wtDir reset --hard "origin/$branch" 2>&1 | ForEach-Object { Write-Host "    $_" }
            }
            return $wtDir
        }
    }

    # No worktree yet — find the target branch.
    $targetRef = $null

    # Prefer nanvix/v* branches (check both local and remote ref namespaces).
    $refs = git -C $bareRoot for-each-ref --sort=version:refname `
        --format='%(refname:short)' 'refs/heads/nanvix/v*' 'refs/remotes/origin/nanvix/v*' 2>$null
    if ($refs)
    {
        $refList = @($refs) | Where-Object { $_ }
        if ($refList.Count -gt 0)
        {
            $targetRef = $refList[-1] -replace '^origin/', ''
        }
    }

    # Fallback: default branch (bare repo HEAD).
    if (-not $targetRef)
    {
        try
        {
            $symRef = git -C $bareRoot symbolic-ref HEAD 2>$null
            if ($symRef -match "refs/heads/(.+)")
            {
                $targetRef = $Matches[1]
            }
        } catch
        {
        }
    }

    if (-not $targetRef)
    {
        Write-Host "  $Consumer`: no nanvix/v* branch and cannot determine default branch" -ForegroundColor Red
        return $null
    }

    $wtDir = Join-Path $bareRoot $targetRef

    # If the worktree directory already exists, update it instead of creating.
    if (Test-Path $wtDir)
    {
        $branch = git -C $wtDir rev-parse --abbrev-ref HEAD 2>$null
        Write-Host "  $Consumer`: updating worktree at $wtDir" -ForegroundColor DarkGray
        git -C $wtDir fetch origin 2>&1 | ForEach-Object { Write-Host "    $_" }
        if ($branch)
        {
            git -C $wtDir reset --hard "origin/$branch" 2>&1 | ForEach-Object { Write-Host "    $_" }
        }
        return $wtDir
    }

    Write-Host "  $Consumer`: creating worktree for $targetRef" -ForegroundColor DarkGray
    git -C $bareRoot worktree add $wtDir $targetRef 2>&1 |
        ForEach-Object { Write-Host "    $_" }
    return $wtDir
}

# --- Build-Wheel --------------------------------------------------------------
# Builds a nanvix-zutil wheel from $ZutilsRoot into a temp directory.
function Build-Wheel
{
    param([string]$ZutilsPath)

    $wheelDir = Join-Path $env:TEMP "nanvix-downstream-test\wheel"
    if (-not (Test-Path $wheelDir))
    {
        New-Item -ItemType Directory -Path $wheelDir -Force | Out-Null
    }

    if ($SkipBuild)
    {
        $existing = Get-ChildItem -Path $wheelDir -Filter "*.whl" -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($existing)
        {
            Write-Host "Reusing: $($existing.Name)" -ForegroundColor Green
            return $existing.FullName
        }
        Write-Host "FAIL: No wheel found in $wheelDir. Run without -SkipBuild first." -ForegroundColor Red
        exit 1
    }

    Write-Host "Building nanvix-zutil wheel from $ZutilsPath" -ForegroundColor Cyan
    Remove-Item -Path (Join-Path $wheelDir "*.whl") -Force -ErrorAction SilentlyContinue

    pip wheel --no-deps --wheel-dir $wheelDir $ZutilsPath 2>&1 |
        Select-Object -Last 3 | ForEach-Object { Write-Host "  $_" }

    $built = Get-ChildItem -Path $wheelDir -Filter "*.whl" -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $built)
    {
        Write-Host "FAIL: Wheel build produced no .whl file" -ForegroundColor Red
        exit 1
    }
    Write-Host "Built: $($built.Name)" -ForegroundColor Green
    return $built.FullName
}

# --- Resolve wheel path -------------------------------------------------------
if (-not $WheelPath)
{
    if (-not $ZutilsRoot)
    {
        Write-Host "ERROR: Provide either -WheelPath or -ZutilsRoot" -ForegroundColor Red
        exit 1
    }
    $WheelPath = Build-Wheel -ZutilsPath $ZutilsRoot
}

# --- Resolve repo paths if not provided ---------------------------------------
if (-not $RepoPaths)
{
    Write-Host "Resolving repos from $ReposRoot ..." -ForegroundColor DarkGray
    $resolvedPaths = @()
    $resolvedConsumers = @()
    foreach ($c in $Consumers)
    {
        $dir = Resolve-RepoDir -Consumer $c -Root $ReposRoot
        if ($dir)
        {
            $resolvedPaths += $dir
            $resolvedConsumers += $c
        } else
        {
            $results[$c] = @{ status = "SKIP"; reason = "not found at $ReposRoot\$c" }
        }
    }
    $RepoPaths = $resolvedPaths
    $Consumers = $resolvedConsumers
}

Write-Host "`n=== nanvix-zutil Downstream Test (Windows) ===" -ForegroundColor Cyan
Write-Host "Wheel: $WheelPath"
Write-Host "Consumers: $($Consumers -join ', ')"
Write-Host "Repos root: $ReposRoot"
Write-Host "Setup only: $SetupOnly"
Write-Host ""

for ($i = 0; $i -lt $Consumers.Count; $i++)
{
    $consumer = $Consumers[$i]
    $repoDir = $RepoPaths[$i]
    Write-Host "`n--- Testing $consumer ---" -ForegroundColor Yellow
    Write-Host "  Repo: $repoDir"

    if (-not (Test-Path $repoDir))
    {
        Write-Host "  SKIP: repo not found at $repoDir" -ForegroundColor Red
        $results[$consumer] = @{ status = "SKIP"; reason = "not found" }
        continue
    }

    # Clean any existing venv to force reinstall with our wheel
    $venvDir = Join-Path $repoDir ".nanvix\venv"
    if (Test-Path $venvDir)
    {
        Write-Host "  Removing existing venv..."
        Remove-Item -Recurse -Force $venvDir -ErrorAction SilentlyContinue
    }

    # Create fresh venv and install our wheel
    Write-Host "  Creating venv and installing local wheel..."
    try
    {
        python -m venv $venvDir
        $venvPip = Join-Path $venvDir "Scripts\pip.exe"
        $venvPython = Join-Path $venvDir "Scripts\python.exe"
        & $venvPip install --quiet $WheelPath
        if ($LASTEXITCODE -ne 0)
        { throw "pip install failed" 
        }

        # Verify installed version
        $ver = & $venvPython -c "import nanvix_zutil; print('OK')" 2>&1
        Write-Host "  nanvix_zutil import: $ver"
    } catch
    {
        Write-Host "  FAIL: venv setup failed: $_" -ForegroundColor Red
        $results[$consumer] = @{ status = "FAIL"; phase = "venv"; error = "$_" }
        continue
    }

    # The shim code that z.ps1 uses to work around os.getuid/os.getgid
    $ShimCode = @'
import os,sys;os.getuid=getattr(os,"getuid",lambda:0);os.getgid=getattr(os,"getgid",lambda:0);from nanvix_zutil.__main__ import main;sys.exit(main())
'@

    # Phase 1: setup
    Write-Host "  Running: nanvix-zutil setup" -ForegroundColor Cyan
    Push-Location $repoDir
    try
    {

    if ($ForceFallback)
    {
        # Parse [dependencies] and set NANVIX_VERSION_<NAME> env vars
        # to force fallback resolution.
        Write-Host "  Forcing dependency fallback..." -ForegroundColor Yellow
        # Clean cached artifacts so downloads are forced (matches bash behavior).
        $buildroot = Join-Path $repoDir ".nanvix\buildroot"
        $cache = Join-Path $repoDir ".nanvix\cache"
        if (Test-Path $buildroot) { Remove-Item -Recurse -Force $buildroot -ErrorAction SilentlyContinue }
        if (Test-Path $cache) { Remove-Item -Recurse -Force $cache -ErrorAction SilentlyContinue }
        Write-Host "  Cleaned buildroot and cache" -ForegroundColor DarkGray
        $manifest = Join-Path $repoDir ".nanvix\nanvix.toml"
        $inDeps = $false
        foreach ($line in Get-Content $manifest)
        {
            if ($line -match '^\[dependencies\]')
            { $inDeps = $true; continue 
            }
            if ($line -match '^\[')
            { $inDeps = $false; continue 
            }
            if ($inDeps -and $line -match '^([a-zA-Z0-9_-]+)\s*=\s*"([^"]+)"')
            {
                $depName = $Matches[1].ToUpper() -replace '-','_'
                $depVer = $Matches[2]
                $envKey = "NANVIX_VERSION_$depName"
                $envVal = "$depVer-nanvix-99.99.99"
                [Environment]::SetEnvironmentVariable($envKey, $envVal, "Process")
                Write-Host "    $envKey=$envVal" -ForegroundColor DarkGray
            }
        }
    }

    $setupOutput = & $venvPython -c $ShimCode setup 2>&1
    $setupOutput | ForEach-Object { Write-Host "    $_" }
    $setupExit = $LASTEXITCODE

    if ($ForceFallback)
    {
        $fallbackDetected = ($setupExit -eq 7) -or
            (($setupOutput | Out-String) -match '(?i)fallback for')

        if ($fallbackDetected)
        {
            Write-Host "  setup: DEGRADED (exit $setupExit) — fallback reporting works" -ForegroundColor Green
            $results[$consumer] = @{ status = "OK"; phases = @("fallback verified") }
        } elseif ($setupExit -eq 0)
        {
            Write-Host "  FAIL: expected exit 7 but got 0 — fallback not triggered" -ForegroundColor Red
            $results[$consumer] = @{ status = "FAIL"; phase = "setup"; error = "fallback not triggered" }
        } else
        {
            Write-Host "  FAIL: expected exit 7 but got $setupExit" -ForegroundColor Red
            $results[$consumer] = @{ status = "FAIL"; phase = "setup"; error = "exit $setupExit" }
        }
        continue
    }

    if ($setupExit -ne 0)
    {
        Write-Host "  FAIL: setup failed (exit $setupExit)" -ForegroundColor Red
        $results[$consumer] = @{ status = "FAIL"; phase = "setup"; error = "exit $setupExit" }
        continue
    }
    Write-Host "  setup: OK" -ForegroundColor Green
    $results[$consumer] = @{ status = "OK"; phases = @("setup") }

    if ($SetupOnly)
    {
        Write-Host "  (skipping build/test — setup-only mode)" -ForegroundColor DarkGray
        continue
    }

    # Phase 2: build (always --with-docker on Windows)
    Write-Host "  Running: nanvix-zutil --with-docker build" -ForegroundColor Cyan
    try
    {
        & $venvPython -c $ShimCode --with-docker build 2>&1 | ForEach-Object { Write-Host "    $_" }
        if ($LASTEXITCODE -ne 0)
        { throw "build failed (exit $LASTEXITCODE)" 
        }
        Write-Host "  build: OK" -ForegroundColor Green
        $results[$consumer].phases += "build"
    } catch
    {
        Write-Host "  FAIL: build: $_" -ForegroundColor Red
        $results[$consumer] = @{ status = "FAIL"; phase = "build"; error = "$_" }
        continue
    }

    # Phase 3: test (always --with-docker on Windows)
    Write-Host "  Running: nanvix-zutil --with-docker test" -ForegroundColor Cyan
    try
    {
        & $venvPython -c $ShimCode --with-docker test 2>&1 | ForEach-Object { Write-Host "    $_" }
        if ($LASTEXITCODE -ne 0)
        { throw "test failed (exit $LASTEXITCODE)" 
        }
        Write-Host "  test: OK" -ForegroundColor Green
        $results[$consumer].phases += "test"
    } catch
    {
        Write-Host "  FAIL: test: $_" -ForegroundColor Red
        $results[$consumer] = @{ status = "FAIL"; phase = "test"; error = "$_" }
        continue
    }

    }
    finally
    {
        Pop-Location
    }
}

# Write results
Write-Host "`n=== Results ===" -ForegroundColor Cyan
$json = $results | ConvertTo-Json -Depth 3
$json | Out-File -FilePath $ResultsFile -Encoding utf8
Write-Host $json

$failed = ($results.Values | Where-Object { $_.status -ne "OK" }).Count
if ($failed -gt 0)
{
    Write-Host "`n$failed consumer(s) FAILED" -ForegroundColor Red
    exit 1
} else
{
    Write-Host "`nAll consumers passed!" -ForegroundColor Green
    exit 0
}
