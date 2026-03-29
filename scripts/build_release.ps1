param(
    [string]$PythonExe = "",
    [string]$InnoSetupExe = "",
    [string]$NodeExe = "",
    [string]$PnpmExe = "",
    [switch]$SkipBundledRSSBuild,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"

function Stop-LocalBuildProcesses {
    param([string]$TargetRoot)

    Get-Process -ErrorAction SilentlyContinue | ForEach-Object {
        $process = $_
        try {
            $processPath = $process.Path
        } catch {
            $processPath = $null
        }

        if (-not $processPath) {
            return
        }

        if ($processPath.StartsWith($TargetRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
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

function Resolve-IsccPath {
    param([string]$PreferredPath = "")

    if ($PreferredPath) {
        if (Test-Path $PreferredPath) {
            return (Resolve-Path $PreferredPath).Path
        }
        throw "指定的 Inno Setup 编译器不存在：$PreferredPath"
    }

    # Stable Inno Setup 6.7.1 works with the flattened runtime layout we now ship.
    # Inno Setup 7 preview builds currently reproduce an uninstall-time
    # "PathRedir: Not initialized" error, so prefer stable releases first.
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
        'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
        'C:\Program Files\Inno Setup 6\ISCC.exe',
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 7\ISCC.exe'),
        'C:\Program Files\Inno Setup 7\ISCC.exe',
        'C:\Program Files (x86)\Inno Setup 7\ISCC.exe'
    ) | Where-Object { $_ }

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return (Resolve-Path $candidate).Path
        }
    }

    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
    if ($command) {
        return $command
    }

    $registryRoots = @(
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'
    )

    $registryIscc = @()
    foreach ($root in $registryRoots) {
        if (-not (Test-Path $root)) {
            continue
        }

        foreach ($subkey in Get-ChildItem $root -ErrorAction SilentlyContinue) {
            $item = Get-ItemProperty $subkey.PSPath -ErrorAction SilentlyContinue
            if (-not $item -or $item.DisplayName -notlike 'Inno Setup*') {
                continue
            }

            foreach ($value in @($item.InstallLocation, $item.DisplayIcon)) {
                if (-not $value) {
                    continue
                }

                $candidate = $value.Trim('"')
                if ($candidate -like '*.exe') {
                    if (Test-Path $candidate) {
                        $registryIscc += (Resolve-Path $candidate).Path
                    }
                } else {
                    $candidateExe = Join-Path $candidate 'ISCC.exe'
                    if (Test-Path $candidateExe) {
                        $registryIscc += (Resolve-Path $candidateExe).Path
                    }
                }
            }
        }
    }

    $preferStable6 = $registryIscc | Where-Object { $_ -like '*Inno Setup 6*' } | Select-Object -First 1
    if ($preferStable6) {
        return $preferStable6
    }
    return $registryIscc | Select-Object -First 1
}

function Assert-ProjectedInstallPathLengthsSafe {
    param(
        [string]$SourceRoot,
        [string]$ProjectedInstallRoot,
        [int]$MaxPathLength = 240
    )

    $sourceRootResolved = (Resolve-Path $SourceRoot).Path.TrimEnd('\')
    $violations = @()

    Get-ChildItem -LiteralPath $sourceRootResolved -Recurse -Force -File | ForEach-Object {
        $relativePath = $_.FullName.Substring($sourceRootResolved.Length).TrimStart('\')
        $projectedInstallPath = Join-Path $ProjectedInstallRoot $relativePath
        if ($projectedInstallPath.Length -gt $MaxPathLength) {
            $violations += [pscustomobject]@{
                RelativePath = $relativePath
                ProjectedLength = $projectedInstallPath.Length
                ProjectedPath = $projectedInstallPath
            }
        }
    }

    if ($violations.Count -gt 0) {
        $examples = $violations |
            Sort-Object ProjectedLength -Descending |
            Select-Object -First 5 |
            ForEach-Object { "$($_.ProjectedLength): $($_.RelativePath)" }
        throw "安装包文件路径仍然过深，默认安装到 '$ProjectedInstallRoot' 时可能失败。`n$($examples -join "`n")"
    }
}

$projectRoot = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $projectRoot

if (-not $PythonExe) {
    $venvPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (Test-Path $venvPython) {
        $PythonExe = $venvPython
    } else {
        $PythonExe = 'python'
    }
}

$buildDir = Join-Path $projectRoot 'build'
$distDir = Join-Path $projectRoot 'dist'
$releaseDir = Join-Path $projectRoot 'release'
$bundledRuntimeDir = Join-Path $buildDir 'wewe-rss-runtime'
$pyInstallerBuildDir = Join-Path $buildDir 'GZHReader'
$installerSourceDir = Join-Path ([System.IO.Path]::GetPathRoot($projectRoot)) 'gzhsrc'
$specPath = Join-Path $projectRoot 'packaging\pyinstaller\GZHReader.spec'
$issPath = Join-Path $projectRoot 'packaging\inno\GZHReader.iss'
$iconPath = Join-Path $projectRoot 'packaging\assets\gzhreader.ico'
$wizardSidebarPath = Join-Path $projectRoot 'packaging\assets\wizard-sidebar.bmp'
$wizardSmallPath = Join-Path $projectRoot 'packaging\assets\wizard-small.bmp'
$buildWeweRssScript = Join-Path $projectRoot 'scripts\build_wewe_rss.ps1'
$runPyInstallerScript = Join-Path $projectRoot 'scripts\run_pyinstaller.py'
$appVersion = (& $PythonExe -c "from pathlib import Path; namespace = {}; exec(Path('src/gzhreader/__init__.py').read_text(encoding='utf-8'), namespace); print(namespace['__version__'])").Trim()

Stop-LocalBuildProcesses -TargetRoot $distDir
Start-Sleep -Milliseconds 500

if ($SkipBundledRSSBuild) {
    $cleanupTargets = @($pyInstallerBuildDir, $distDir, $releaseDir)
} else {
    $cleanupTargets = @($buildDir, $distDir, $releaseDir)
}

foreach ($dir in $cleanupTargets) {
    if (Test-Path $dir) {
        Remove-PathRobust -LiteralPath $dir
    }
}
New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null

if (-not (Test-Path $iconPath)) {
    throw 'Icon file is missing: packaging\assets\gzhreader.ico'
}
if (-not (Test-Path $wizardSidebarPath)) {
    throw 'Wizard sidebar image is missing: packaging\assets\wizard-sidebar.bmp'
}
if (-not (Test-Path $wizardSmallPath)) {
    throw 'Wizard small image is missing: packaging\assets\wizard-small.bmp'
}
if (-not (Test-Path $buildWeweRssScript)) {
    throw 'wewe-rss build script is missing: scripts\build_wewe_rss.ps1'
}
if (-not (Test-Path $runPyInstallerScript)) {
    throw 'PyInstaller runner is missing: scripts\run_pyinstaller.py'
}

& $PythonExe -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('PyInstaller') else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host 'PyInstaller 未安装，正在安装...'
    & $PythonExe -m pip install pyinstaller
    if ($LASTEXITCODE -ne 0) {
        throw 'PyInstaller 安装失败。'
    }
}

if ($SkipBundledRSSBuild) {
    Write-Host "已跳过 bundled wewe-rss 重建，复用现有运行时：$bundledRuntimeDir"
    $bundledRuntimeEntries = @(
        (Join-Path $bundledRuntimeDir 'apps\server\dist\main.js'),
        (Join-Path $bundledRuntimeDir 'dist\main.js')
    )
    if (-not ($bundledRuntimeEntries | Where-Object { Test-Path $_ } | Select-Object -First 1)) {
        throw '已选择 -SkipBundledRSSBuild，但现有 bundled wewe-rss 运行时不完整，请先运行 scripts\build_wewe_rss.ps1。'
    }
} else {
    Write-Host '开始构建 bundled wewe-rss 运行时...'
    $buildWeweRssArgs = @(
        '-ExecutionPolicy', 'Bypass',
        '-File', $buildWeweRssScript
    )
    if ($NodeExe) {
        $buildWeweRssArgs += @('-NodeExe', $NodeExe)
    }
    if ($PnpmExe) {
        $buildWeweRssArgs += @('-PnpmExe', $PnpmExe)
    }
    & powershell.exe @buildWeweRssArgs
    if ($LASTEXITCODE -ne 0) {
        throw 'bundled wewe-rss 构建失败。'
    }
}

Write-Host '开始构建 PyInstaller 产物...'
& $PythonExe $runPyInstallerScript --clean --noconfirm $specPath
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller 构建失败。'
}

$distAppDir = Join-Path $distDir 'GZHReader'
if (-not (Test-Path (Join-Path $distAppDir 'GZHReader.exe'))) {
    throw '资源检查失败：未生成 GZHReader.exe'
}

$packagedRuntimeInternalDir = Join-Path $distAppDir '_internal\r'
$packagedRuntimeTopLevelDir = Join-Path $distAppDir 'r'
if ((Test-Path $packagedRuntimeInternalDir) -and -not (Test-Path $packagedRuntimeTopLevelDir)) {
    Move-Item -LiteralPath $packagedRuntimeInternalDir -Destination $packagedRuntimeTopLevelDir
}
if (-not (Test-Path (Join-Path $distAppDir '_internal\scripts\register_task.ps1'))) {
    throw '资源检查失败：register_task.ps1 未被打入产物'
}
if (-not (Test-Path (Join-Path $distAppDir '_internal\gzhreader\templates'))) {
    throw '资源检查失败：templates 未被打入产物'
}
$packagedRuntimeEntries = @(
    (Join-Path $distAppDir 'r\apps\server\dist\main.js'),
    (Join-Path $distAppDir 'r\dist\main.js'),
    (Join-Path $distAppDir '_internal\r\apps\server\dist\main.js'),
    (Join-Path $distAppDir '_internal\r\dist\main.js')
)
if (-not ($packagedRuntimeEntries | Where-Object { Test-Path $_ } | Select-Object -First 1)) {
    throw '资源检查失败：bundled wewe-rss 运行时未被打入产物'
}

Assert-ProjectedInstallPathLengthsSafe -SourceRoot $distAppDir -ProjectedInstallRoot 'C:\Program Files\GZHReader'

if ($SkipInstaller) {
    Write-Host "已跳过 Inno Setup，PyInstaller 产物位于：$distAppDir"
    exit 0
}

$iscc = Resolve-IsccPath -PreferredPath $InnoSetupExe
if (-not $iscc) {
    throw '未找到可用的 Inno Setup 编译器 ISCC.exe，请先安装 Inno Setup 6.7.1 稳定版，或用 -InnoSetupExe 显式指定路径。'
}

Write-Host '开始生成安装包...'
Write-Host "使用 Inno Setup：$iscc"
if ($iscc -like '*Inno Setup 7*') {
    Write-Warning '当前检测到的是 Inno Setup 7 路径。若为 7.0.0 preview 版，卸载器可能报 “PathRedir: Not initialized”。除非你确认是稳定正式版，否则请优先改用 Inno Setup 6.7.1。'
}
Write-Host "应用版本：$appVersion"
if (Test-Path $installerSourceDir) {
    Remove-PathRobust -LiteralPath $installerSourceDir
}
New-Item -ItemType Directory -Path $installerSourceDir -Force | Out-Null
robocopy $distAppDir $installerSourceDir /E | Out-Null
if ($LASTEXITCODE -gt 7) {
    throw '无法为 Inno Setup 准备临时源目录。'
}

try {
    & $iscc "/DSourceDir=$installerSourceDir" "/DReleaseDir=$releaseDir" "/DMyAppVersion=$appVersion" $issPath
    if ($LASTEXITCODE -ne 0) {
        throw 'Inno Setup 构建失败。'
    }
}
finally {
    if (Test-Path $installerSourceDir) {
        Remove-PathRobust -LiteralPath $installerSourceDir
    }
}

Write-Host "构建完成，安装包输出目录：$releaseDir"
