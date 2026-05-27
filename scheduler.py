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

# โหลด .env ก่อนอ่านค่า (สำคัญเมื่อรัน scheduler.py โดยตรงจาก Task Scheduler)
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path, override=False)  # override=False: ไม่ทับถ้ามีค่าอยู่แล้ว
except ImportError:
    pass

# โหลด env vars (อ่านหลัง dotenv)
LOVELY_BOT_TOKEN = os.environ.get("LOVELY_BOT_TOKEN", "").strip()
LINE_TARGET_ID   = os.environ.get("LINE_TARGET_ID", "").strip()
TZ_NAME          = os.environ.get("TZ", "Asia/Bangkok")

# Schedule times — 4 ครั้ง/วัน
SCHEDULE_TIMES = [
    (13, 0),
    (15, 0),
    (18, 0),
    (20, 20),
]


def run_appointment_drx_sync():
    """20:15 — Sync DRX → Google Sheet (ใหม่ทุกเย็น)"""
    log.info("[Scheduler] === Appointment DRX Sync (20:15) ===")
    try:
        import appointment_sync
        appointment_sync.run_drx_sync(fetch_fresh=True)
        log.info("[Scheduler] DRX sync OK")
    except Exception as e:
        log.exception(f"[Scheduler] DRX sync failed: {e}")


def run_appointment_queue_build():
    """13:00 — Build Send_Queue (T+2) → Google Sheet"""
    log.info("[Scheduler] === Appointment Queue Build (13:00) ===")
    try:
        import appointment_sync
        appointment_sync.run_queue_build()
        log.info("[Scheduler] Queue build OK")
    except Exception as e:
        log.exception(f"[Scheduler] Queue build failed: {e}")


def run_appointment_send():
    """18:00 — ส่ง LINE reminders ให้ลูกค้าที่นัด T+2"""
    log.info("[Scheduler] === Appointment Send LINE (18:00) ===")
    try:
        import appointment_sender
        # appointment_sender.main() reads sys.argv — call via wrapper
        # ใช้ subprocess เพื่อหลีกเลี่ยง sys.exit ที่อาจ kill worker
        import sys as _sys
        old_argv = _sys.argv
        _sys.argv = ["appointment_sender.py"]   # no args = production mode
        try:
            appointment_sender.main()
        finally:
            _sys.argv = old_argv
        log.info("[Scheduler] Send LINE OK")
    except SystemExit as e:
        log.warning(f"[Scheduler] Send LINE exited: {e}")
    except Exception as e:
        log.exception(f"[Scheduler] Send LINE failed: {e}")


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

        # 3. ส่ง stock message แยก — เฉพาะรอบ 20:xx (20:20)
        if now.hour == 20:
            stock_msg = line_sender.build_stock_message(data)
            log.info(f"[Scheduler] Stock message ({len(stock_msg)} chars), sending...")
            line_sender.send_line_push(LOVELY_BOT_TOKEN, LINE_TARGET_ID, stock_msg)
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

    # 1) Daily summary jobs
    for hh, mm in SCHEDULE_TIMES:
        scheduler.add_job(
            run_daily_summary,
            trigger=CronTrigger(hour=hh, minute=mm, timezone=tz),
            id=f"daily_summary_{hh:02d}{mm:02d}",
            name=f"Daily Summary at {hh:02d}:{mm:02d}",
            replace_existing=True,
            misfire_grace_time=300,
        )

    # 2) Appointment workflow jobs
    # ⚠️  Queue build และ Send LINE ถูกจัดการโดย Windows Task Scheduler บนเครื่อง PC แล้ว
    #     (DogCatLovely_APPT_Queue_1300 และ DogCatLovely_APPT_Send_1800)
    #     ไม่ต้องรันซ้ำจาก APScheduler — ถ้ารันซ้ำจะส่ง LINE เบิ้ล 2 ครั้ง!
    APPT_JOBS = []   # ว่างไว้ — Task Scheduler จัดการแทน

    scheduler.start()
    total = len(SCHEDULE_TIMES)
    log.info(f"[Scheduler] Started with {total} daily summary jobs (TZ={TZ_NAME})")
    log.info("  ℹ️  Appointment jobs ถูก disable — ใช้ Windows Task Scheduler แทน")
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
