# Setup Task Scheduler for appointment workflow
#   06:00 — sync DRX → Excel
#   18:00 — sync + send reminders
# Run as Administrator!

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "[ERROR] Please run as Administrator!" -ForegroundColor Red
    pause
    exit 1
}

$BAT  = "D:\AI Dashboard\appointment_run.bat"
$USER = "TARN\aitrc"

$tasks = @{
    "DogCatLovely_APPT_Sync_0600" = @{ time = "06:00"; arg = "sync" }
    "DogCatLovely_APPT_Send_1800" = @{ time = "18:00"; arg = "send" }
}

$principal = New-ScheduledTaskPrincipal -UserId $USER -LogonType S4U -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -WakeToRun

foreach ($entry in $tasks.GetEnumerator()) {
    $tn   = $entry.Key
    $t    = $entry.Value
    $tm   = $t.time
    $arg  = $t.arg

    $action  = New-ScheduledTaskAction -Execute "cmd.exe" `
                -Argument "/c `"$BAT`" $arg" `
                -WorkingDirectory "D:\AI Dashboard"
    $trigger = New-ScheduledTaskTrigger -Daily -At $tm

    Unregister-ScheduledTask -TaskName $tn -Confirm:$false -ErrorAction SilentlyContinue
    try {
        Register-ScheduledTask -TaskName $tn -Action $action -Trigger $trigger `
            -Principal $principal -Settings $settings -Force -ErrorAction Stop | Out-Null
        Write-Host "  [OK] $tn @ $tm  (arg=$arg)" -ForegroundColor Green
    } catch {
        Write-Host "  [FAIL] $tn : $_" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "=== Current appointment tasks ===" -ForegroundColor Cyan
Get-ScheduledTask -TaskName "DogCatLovely_APPT*" | Select-Object TaskName, @{N='LogonType';E={$_.Principal.LogonType}}, @{N='NextRun';E={(Get-ScheduledTaskInfo $_).NextRunTime}} | Format-Table -AutoSize
pause
