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

# --- Config generation --------------------------------------------------------

$ConsumersUrl = "https://raw.githubusercontent.com/nanvix/workflows/refs/heads/main/consumer-repos.json"
$ConsumersCache = Join-Path $PSScriptRoot "consumer-repos.json"

# Ensure-Config <ConfigFile>
#   Generate downstream.json from consumer-repos.json on first run.
function Ensure-Config
{
    param([string]$ConfigFile)

    if (Test-Path $ConfigFile) { return }

    Write-Host "No downstream.json found - generating from consumer-repos.json..." -ForegroundColor Cyan

    $json = $null
    try
    {
        $json = Invoke-RestMethod -Uri $ConsumersUrl -ErrorAction Stop
        $json | ConvertTo-Json | Out-File -FilePath $ConsumersCache -Encoding utf8
        Write-Host "  Fetched consumer list from remote" -ForegroundColor DarkGray
    }
    catch
    {
        if (Test-Path $ConsumersCache)
        {
            $json = Get-Content $ConsumersCache | ConvertFrom-Json
            Write-Host "  Using cached consumer-repos.json" -ForegroundColor DarkGray
        }
        else
        {
            Write-Host "ERROR: Cannot fetch consumer list and no cache at $ConsumersCache" -ForegroundColor Red
            exit 1
        }
    }

    $config = @{
        '$schema'  = './downstream.schema.json'
        defaults   = @{
            checkout_strategy = 'shallow'
            repos_root        = '~/repos'
            win_repos_root    = $null
            branch_pattern    = 'nanvix/v*'
        }
        consumers  = @($json | ForEach-Object { @{ repo = $_ } })
    }

    $config | ConvertTo-Json -Depth 3 | Out-File -FilePath $ConfigFile -Encoding utf8
    Write-Host "Generated $ConfigFile - customize as needed." -ForegroundColor Cyan
}

# --- Fetch consumer list ------------------------------------------------------

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

# --- Resolve-RepoDir (multi-strategy) -----------------------------------------

# Detect-CheckoutStrategy <RepoPath>
#   Auto-detect the checkout strategy for an existing repo path.
#   Returns: bare, clone, or shallow (default for new/unknown).
function Detect-CheckoutStrategy
{
    param([string]$RepoPath)

    if (-not (Test-Path $RepoPath))
    {
        return "shallow"
    }
    if ((Test-Path (Join-Path $RepoPath "HEAD")) -and -not (Test-Path (Join-Path $RepoPath ".git") -PathType Container))
    {
        return "bare"
    }
    if (Test-Path (Join-Path $RepoPath ".git") -PathType Leaf)
    {
        # .git is a file → gitdir pointer → worktree of a bare repo
        return "bare"
    }
    if (Test-Path (Join-Path $RepoPath ".git") -PathType Container)
    {
        return "clone"
    }
    return "shallow"
}

# Resolve-Branch <Consumer> <RepoPath> <Strategy> [BranchPattern]
#   Resolve the target branch for a consumer repo.
function Resolve-Branch
{
    param(
        [string]$Consumer,
        [string]$RepoPath,
        [string]$Strategy,
        [string]$BranchPattern = "nanvix/v*"
    )

    $targetRef = $null

    if ($Strategy -eq "bare" -and (Test-Path $RepoPath))
    {
        $refs = git -C $RepoPath for-each-ref --sort=version:refname `
            --format='%(refname:short)' "refs/heads/$BranchPattern" "refs/remotes/origin/$BranchPattern" 2>$null
        if ($refs)
        {
            $refList = @($refs) | Where-Object { $_ }
            if ($refList.Count -gt 0)
            {
                $targetRef = $refList[-1] -replace '^origin/', ''
            }
        }

        # Fallback: bare repo HEAD.
        if (-not $targetRef)
        {
            try
            {
                $symRef = git -C $RepoPath symbolic-ref HEAD 2>$null
                if ($symRef -match "refs/heads/(.+)")
                {
                    $targetRef = $Matches[1]
                }
            } catch { }
        }
    }
    else
    {
        # Use git ls-remote for clone/shallow.
        $remoteRefs = git ls-remote --heads "https://github.com/$Consumer.git" $BranchPattern 2>$null
        if ($remoteRefs)
        {
            $refList = @($remoteRefs) | ForEach-Object {
                if ($_ -match 'refs/heads/(.+)$') { $Matches[1] }
            } | Sort-Object { [version]($_ -replace '^nanvix/v', '' -replace '[^0-9.]', '') } -ErrorAction SilentlyContinue
            if ($refList) { $targetRef = @($refList)[-1] }
        }

        # Fallback: default branch via ls-remote HEAD.
        if (-not $targetRef)
        {
            $headRef = git ls-remote --symref "https://github.com/$Consumer.git" HEAD 2>$null
            if ($headRef)
            {
                $line = @($headRef) | Where-Object { $_ -match '^ref:' } | Select-Object -First 1
                if ($line -match 'refs/heads/(.+)\s')
                {
                    $targetRef = $Matches[1]
                }
            }
        }
    }

    return $targetRef
}

# Resolve-RepoBare <Consumer> <RepoPath> <Branch>
#   Bare-repo + worktree strategy (original behavior).
function Resolve-RepoBare
{
    param(
        [string]$Consumer,
        [string]$RepoPath,
        [string]$Branch
    )

    # Clone if missing.
    if (-not (Test-Path $RepoPath))
    {
        Write-Host "  $Consumer`: cloning bare repo to $RepoPath" -ForegroundColor DarkGray
        New-Item -ItemType Directory -Path (Split-Path $RepoPath -Parent) -Force | Out-Null
        $cloneUrl = "https://github.com/$Consumer.git"
        git clone --bare $cloneUrl $RepoPath 2>&1 |
            ForEach-Object { Write-Host "    $_" }
        if ($LASTEXITCODE -ne 0)
        {
            Write-Host "  $Consumer`: git clone --bare failed" -ForegroundColor Red
            return $null
        }
        git -C $RepoPath config remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'
        Write-Host "  $Consumer`: cloned" -ForegroundColor Green
    }

    # Ensure fetch refspec is correct.
    $curFetch = git -C $RepoPath config --get remote.origin.fetch 2>$null
    if ($curFetch -ne '+refs/heads/*:refs/remotes/origin/*')
    {
        git -C $RepoPath config remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'
    }

    # Fetch latest.
    Write-Host "  $Consumer`: fetching latest" -ForegroundColor DarkGray
    git -C $RepoPath fetch origin --prune 2>&1 | ForEach-Object { Write-Host "    $_" }

    # Look for existing nanvix\v* worktree directories.
    $wtParent = Join-Path $RepoPath "nanvix"
    if (Test-Path $wtParent)
    {
        $candidates = Get-ChildItem -Path $wtParent -Directory -Filter "v*" |
            Sort-Object Name
        if ($candidates.Count -gt 0)
        {
            $wtDir = $candidates[-1].FullName
            $curBranch = git -C $wtDir rev-parse --abbrev-ref HEAD 2>$null
            Write-Host "  $Consumer`: updating worktree at $wtDir" -ForegroundColor DarkGray
            git -C $wtDir fetch origin 2>&1 | ForEach-Object { Write-Host "    $_" }
            if ($curBranch)
            {
                git -C $wtDir reset --hard "origin/$curBranch" 2>&1 | ForEach-Object { Write-Host "    $_" }
            }
            return $wtDir
        }
    }

    $wtDir = Join-Path $RepoPath $Branch

    # Update existing worktree.
    if (Test-Path $wtDir)
    {
        $curBranch = git -C $wtDir rev-parse --abbrev-ref HEAD 2>$null
        Write-Host "  $Consumer`: updating worktree at $wtDir" -ForegroundColor DarkGray
        git -C $wtDir fetch origin 2>&1 | ForEach-Object { Write-Host "    $_" }
        if ($curBranch)
        {
            git -C $wtDir reset --hard "origin/$curBranch" 2>&1 | ForEach-Object { Write-Host "    $_" }
        }
        return $wtDir
    }

    Write-Host "  $Consumer`: creating worktree for $Branch" -ForegroundColor DarkGray
    git -C $RepoPath worktree add $wtDir $Branch 2>&1 |
        ForEach-Object { Write-Host "    $_" }
    return $wtDir
}

# Resolve-RepoClone <Consumer> <RepoPath> <Branch>
#   Standard clone strategy.
function Resolve-RepoClone
{
    param(
        [string]$Consumer,
        [string]$RepoPath,
        [string]$Branch
    )

    if (-not (Test-Path $RepoPath))
    {
        Write-Host "  $Consumer`: cloning to $RepoPath" -ForegroundColor DarkGray
        New-Item -ItemType Directory -Path (Split-Path $RepoPath -Parent) -Force | Out-Null
        git clone "https://github.com/$Consumer.git" $RepoPath 2>&1 |
            ForEach-Object { Write-Host "    $_" }
        if ($LASTEXITCODE -ne 0)
        {
            Write-Host "  $Consumer`: git clone failed" -ForegroundColor Red
            return $null
        }
        Write-Host "  $Consumer`: cloned" -ForegroundColor Green
    }

    Write-Host "  $Consumer`: fetching and checking out $Branch" -ForegroundColor DarkGray
    git -C $RepoPath fetch origin 2>&1 | ForEach-Object { Write-Host "    $_" }
    git -C $RepoPath checkout $Branch 2>$null
    if ($LASTEXITCODE -ne 0) { git -C $RepoPath checkout -b $Branch "origin/$Branch" 2>&1 | ForEach-Object { Write-Host "    $_" } }
    git -C $RepoPath reset --hard "origin/$Branch" 2>&1 | ForEach-Object { Write-Host "    $_" }
    return $RepoPath
}

# Resolve-RepoShallow <Consumer> <RepoPath> <Branch>
#   Shallow clone strategy (default for new repos — fastest).
function Resolve-RepoShallow
{
    param(
        [string]$Consumer,
        [string]$RepoPath,
        [string]$Branch
    )

    if (-not (Test-Path $RepoPath))
    {
        Write-Host "  $Consumer`: shallow clone ($Branch) to $RepoPath" -ForegroundColor DarkGray
        New-Item -ItemType Directory -Path (Split-Path $RepoPath -Parent) -Force | Out-Null
        git clone --depth 1 -b $Branch "https://github.com/$Consumer.git" $RepoPath 2>&1 |
            ForEach-Object { Write-Host "    $_" }
        if ($LASTEXITCODE -ne 0)
        {
            Write-Host "  $Consumer`: shallow clone failed" -ForegroundColor Red
            return $null
        }
        Write-Host "  $Consumer`: cloned (shallow)" -ForegroundColor Green
        return $RepoPath
    }

    Write-Host "  $Consumer`: updating shallow clone" -ForegroundColor DarkGray
    git -C $RepoPath fetch --depth 1 origin $Branch 2>&1 | ForEach-Object { Write-Host "    $_" }
    git -C $RepoPath reset --hard "origin/$Branch" 2>&1 | ForEach-Object { Write-Host "    $_" }
    return $RepoPath
}

# Resolve-RepoDir <Consumer> <Root> [Strategy] [Branch] [BranchPattern]
#   Resolve a consumer repo to a working directory path.
#   Auto-detects strategy and branch if not provided.
function Resolve-RepoDir
{
    param(
        [string]$Consumer,
        [string]$Root,
        [string]$Strategy,
        [string]$Branch,
        [string]$BranchPattern = "nanvix/v*"
    )

    $repoPath = Join-Path $Root $Consumer

    # Auto-detect strategy if not specified.
    if (-not $Strategy)
    {
        $Strategy = Detect-CheckoutStrategy -RepoPath $repoPath
        Write-Host "  $Consumer`: auto-detected strategy: $Strategy" -ForegroundColor DarkGray
    }

    # Resolve branch if not specified.
    if (-not $Branch)
    {
        $Branch = Resolve-Branch -Consumer $Consumer -RepoPath $repoPath -Strategy $Strategy -BranchPattern $BranchPattern
    }

    if (-not $Branch)
    {
        Write-Host "  $Consumer`: cannot determine target branch" -ForegroundColor Red
        return $null
    }

    Write-Host "  $Consumer`: strategy=$Strategy branch=$Branch" -ForegroundColor DarkGray

    switch ($Strategy)
    {
        "bare"    { return Resolve-RepoBare    -Consumer $Consumer -RepoPath $repoPath -Branch $Branch }
        "clone"   { return Resolve-RepoClone   -Consumer $Consumer -RepoPath $repoPath -Branch $Branch }
        "shallow" { return Resolve-RepoShallow -Consumer $Consumer -RepoPath $repoPath -Branch $Branch }
        default   {
            Write-Host "  $Consumer`: unknown checkout strategy: $Strategy" -ForegroundColor Red
            return $null
        }
    }
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
