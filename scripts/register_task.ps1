param(
    [string]$TaskName = "GZHReaderDaily",
    [string]$PythonExe = "python",
    [string]$ProjectDir = (Resolve-Path "$PSScriptRoot\.."),
    [string]$RunTime = "21:30",
    [string]$ConfigPath = "$((Resolve-Path "$PSScriptRoot\..").Path)\config.yaml"
)

$actionArgs = "-m gzhreader run today --config `"$ConfigPath`""
$action = New-ScheduledTaskAction -Execute $PythonExe -Argument $actionArgs -WorkingDirectory $ProjectDir
$trigger = New-ScheduledTaskTrigger -Daily -At $RunTime

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Force | Out-Null
Write-Output "Installed scheduled task: $TaskName at $RunTime"
