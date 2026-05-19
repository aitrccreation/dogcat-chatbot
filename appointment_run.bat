@echo off
:: Appointment workflow — sync DRX + send reminders
:: รัน 2 รอบ/วัน: 06:00 (sync only), 18:00 (sync + send)
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
cd /d "D:\AI Dashboard"

set PYEXE=C:\Users\aitrc\AppData\Local\Python\bin\python.exe
set LOGFILE=D:\AI Dashboard\appointment_log.txt

set MODE=%1
if "%MODE%"=="" set MODE=both

echo [%DATE% %TIME%] ===== START (mode=%MODE%) ===== >> "%LOGFILE%"

if "%MODE%"=="sync" goto :sync
if "%MODE%"=="send" goto :send
if "%MODE%"=="both" goto :sync

:sync
echo [%DATE% %TIME%] Syncing DRX to Excel... >> "%LOGFILE%"
"%PYEXE%" appointment_sync.py --fetch >> "%LOGFILE%" 2>&1
if "%MODE%"=="sync" goto :done

:send
echo [%DATE% %TIME%] Sending appointment reminders... >> "%LOGFILE%"
"%PYEXE%" appointment_sender.py >> "%LOGFILE%" 2>&1

:done
echo [%DATE% %TIME%] ===== DONE ===== >> "%LOGFILE%"
