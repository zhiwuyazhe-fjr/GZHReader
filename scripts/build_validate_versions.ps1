param(
    [string]$Tag
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Read-VersionMatch {
    param([string]$RelativePath, [string]$Pattern)

    $Content = Get-Content -LiteralPath (Join-Path $RepoRoot $RelativePath) -Raw
    $Match = [regex]::Match($Content, $Pattern)
    if (-not $Match.Success) { throw "无法读取版本：$RelativePath" }
    return $Match.Groups[1].Value
}

$TauriConfig = Get-Content -LiteralPath (Join-Path $RepoRoot "desktop\src-tauri\tauri.conf.json") -Raw | ConvertFrom-Json
$DesktopPackage = Get-Content -LiteralPath (Join-Path $RepoRoot "desktop\package.json") -Raw | ConvertFrom-Json
$Versions = [ordered]@{
    "tauri.conf.json" = $TauriConfig.version
    "package.json" = $DesktopPackage.version
    "Cargo.toml" = Read-VersionMatch "desktop\src-tauri\Cargo.toml" '(?ms)^\[package\]\s+.*?^version\s*=\s*"([^"]+)"'
    "Python Core" = Read-VersionMatch "src\gzhreader_core\__init__.py" '(?m)^__version__\s*=\s*["'']([^"'']+)["'']'
}

$Version = [string]$TauriConfig.version
foreach ($Entry in $Versions.GetEnumerator()) {
    if ([string]::IsNullOrWhiteSpace($Entry.Value) -or $Entry.Value -cne $Version) {
        throw "版本不一致：$($Entry.Key)=$($Entry.Value)，tauri.conf.json=$Version。"
    }
}
if ($Tag -and $Tag -cne "v$Version") {
    throw "发布标签 $Tag 与应用版本 v$Version 不一致。"
}
Write-Host "版本检查通过：$Version"
