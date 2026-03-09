param(
    [string]$PythonExe = "",
    [string]$InnoSetupExe = "",
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

function Resolve-IsccPath {
    param([string]$PreferredPath = "")

    if ($PreferredPath) {
        if (Test-Path $PreferredPath) {
            return (Resolve-Path $PreferredPath).Path
        }
        throw "指定的 Inno Setup 编译器不存在：$PreferredPath"
    }

    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source
    if ($command) {
        return $command
    }

    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
        'C:\Program Files (x86)\Inno Setup 6\ISCC.exe',
        'C:\Program Files\Inno Setup 6\ISCC.exe'
    ) | Where-Object { $_ }

    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) {
            return (Resolve-Path $candidate).Path
        }
    }

    $registryRoots = @(
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'
    )

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
                        return (Resolve-Path $candidate).Path
                    }
                } else {
                    $candidateExe = Join-Path $candidate 'ISCC.exe'
                    if (Test-Path $candidateExe) {
                        return (Resolve-Path $candidateExe).Path
                    }
                }
            }
        }
    }

    return $null
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
$specPath = Join-Path $projectRoot 'packaging\pyinstaller\GZHReader.spec'
$issPath = Join-Path $projectRoot 'packaging\inno\GZHReader.iss'
$iconPath = Join-Path $projectRoot 'packaging\assets\gzhreader.ico'
$wizardSidebarPath = Join-Path $projectRoot 'packaging\assets\wizard-sidebar.bmp'
$wizardSmallPath = Join-Path $projectRoot 'packaging\assets\wizard-small.bmp'
$appVersion = (& $PythonExe -c "from pathlib import Path; namespace = {}; exec(Path('src/gzhreader/__init__.py').read_text(encoding='utf-8'), namespace); print(namespace['__version__'])").Trim()

Stop-LocalBuildProcesses -TargetRoot $distDir
Start-Sleep -Milliseconds 500

foreach ($dir in @($buildDir, $distDir, $releaseDir)) {
    if (Test-Path $dir) {
        Remove-Item $dir -Recurse -Force
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

& $PythonExe -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('PyInstaller') else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host 'PyInstaller 未安装，正在安装...'
    & $PythonExe -m pip install pyinstaller
    if ($LASTEXITCODE -ne 0) {
        throw 'PyInstaller 安装失败。'
    }
}

Write-Host '开始构建 PyInstaller 产物...'
& $PythonExe -m PyInstaller --clean --noconfirm $specPath
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
Write-Host "应用版本：$appVersion"
& $iscc "/DSourceDir=$distAppDir" "/DReleaseDir=$releaseDir" "/DMyAppVersion=$appVersion" $issPath
if ($LASTEXITCODE -ne 0) {
    throw 'Inno Setup 构建失败。'
}

Write-Host "构建完成，安装包输出目录：$releaseDir"
