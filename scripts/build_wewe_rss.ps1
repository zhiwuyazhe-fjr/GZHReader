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

function Remove-PathRobust {
    param(
        [string]$LiteralPath,
        [int]$Retries = 5
    )

    if (-not (Test-Path $LiteralPath)) {
        return
    }

    for ($attempt = 1; $attempt -le $Retries; $attempt++) {
        try {
            Get-ChildItem -LiteralPath $LiteralPath -Recurse -Force -ErrorAction SilentlyContinue |
                Where-Object { -not $_.PSIsContainer } |
                ForEach-Object {
                    try {
                        $_.IsReadOnly = $false
                    } catch {
                    }
                }

            Remove-Item -LiteralPath $LiteralPath -Recurse -Force -ErrorAction Stop
        } catch {
            if (-not (Test-Path $LiteralPath)) {
                return
            }

            if ($attempt -ge $Retries) {
                throw "Failed to remove path after $Retries attempts: $LiteralPath`n$($_.Exception.Message)"
            }

            Start-Sleep -Milliseconds (250 * $attempt)
            continue
        }

        if (-not (Test-Path $LiteralPath)) {
            return
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
    Remove-PathRobust -LiteralPath $stagingDir
}
if (Test-Path $outputDir) {
    Remove-PathRobust -LiteralPath $outputDir
}

New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null

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

Write-Host "Creating deployable wewe-rss runtime..."
if ($pnpmLeaf -in @("corepack.exe", "corepack", "corepack.cmd")) {
    $corepackPnpm = "pnpm@8.15.8"
    Invoke-Step -FilePath $pnpmPath -Arguments @($corepackPnpm, "--filter", "server", "deploy", "--prod", $outputDir) -WorkingDirectory $stagingDir -Environment $envVars
}
else {
    Invoke-Step -FilePath $pnpmPath -Arguments @("--filter", "server", "deploy", "--prod", $outputDir) -WorkingDirectory $stagingDir -Environment $envVars
}

Copy-Item $nodePath (Join-Path $outputDir "node.exe") -Force

# pnpm deploy omits the generated ".prisma" runtime folder; copy it from the
# build workspace so the deployed server can load Prisma at runtime.
$prismaPackage = Get-ChildItem (Join-Path $stagingDir "node_modules\.pnpm") -Directory -Filter "@prisma+client@*" |
    Select-Object -First 1
if ($prismaPackage) {
    $prismaSourceDir = Join-Path $prismaPackage.FullName "node_modules\.prisma"
    $prismaDestDir = Join-Path $outputDir ("node_modules\.pnpm\" + $prismaPackage.Name + "\node_modules\.prisma")
    if (Test-Path $prismaSourceDir) {
        New-Item -ItemType Directory -Path (Split-Path $prismaDestDir -Parent) -Force | Out-Null
        robocopy $prismaSourceDir $prismaDestDir /E | Out-Null
        if ($LASTEXITCODE -gt 7) {
            throw "Failed to copy Prisma runtime assets into the deployed wewe-rss runtime."
        }
    }
}

# Trim development-only sources/configs from the packaged runtime.
foreach ($path in @(
    (Join-Path $outputDir "src"),
    (Join-Path $outputDir "test"),
    (Join-Path $outputDir ".eslintrc.js"),
    (Join-Path $outputDir ".gitignore"),
    (Join-Path $outputDir ".prettierrc.json"),
    (Join-Path $outputDir "nest-cli.json"),
    (Join-Path $outputDir "tsconfig.build.json"),
    (Join-Path $outputDir "tsconfig.json")
)) {
    if (Test-Path $path) {
        Remove-PathRobust -LiteralPath $path
    }
}

Write-Host "wewe-rss runtime built at: $outputDir"
