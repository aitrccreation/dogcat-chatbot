# Setup Task Scheduler for appointment workflow
#   12:30 — sync DRX (รอบเช้า) → ให้ queue build 13:00 ใช้ข้อมูลสด
#   13:00 — build Send_Queue (จาก DRX data ที่มี)
#   18:00 — send LINE reminders (ส่งล่วงหน้า 2 วัน ครั้งเดียว)
#   20:15 — sync DRX (รอบเย็น) → ให้พรุ่งนี้เช้ามีข้อมูลสำรอง
# Run as Administrator!

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "[ERROR] Please run as Administrator!" -ForegroundColor Red
    pause
    exit 1
}

$BAT  = "E:\AI Dashboard\appointment_run.bat"
$USER = "IHC-BTKRX1720\usEr"

$tasks = @{
    "DogCatLovely_APPT_DRX_1230"   = @{ time = "12:30"; arg = "drx"   }
    "DogCatLovely_APPT_Queue_1300" = @{ time = "13:00"; arg = "queue" }
    "DogCatLovely_APPT_Send_1800"  = @{ time = "18:00"; arg = "send"  }
    "DogCatLovely_APPT_DRX_2015"   = @{ time = "20:15"; arg = "drx"   }
}

$principal = New-ScheduledTaskPrincipal -UserId $USER -LogonType S4U -RunLevel Highest

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -WakeToRun

# ลบ task เก่า (ที่ไม่ใช้แล้ว)
Unregister-ScheduledTask -TaskName "DogCatLovely_APPT_Sync_0600" -Confirm:$false -ErrorAction SilentlyContinue

foreach ($entry in $tasks.GetEnumerator()) {
    $tn   = $entry.Key
    $t    = $entry.Value
    $tm   = $t.time
    $arg  = $t.arg

    $action  = New-ScheduledTaskAction -Execute "cmd.exe" `
                -Argument "/c `"$BAT`" $arg" `
                -WorkingDirectory "E:\AI Dashboard"
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
