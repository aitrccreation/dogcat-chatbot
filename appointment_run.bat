@echo off
:: Appointment workflow
::   queue   → build Send_Queue (13:00)
::   send    → send LINE reminders (18:00)
::   drx     → sync DRX data (12:30 / 20:15)
::   catchup → build queue + send (20:30) — รับนัดที่หมอจองหลัง 18:00
::             สำหรับวันพรุ่งนี้ ซึ่งรอบ 18:00 ไม่ทัน และวันนัดจริงจะเป็น T+0
::             ที่ sender ข้าม → ถ้าไม่มีรอบนี้ ลูกค้าจะไม่ได้รับเตือนเลย
::   both    → full sync (manual)
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
cd /d "E:\AI Dashboard"

set PYEXE=C:\Users\usEr\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe
set LOGFILE=E:\AI Dashboard\appointment_log.txt

set MODE=%1
if "%MODE%"=="" set MODE=both

:: Separate log per mode: DRX sync holds appointment_log.txt open ~30min,
:: so Queue@13:00 must write to a different file to avoid file-lock crash.
if "%MODE%"=="queue"   set LOGFILE=E:\AI Dashboard\queue_log.txt
if "%MODE%"=="send"    set LOGFILE=E:\AI Dashboard\send_log.txt
if "%MODE%"=="catchup" set LOGFILE=E:\AI Dashboard\send_log.txt

echo [%DATE% %TIME%] ===== START (mode=%MODE%) ===== >> "%LOGFILE%"

if "%MODE%"=="queue"   goto :queue
if "%MODE%"=="send"    goto :send
if "%MODE%"=="drx"     goto :drx
if "%MODE%"=="catchup" goto :catchup
if "%MODE%"=="both"    goto :both
goto :done

:drx
echo [%DATE% %TIME%] DRX sync (fetch fresh) ... >> "%LOGFILE%"
"%PYEXE%" appointment_sync.py --drx-only >> "%LOGFILE%" 2>&1
goto :done

:queue
echo [%DATE% %TIME%] Build Send_Queue (T+2) ... >> "%LOGFILE%"
"%PYEXE%" appointment_sync.py --queue-only >> "%LOGFILE%" 2>&1
goto :done

:send
echo [%DATE% %TIME%] Send LINE reminders ... >> "%LOGFILE%"
"%PYEXE%" appointment_sender.py >> "%LOGFILE%" 2>&1
goto :done

:catchup
echo [%DATE% %TIME%] Catch-up: rebuild queue + send (นัดที่จองหลัง 18:00) ... >> "%LOGFILE%"
"%PYEXE%" appointment_sync.py --queue-only >> "%LOGFILE%" 2>&1
"%PYEXE%" appointment_sender.py >> "%LOGFILE%" 2>&1
goto :done

:both
echo [%DATE% %TIME%] Full sync (DRX + Queue) ... >> "%LOGFILE%"
"%PYEXE%" appointment_sync.py --fetch >> "%LOGFILE%" 2>&1

:done
echo [%DATE% %TIME%] ===== DONE ===== >> "%LOGFILE%"
