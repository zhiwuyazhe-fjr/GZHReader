param(
    [switch]$SkipInstall,
    [switch]$SkipSidecar
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Desktop = Join-Path $RepoRoot "desktop"

& (Join-Path $PSScriptRoot "build_validate_versions.ps1")

$CargoBin = Join-Path $env:USERPROFILE ".cargo\bin"
if ((Test-Path -LiteralPath $CargoBin) -and ($env:PATH -notlike "*$CargoBin*")) {
    $env:PATH = "$CargoBin;$env:PATH"
}
if (-not (Get-Command link.exe -ErrorAction SilentlyContinue)) {
    $VsDevCmd = $null
    $VsWhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path -LiteralPath $VsWhere) {
        $VsDevCmdPaths = @(& $VsWhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -find "Common7\Tools\VsDevCmd.bat")
        if ($LASTEXITCODE -ne 0) { throw "Visual Studio 安装检测失败。" }
        if ($VsDevCmdPaths.Count -gt 0) { $VsDevCmd = $VsDevCmdPaths[0] }
    }
    if (-not $VsDevCmd) {
        $VsDevCmd = "D:\tools\VSBuildTools2022\Common7\Tools\VsDevCmd.bat"
    }
    if (-not (Test-Path -LiteralPath $VsDevCmd)) {
        throw "未找到带 MSVC C++ 工具链的 Visual Studio。请安装 Build Tools 或从 Developer PowerShell 运行。"
    }
    $EnvironmentLines = & cmd.exe /d /s /c "`"$VsDevCmd`" -arch=x64 -host_arch=x64 >nul && set"
    if ($LASTEXITCODE -ne 0) { throw "Visual Studio 构建环境加载失败。" }
    $ImportedVariables = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($Line in $EnvironmentLines) {
        if ($Line -match '^([^=]+)=(.*)$' -and $ImportedVariables.Add($Matches[1])) {
            [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
        }
    }
    if (-not (Get-Command link.exe -ErrorAction SilentlyContinue)) {
        throw "Visual Studio 环境加载后仍未找到 link.exe。"
    }
}

if (-not $SkipSidecar) {
    & (Join-Path $PSScriptRoot "build_sidecar.ps1")
}

Push-Location $Desktop
$OriginalSigningKey = $env:TAURI_SIGNING_PRIVATE_KEY
$OriginalSigningPassword = $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD
try {
    if (-not $SkipInstall) {
        npm ci
        if ($LASTEXITCODE -ne 0) { throw "桌面依赖安装失败。" }
    }
    if (-not $env:TAURI_SIGNING_PRIVATE_KEY) {
        if ($env:TAURI_SIGNING_PRIVATE_KEY_PATH) {
            if (-not (Test-Path -LiteralPath $env:TAURI_SIGNING_PRIVATE_KEY_PATH -PathType Leaf)) {
                throw "TAURI_SIGNING_PRIVATE_KEY_PATH 指向的签名文件不存在。"
            }
            # `tauri build` accepts a path through TAURI_SIGNING_PRIVATE_KEY.
            $env:TAURI_SIGNING_PRIVATE_KEY = (Resolve-Path -LiteralPath $env:TAURI_SIGNING_PRIVATE_KEY_PATH).Path
        } else {
            $DefaultKey = Join-Path $env:USERPROFILE ".tauri\gzhreader-updater.key"
            if (-not (Test-Path -LiteralPath $DefaultKey -PathType Leaf)) {
                throw "未找到更新签名私钥。请设置 TAURI_SIGNING_PRIVATE_KEY 或 TAURI_SIGNING_PRIVATE_KEY_PATH。"
            }
            $PasswordFile = Join-Path $env:USERPROFILE ".tauri\gzhreader-updater.password"
            if (-not (Test-Path -LiteralPath $PasswordFile)) { throw "未找到更新签名密码文件。" }
            $env:TAURI_SIGNING_PRIVATE_KEY = $DefaultKey
            $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = (Get-Content -LiteralPath $PasswordFile -Raw).Trim()
        }
    }
    npm run desktop:build
    if ($LASTEXITCODE -ne 0) { throw "Tauri 桌面安装包构建失败。" }
} finally {
    $env:TAURI_SIGNING_PRIVATE_KEY = $OriginalSigningKey
    $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = $OriginalSigningPassword
    Pop-Location
}
