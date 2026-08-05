# เลื่อน DRX sync รอบเย็น 20:15 → 20:05
#
# เหตุผล: sync ใช้เวลา ~14 นาที เดิมเริ่ม 20:15 จบ 20:29 ซึ่งชนเวลาปิดเครื่อง
#         (จาก Event Log เครื่องปิดเร็วสุด 20:25:30 — วันที่ 1 และ 2 ส.ค. sync
#          ถูกตัดกลางคันจริง ไม่มี DONE ใน log)
#         เริ่ม 20:05 → จบราว 20:19 เหลือขอบเขตราว 6 นาที
#
# ลบ task เดิม DogCatLovely_APPT_DRX_2015 แล้วสร้าง DogCatLovely_APPT_DRX_2005 แทน
# (Task Scheduler เปลี่ยนชื่อ task ไม่ได้ ต้องลบแล้วสร้างใหม่ — ชื่อจะได้ตรงเวลาจริง)
#
# วิธีรัน: คลิกขวาที่ไฟล์ → Run with PowerShell (as Administrator)

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "[ERROR] โปรดรันด้วย Administrator" -ForegroundColor Red
    Write-Host "คลิกขวาที่ไฟล์ → Run with PowerShell (as Administrator)" -ForegroundColor Yellow
    pause
    exit 1
}

$BAT     = "E:\AI Dashboard\appointment_run.bat"
$USER    = "IHC-BTKRX1720\usEr"
$OLDTASK = "DogCatLovely_APPT_DRX_2015"
$NEWTASK = "DogCatLovely_APPT_DRX_2005"

$action    = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$BAT`" drx" -WorkingDirectory "E:\AI Dashboard"
$trigger   = New-ScheduledTaskTrigger -Daily -At "20:05"
$principal = New-ScheduledTaskPrincipal -UserId $USER -LogonType S4U -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -WakeToRun

try {
    Register-ScheduledTask -TaskName $NEWTASK -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Force -ErrorAction Stop | Out-Null
    Write-Host "[OK] สร้าง $NEWTASK (20:05 ทุกวัน)" -ForegroundColor Green

    if (Get-ScheduledTask -TaskName $OLDTASK -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $OLDTASK -Confirm:$false -ErrorAction Stop
        Write-Host "[OK] ลบ $OLDTASK ออกแล้ว" -ForegroundColor Green
    }
    Write-Host ""

    Write-Host "=== ตารางงานนัดหลังเปลี่ยน ===" -ForegroundColor Cyan
    Get-ScheduledTask -TaskName "DogCatLovely_APPT*" |
        Select-Object TaskName, State, @{N='NextRun'; E={(Get-ScheduledTaskInfo $_).NextRunTime}} |
        Sort-Object NextRun |
        Format-Table -AutoSize
} catch {
    Write-Host "[FAIL] $_" -ForegroundColor Red
    Write-Host "task เดิมยังอยู่ ไม่ได้ลบ" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "เสร็จเรียบร้อย กด Enter เพื่อปิด..."
pause
