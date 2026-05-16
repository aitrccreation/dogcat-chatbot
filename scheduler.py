"""
Daily Summary Scheduler
========================
Schedule fetch DRX → send LINE daily summary
Schedules: 10:00, 13:00, 15:00, 18:00, 20:20 (Asia/Bangkok)

Uses APScheduler in Background mode.
ส่งผ่าน Lovely Bot (LOVELY_BOT_TOKEN) ไปยัง Wirote (LINE_TARGET_ID)
"""
import os
import sys
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

# โหลด env vars
LOVELY_BOT_TOKEN = os.environ.get("LOVELY_BOT_TOKEN", "").strip()
LINE_TARGET_ID   = os.environ.get("LINE_TARGET_ID", "").strip()
TZ_NAME          = os.environ.get("TZ", "Asia/Bangkok")

# Schedule times — 5 ครั้ง/วัน
SCHEDULE_TIMES = [
    (10, 0),
    (13, 0),
    (15, 0),
    (18, 0),
    (20, 20),
]


def run_daily_summary():
    """ดึงข้อมูล DRX → สร้างข้อความ → ส่ง LINE Push"""
    now = datetime.now()
    log.info(f"[Scheduler] Daily summary triggered at {now.strftime('%H:%M:%S')}")

    if not LOVELY_BOT_TOKEN:
        log.error("[Scheduler] LOVELY_BOT_TOKEN not set — skip")
        return
    if not LINE_TARGET_ID:
        log.error("[Scheduler] LINE_TARGET_ID not set — skip")
        return

    try:
        # 1. ดึง DRX ใหม่
        import drx_bridge
        log.info("[Scheduler] Fetching fresh DRX data...")
        # drx_bridge.main() writes drx_data.json
        ok = drx_bridge.run_fetch()
        if not ok:
            log.warning("[Scheduler] DRX fetch failed — ใช้ข้อมูลเก่า")

        # 2. สร้างและส่งข้อความ
        import line_sender
        data = line_sender.load_data()
        msg  = line_sender.build_message(data)
        log.info(f"[Scheduler] Built message ({len(msg)} chars), sending to LINE...")
        line_sender.send_line_push(LOVELY_BOT_TOKEN, LINE_TARGET_ID, msg)
        log.info("[Scheduler] Daily summary sent successfully")
    except Exception as e:
        log.exception(f"[Scheduler] Error in daily summary: {e}")


def start_scheduler():
    """เริ่ม APScheduler background"""
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        import pytz
    except ImportError as e:
        log.error(f"[Scheduler] Missing dependencies: {e}")
        return None

    tz = pytz.timezone(TZ_NAME)
    scheduler = BackgroundScheduler(timezone=tz)

    for hh, mm in SCHEDULE_TIMES:
        scheduler.add_job(
            run_daily_summary,
            trigger=CronTrigger(hour=hh, minute=mm, timezone=tz),
            id=f"daily_summary_{hh:02d}{mm:02d}",
            name=f"Daily Summary at {hh:02d}:{mm:02d}",
            replace_existing=True,
            misfire_grace_time=300,
        )

    scheduler.start()
    log.info(f"[Scheduler] Started with {len(SCHEDULE_TIMES)} jobs (TZ={TZ_NAME})")
    for hh, mm in SCHEDULE_TIMES:
        log.info(f"  → Daily summary at {hh:02d}:{mm:02d}")
    return scheduler


if __name__ == "__main__":
    # Test รัน daily summary ทันที
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    print("=" * 50)
    print("  Daily Summary Scheduler — Manual Test")
    print("=" * 50)
    run_daily_summary()
