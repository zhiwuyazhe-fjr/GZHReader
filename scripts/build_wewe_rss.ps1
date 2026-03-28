param(
    [string]$NodeExe = "",
    [string]$PnpmExe = "",
    [string]$SourceDir = "",
    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"

function Resolve-CommandPath {
    param(
        [string]$PreferredPath,
        [string]$CommandName
    )

    if ($PreferredPath) {
        if (Test-Path $PreferredPath) {
            return (Resolve-Path $PreferredPath).Path
        }
        throw "Command not found at path: $PreferredPath"
    }

    $command = Get-Command $CommandName -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
    if ($command) {
        return $command
    }

    return $null
}

function Invoke-Step {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$WorkingDirectory,
        [hashtable]$Environment = @{}
    )

    $original = @{}
    foreach ($key in $Environment.Keys) {
        $original[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
        [Environment]::SetEnvironmentVariable($key, $Environment[$key], "Process")
    }

    try {
        Push-Location $WorkingDirectory
        & $FilePath @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "Command failed: $FilePath $($Arguments -join ' ')"
        }
    }
    finally {
        Pop-Location
        foreach ($key in $Environment.Keys) {
            [Environment]::SetEnvironmentVariable($key, $original[$key], "Process")
        }
    }
}

$projectRoot = (Resolve-Path "$PSScriptRoot\..").Path
if (-not $SourceDir) {
    $SourceDir = Join-Path $projectRoot "third_party\wewe-rss"
}
if (-not $OutputDir) {
    $OutputDir = Join-Path $projectRoot "build\wewe-rss-runtime"
}

$sourceDir = (Resolve-Path $SourceDir).Path
$buildRoot = Join-Path $projectRoot "build"
$stagingDir = Join-Path $buildRoot "wewe-rss-staging"
$outputDir = $OutputDir

$nodePath = Resolve-CommandPath -PreferredPath $NodeExe -CommandName "node"
if (-not $nodePath) {
    throw "Node.js 20+ is required. Install Node.js or pass -NodeExe."
}

$pnpmPath = Resolve-CommandPath -PreferredPath $PnpmExe -CommandName "pnpm"
if (-not $pnpmPath) {
    $corepackPath = Resolve-CommandPath -PreferredPath "" -CommandName "corepack"
    if (-not $corepackPath) {
        throw "pnpm or corepack is required. Install pnpm or use a Node.js install with corepack."
    }
    $pnpmPath = $corepackPath
}

if (Test-Path $stagingDir) {
    Remove-Item $stagingDir -Recurse -Force
}
if (Test-Path $outputDir) {
    Remove-Item $outputDir -Recurse -Force
}

New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
New-Item -ItemType Directory -Path $outputDir -Force | Out-Null

robocopy $sourceDir $stagingDir /E /XD .git node_modules .github .vscode | Out-Null
if ($LASTEXITCODE -gt 7) {
    throw "Failed to copy third_party/wewe-rss into the staging directory."
}

$envVars = @{
    "DATABASE_URL" = "file:../data/wewe-rss.db"
    "DATABASE_TYPE" = "sqlite"
}

$pnpmLeaf = (Split-Path $pnpmPath -Leaf).ToLowerInvariant()
if ($pnpmLeaf -in @("corepack.exe", "corepack", "corepack.cmd")) {
    $corepackPnpm = "pnpm@8.15.8"
    Invoke-Step -FilePath $pnpmPath -Arguments @($corepackPnpm, "install", "--frozen-lockfile") -WorkingDirectory $stagingDir -Environment $envVars
    Invoke-Step -FilePath $pnpmPath -Arguments @($corepackPnpm, "--filter", "server", "exec", "prisma", "generate", "--schema", "prisma/schema.prisma") -WorkingDirectory $stagingDir -Environment $envVars
    Invoke-Step -FilePath $pnpmPath -Arguments @($corepackPnpm, "--filter", "web", "build") -WorkingDirectory $stagingDir -Environment $envVars
    Invoke-Step -FilePath $pnpmPath -Arguments @($corepackPnpm, "--filter", "server", "build") -WorkingDirectory $stagingDir -Environment $envVars
}
else {
    Invoke-Step -FilePath $pnpmPath -Arguments @("install", "--frozen-lockfile") -WorkingDirectory $stagingDir -Environment $envVars
    Invoke-Step -FilePath $pnpmPath -Arguments @("--filter", "server", "exec", "prisma", "generate", "--schema", "prisma/schema.prisma") -WorkingDirectory $stagingDir -Environment $envVars
    Invoke-Step -FilePath $pnpmPath -Arguments @("--filter", "web", "build") -WorkingDirectory $stagingDir -Environment $envVars
    Invoke-Step -FilePath $pnpmPath -Arguments @("--filter", "server", "build") -WorkingDirectory $stagingDir -Environment $envVars
}

robocopy $stagingDir $outputDir /E /XD .git .github .vscode | Out-Null
if ($LASTEXITCODE -gt 7) {
    throw "Failed to copy the built runtime into the output directory."
}

Copy-Item $nodePath (Join-Path $outputDir "node.exe") -Force

Write-Host "wewe-rss runtime built at: $outputDir"
