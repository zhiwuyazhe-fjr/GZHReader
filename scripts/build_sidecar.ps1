$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    $PythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $PythonCommand) { throw "未找到 Python，请先安装构建依赖。" }
    $Python = $PythonCommand.Source
}

# The bundled sidecar is named for the Windows x64 target below.
& $Python -c "import sysconfig; raise SystemExit(0 if sysconfig.get_platform() == 'win-amd64' else 1)"
if ($LASTEXITCODE -ne 0) { throw "Sidecar 构建需要 Windows x64 Python。" }

$Dist = Join-Path $RepoRoot "build\sidecar-dist"
$Work = Join-Path $RepoRoot "build\sidecar-work"
$Spec = Join-Path $RepoRoot "build\sidecar-spec"
$BinaryDir = Join-Path $RepoRoot "desktop\src-tauri\binaries"
New-Item -ItemType Directory -Force -Path $Dist, $Work, $Spec, $BinaryDir | Out-Null

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --console `
    --name "gzhreader-core" `
    --paths (Join-Path $RepoRoot "src") `
    --collect-all readability `
    --collect-all chardet `
    --collect-all tzdata `
    --hidden-import playwright.sync_api `
    --distpath $Dist `
    --workpath $Work `
    --specpath $Spec `
    (Join-Path $RepoRoot "src\gzhreader_core\sidecar.py")

if ($LASTEXITCODE -ne 0) { throw "Python Sidecar 构建失败。" }
$Source = Join-Path $Dist "gzhreader-core.exe"
$Target = Join-Path $BinaryDir "gzhreader-core-x86_64-pc-windows-msvc.exe"
Copy-Item -LiteralPath $Source -Destination $Target -Force
Write-Host "Sidecar 已生成：$Target"
