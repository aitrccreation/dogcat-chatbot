# Chatbot Health Monitor
# Runs every 30 minutes via Task Scheduler
# Checks:
#   1. Chatbot port 5000 responds
#   2. APScheduler fired within expected window (no >25h gap)
# If unhealthy -> restart via Task Scheduler

$ErrorActionPreference = "Continue"
$logDir = "E:\AI Dashboard"
$errLog = "$logDir\chatbot_err.log"
$healthLog = "$logDir\health_check.log"
$now = Get-Date
$timestamp = $now.ToString("yyyy-MM-dd HH:mm:ss")

function Write-Health($msg) {
    Add-Content -Path $healthLog -Value "[$timestamp] $msg" -Encoding UTF8
}

# 1. Check port 5000 responsive
$portAlive = $false
try {
    $r = Invoke-WebRequest -Uri "http://localhost:5000/" -TimeoutSec 5 -UseBasicParsing
    if ($r.StatusCode -eq 200) { $portAlive = $true }
} catch {}

# 2. Check last APScheduler fire from chatbot_err.log
$lastFire = $null
if (Test-Path $errLog) {
    $lines = Get-Content $errLog -Tail 500 -ErrorAction SilentlyContinue
    foreach ($line in $lines) {
        if ($line -match '^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*Daily summary sent successfully') {
            try {
                $lastFire = [datetime]::ParseExact($matches[1], "yyyy-MM-dd HH:mm:ss", $null)
            } catch {}
        }
    }
}

# 3. Decide if restart needed
$needRestart = $false
$reason = ""

# Proactive window: 11:30-12:29 (covers the 11:30 AND 12:00 health checks)
# APScheduler freezes overnight (gap 20:20 -> next 13:00 is "normal" so undetectable
# until 13:00 is already missed). Fix: restart proactively before the first fire (13:00).
# Why TWO ticks: on 6/8 the 12:00 tick was skipped by Windows Task Scheduler, so a
# single-tick (12:00 only) window missed it and 13:00 was lost. 11:30+12:00 = redundancy.
# Window ends at 12:30 to avoid colliding with the 12:30 DRX-sync task (its python3.13
# would be killed by the restart's taskkill). A marker file ensures only ONE proactive
# restart per day even if both ticks fire.
$markerFile = "$logDir\last_restart_date.txt"
$todayStr   = $now.ToString("yyyy-MM-dd")
$restartedToday = (Test-Path $markerFile) -and ((Get-Content $markerFile -Raw -ErrorAction SilentlyContinue).Trim() -eq $todayStr)
$inProactiveWindow = ($now.Hour -eq 11 -and $now.Minute -ge 30) -or ($now.Hour -eq 12 -and $now.Minute -lt 30)

if (-not $portAlive) {
    $needRestart = $true
    $reason = "port 5000 not responding"
}
elseif ($inProactiveWindow -and -not $restartedToday -and (-not $lastFire -or $lastFire.Date -lt $now.Date)) {
    # Scheduler hasn't fired today yet and we're in the pre-13:00 window -> refresh it
    $needRestart = $true
    $reason = "proactive pre-13:00 restart (scheduler stale since $(if ($lastFire) { $lastFire.ToString('MM-dd HH:mm') } else { 'never' }))"
}
elseif ($lastFire) {
    # Check 1: if past 13:30 today AND last fire was from a previous day -> 13:00 missed
    $today1330 = $now.Date.AddHours(13).AddMinutes(30)
    if ($now -gt $today1330 -and $lastFire.Date -lt $now.Date) {
        $needRestart = $true
        $reason = "13:00 missed (last fire: $($lastFire.ToString('yyyy-MM-dd HH:mm')))"
    } else {
        # Check 2: during active hours (13-21), no fire in 4h = stuck
        $hour = $now.Hour
        $hoursSinceFire = ($now - $lastFire).TotalHours
        if ($hour -ge 13 -and $hour -le 21 -and $hoursSinceFire -gt 4) {
            $needRestart = $true
            $reason = "no fire in $([math]::Round($hoursSinceFire,1))h during active hours"
        }
    }
}

# -- Appointment-job guard --
# start_chatbot.bat runs taskkill /F /IM python3.13.exe which kills ALL python3.13
# processes, INCLUDING a running appointment job (they are python3.13 too). On 6/8 the
# 13:00:02 restart killed the 13:00:01 queue-build mid-run, so the 6/10 T+2 appts never
# got queued. Defer non-critical restarts during appointment-job windows:
#   12:30 DRX sync | 13:00 queue build | 18:00 send | 20:15 DRX sync
# (port-dead is critical so restart anyway; the proactive 11:30 restart keeps the
#  scheduler fresh so a 13:00 restart should not be needed in the first place.)
# NOTE: keep this file ASCII-only — PowerShell 5.1 mangles UTF-8 in strings (no BOM).
$h = $now.Hour; $m = $now.Minute
$inApptWindow = ($h -eq 12 -and $m -ge 28) -or ($h -eq 13 -and $m -le 5) -or ($h -eq 18 -and $m -le 5) -or ($h -eq 20 -and $m -ge 13 -and $m -le 18)
if ($needRestart -and $inApptWindow -and $portAlive) {
    Write-Health "DEFER restart ($reason) -- appointment job window, wont risk killing it"
    $needRestart = $false
}

if ($needRestart) {
    Write-Health "UNHEALTHY -> restart: $reason"
    try {
        # Just trigger the task — start_chatbot.bat already runs `taskkill python3.13`
        # at its start, so it kills the old (stuck) process itself. We do NOT call
        # Stop-ScheduledTask first: on Session-0 tasks it can block indefinitely,
        # which killed this script mid-restart on 6/5 (detected but never triggered).
        # The bat exits quickly (launches via Start-Process), so the task returns to
        # "Ready" and schtasks /RUN is accepted on the next run.
        & schtasks /RUN /TN "DogCatLovely Chatbot" 2>&1 | Out-Null
        # บันทึก marker เฉพาะ proactive restart → กัน double-restart ในหน้าต่างเดียวกัน
        if ($reason -like "proactive*") {
            Set-Content -Path $markerFile -Value $todayStr -Encoding UTF8
        }
        Write-Health "Restart triggered (schtasks /RUN)"
    } catch {
        Write-Health "Restart failed: $_"
    }
} else {
    $fireStr = if ($lastFire) { $lastFire.ToString("yyyy-MM-dd HH:mm") } else { "N/A" }
    Write-Health "OK (port: $portAlive, last fire: $fireStr)"
}
