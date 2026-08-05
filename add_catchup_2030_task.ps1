# เพิ่ม Task Scheduler รอบ catch-up 20:30
# (หลัง DRX sync 20:15 — รับนัดที่หมอจองหลัง sender รอบ 18:00 ไปแล้ว)
#
# ทำไมต้องมีรอบนี้:
#   หมอจองนัดตอนเย็นสำหรับ "พรุ่งนี้" → DRX sync 20:15 เพิ่งดึงมา แต่ sender
#   รอบ 18:00 ผ่านไปแล้ว พอถึงวันนัดจริงจะเป็น T+0 ซึ่ง sender ข้าม
#   (ส่งเฉพาะ T+1/T+2) → ลูกค้าไม่ได้รับเตือนเลย
#   รอบ 20:30 นี้ build queue ใหม่แล้วส่งทันที ตอนนั้นยังเป็น T+1 อยู่
#
# ส่งซ้ำไหม: ไม่ — sender เช็ค r1_at ก่อนส่ง นัดที่ส่งไปแล้วรอบ 18:00 จะถูกข้าม
#
# วิธีรัน: คลิกขวาที่ไฟล์ → Run with PowerShell (as Administrator)

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "[ERROR] โปรดรันด้วย Administrator" -ForegroundColor Red
    Write-Host "คลิกขวาที่ไฟล์ → Run with PowerShell (as Administrator)" -ForegroundColor Yellow
    pause
    exit 1
}

$BAT  = "E:\AI Dashboard\appointment_run.bat"
$USER = "IHC-BTKRX1720\usEr"
$TASK = "DogCatLovely_APPT_Catchup_2030"

$action    = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$BAT`" catchup" -WorkingDirectory "E:\AI Dashboard"
$trigger   = New-ScheduledTaskTrigger -Daily -At "20:30"
$principal = New-ScheduledTaskPrincipal -UserId $USER -LogonType S4U -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -WakeToRun

try {
    Unregister-ScheduledTask -TaskName $TASK -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $TASK -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Force -ErrorAction Stop | Out-Null
    Write-Host ""
    Write-Host "[OK] สร้าง task สำเร็จ:" -ForegroundColor Green
    Write-Host "  Name: $TASK"
    Write-Host "  Time: 20:30 ทุกวัน"
    Write-Host "  Cmd:  cmd.exe /c `"$BAT`" catchup"
    Write-Host ""

    Write-Host "=== Task รายชื่อนัด ทั้งหมด ===" -ForegroundColor Cyan
    Get-ScheduledTask -TaskName "DogCatLovely_APPT*" |
        Select-Object TaskName, @{N='NextRun'; E={(Get-ScheduledTaskInfo $_).NextRunTime}} |
        Sort-Object NextRun |
        Format-Table -AutoSize
} catch {
    Write-Host "[FAIL] $_" -ForegroundColor Red
}

Write-Host ""
Write-Host "เสร็จเรียบร้อย กด Enter เพื่อปิด..."
pause
