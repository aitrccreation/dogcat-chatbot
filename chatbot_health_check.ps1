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

# Proactive window: 12:00-12:29 (the noon health check)
# APScheduler freezes overnight (gap 20:20 -> next 13:00 is "normal" so undetectable
# until 13:00 is already missed). Fix: restart proactively at 12:00 so the scheduler is
# fresh before the first fire (13:00). 12:00 is safe — no appointment job runs then
# (DRX sync 12:30, queue 13:00), so the restart's taskkill won't interrupt anything.
$proactiveStart = $now.Date.AddHours(12)                 # 12:00
$proactiveEnd   = $now.Date.AddHours(12).AddMinutes(30)  # 12:30

if (-not $portAlive) {
    $needRestart = $true
    $reason = "port 5000 not responding"
}
elseif ($now -ge $proactiveStart -and $now -lt $proactiveEnd -and (-not $lastFire -or $lastFire.Date -lt $now.Date)) {
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
        Write-Health "Restart triggered (schtasks /RUN)"
    } catch {
        Write-Health "Restart failed: $_"
    }
} else {
    $fireStr = if ($lastFire) { $lastFire.ToString("yyyy-MM-dd HH:mm") } else { "N/A" }
    Write-Health "OK (port: $portAlive, last fire: $fireStr)"
}
