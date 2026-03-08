param(
    [string]$TaskName = "GZHReaderDaily",
    [Parameter(Mandatory = $true)]
    [string]$CommandExe,
    [Parameter(Mandatory = $true)]
    [string]$CommandArgs,
    [string]$WorkingDirectory = (Split-Path -Parent $CommandExe),
    [string]$RunTime = "21:30"
)

$action = New-ScheduledTaskAction -Execute $CommandExe -Argument $CommandArgs -WorkingDirectory $WorkingDirectory
$trigger = New-ScheduledTaskTrigger -Daily -At $RunTime

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Force | Out-Null
Write-Output "Installed scheduled task: $TaskName at $RunTime"
