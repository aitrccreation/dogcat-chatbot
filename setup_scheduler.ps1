# DogCatLovely - Task Scheduler Setup (Run as Administrator!)
# ============================================================
param()

# ตรวจ admin
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "[ERROR] Please run as Administrator!" -ForegroundColor Red
    Write-Host "Right-click this file -> Run with PowerShell (as Administrator)" -ForegroundColor Yellow
    pause
    exit 1
}

$BAT   = "D:\AI Dashboard\send_daily_summary.bat"
$USER  = "TARN\aitrc"

$times = @{ "DogCatLovely_LINE_1000"="10:00"; "DogCatLovely_LINE_1300"="13:00"; "DogCatLovely_LINE_1500"="15:00"; "DogCatLovely_LINE_1800"="18:00"; "DogCatLovely_LINE_2020"="20:20" }

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Dog and Cat Lovely Task Scheduler Setup" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Bat file: $BAT"
Write-Host "Run as:   $USER"
Write-Host ""

# Action: cmd.exe /c "bat_file_path" — handles spaces in path
$action   = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$BAT`"" -WorkingDirectory "D:\AI Dashboard"
$principal = New-ScheduledTaskPrincipal -UserId $USER -LogonType Interactive -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -WakeToRun

# เปิด Wake Timer ใน Power Plan ปัจจุบัน (จำเป็นสำหรับ WakeToRun)
powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP BD3B718A-0680-4D9D-8AB2-E1D2B4AC806D 1 | Out-Null
powercfg /setdcvalueindex SCHEME_CURRENT SUB_SLEEP BD3B718A-0680-4D9D-8AB2-E1D2B4AC806D 1 | Out-Null
powercfg /setactive SCHEME_CURRENT
Write-Host "[OK] Wake Timer enabled (AC + Battery)" -ForegroundColor Green

foreach ($entry in $times.GetEnumerator()) {
    $tn      = $entry.Key
    $time    = $entry.Value
    $trigger = New-ScheduledTaskTrigger -Daily -At $time

    # Remove old task
    Unregister-ScheduledTask -TaskName $tn -Confirm:$false -ErrorAction SilentlyContinue

    # Create new task
    $result = Register-ScheduledTask -TaskName $tn -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force -ErrorAction SilentlyContinue
    if ($result) {
        Write-Host "  [OK] $tn @ $time" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] $tn" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "Tasks created. Verify:" -ForegroundColor Cyan
schtasks /query /tn "DogCatLovely*" /fo TABLE 2>$null
Write-Host ""
Write-Host "Test run: schtasks /run /tn DogCatLovely_LINE_1000"
Write-Host "View log: Get-Content 'D:\AI Dashboard\scheduler_log.txt'"
Write-Host ""
pause