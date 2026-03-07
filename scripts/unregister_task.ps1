param(
    [string]$TaskName = "GZHReaderDaily"
)

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Output "Removed scheduled task: $TaskName"
