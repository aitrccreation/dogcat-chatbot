# เพิ่ม Task Scheduler รัน OPD sync ทุก 30 นาที (08:00-20:00)
# ดึงประวัติ OPD ทุก visit จาก DRX MySQL → drx_opd.db เพื่อให้ LINE OA ดูประวัติได้สด
# จบก่อน 20:20 เสมอ (เครื่องปิดทุกคืน 20:25-20:40)
# วิธีรัน: คลิกขวาที่ไฟล์ → Run with PowerShell (as Administrator)

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "[ERROR] โปรดรันด้วย Administrator" -ForegroundColor Red
    Write-Host "คลิกขวาที่ไฟล์ → Run with PowerShell (as Administrator)" -ForegroundColor Yellow
    pause
    exit 1
}

$BAT  = "E:\AI Dashboard\opd_sync_run.bat"
$USER = "IHC-BTKRX1720\usEr"
$TASK = "DogCatLovely_OPD_Sync"

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$BAT`"" -WorkingDirectory "E:\AI Dashboard"

# ทุก 30 นาที ตั้งแต่ 08:00 ยาว 12 ชม. → รอบสุดท้าย 20:00 (จบก่อนเครื่องปิด 20:25)
$trigger = New-ScheduledTaskTrigger -Daily -At "08:00"
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At "08:00" `
    -RepetitionInterval (New-TimeSpan -Minutes 30) `
    -RepetitionDuration (New-TimeSpan -Hours 12)).Repetition

$principal = New-ScheduledTaskPrincipal -UserId $USER -LogonType S4U -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

try {
    Unregister-ScheduledTask -TaskName $TASK -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $TASK -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Force -ErrorAction Stop | Out-Null
    Write-Host ""
    Write-Host "[OK] สร้าง task สำเร็จ:" -ForegroundColor Green
    Write-Host "  Name: $TASK"
    Write-Host "  Time: ทุก 30 นาที 08:00-20:00"
    Write-Host "  Cmd:  cmd.exe /c `"$BAT`""
    Write-Host "  Log:  E:\AI Dashboard\opd_sync_log.txt"
    Write-Host ""

    Get-ScheduledTask -TaskName "DogCatLovely*" |
        Select-Object TaskName, State, @{N='NextRun'; E={(Get-ScheduledTaskInfo $_).NextRunTime}} |
        Sort-Object NextRun |
        Format-Table -AutoSize
} catch {
    Write-Host "[FAIL] $_" -ForegroundColor Red
}

Write-Host ""
Write-Host "เสร็จเรียบร้อย กด Enter เพื่อปิด..."
pause
