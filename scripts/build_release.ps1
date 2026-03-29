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

    # Inno Setup 7 removes MAX_PATH limits; bundled wewe-rss uses deep pnpm paths and
    # IS 6 can fail at install-time extraction with "找不到路径" / path not found.
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 7\ISCC.exe'),
        'C:\Program Files\Inno Setup 7\ISCC.exe',
        'C:\Program Files (x86)\Inno Setup 7\ISCC.exe',
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
        'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
        'C:\Program Files\Inno Setup 6\ISCC.exe'
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

    $prefer7 = $registryIscc | Where-Object { $_ -like '*Inno Setup 7*' } | Select-Object -First 1
    if ($prefer7) {
        return $prefer7
    }
    return $registryIscc | Select-Object -First 1
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
if (-not (Test-Path (Join-Path $distAppDir 'GZHReader Console.exe'))) {
    throw '资源检查失败：未生成 GZHReader Console.exe'
}
if (-not (Test-Path (Join-Path $distAppDir '_internal\scripts\register_task.ps1'))) {
    throw '资源检查失败：register_task.ps1 未被打入产物'
}
if (-not (Test-Path (Join-Path $distAppDir '_internal\gzhreader\templates'))) {
    throw '资源检查失败：templates 未被打入产物'
}
$packagedRuntimeEntries = @(
    (Join-Path $distAppDir '_internal\r\apps\server\dist\main.js'),
    (Join-Path $distAppDir '_internal\r\dist\main.js')
)
if (-not ($packagedRuntimeEntries | Where-Object { Test-Path $_ } | Select-Object -First 1)) {
    throw '资源检查失败：bundled wewe-rss 运行时未被打入产物'
}

if ($SkipInstaller) {
    Write-Host "已跳过 Inno Setup，PyInstaller 产物位于：$distAppDir"
    exit 0
}

$iscc = Resolve-IsccPath -PreferredPath $InnoSetupExe
if (-not $iscc) {
    throw '未找到 Inno Setup 编译器 ISCC.exe，请先安装 Inno Setup 6，或用 -InnoSetupExe 显式指定路径。'
}

Write-Host '开始生成安装包...'
Write-Host "使用 Inno Setup：$iscc"
if ($iscc -notlike '*Inno Setup 7*') {
    Write-Warning '当前使用的是 Inno Setup 6 或更早版本。打包产物含深层 node_modules 路径，旧版安装器可能在解压时出现「找不到路径」。请安装 Inno Setup 7 并重新编译安装包。'
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
