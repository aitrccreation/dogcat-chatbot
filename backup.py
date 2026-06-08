"""
Dog and Cat Lovely — Auto Backup
==================================
สำรองไฟล์สำคัญไว้ใน E:\\AI Dashboard\\backups\\YYYY-MM-DD\\
รันได้เอง หรือให้ Task Scheduler รันทุกวัน

Usage:
    python backup.py           # backup ปกติ
    python backup.py --list    # ดูรายการ backup ที่มี
    python backup.py --restore 2026-05-23   # restore จาก backup วันนั้น
"""

import io
import os
import sys
import json
import shutil
import argparse
from datetime import datetime, timedelta
from pathlib import Path

if sys.stdout and hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE      = Path(__file__).parent
BACKUP_ROOT = BASE / "backups"
KEEP_DAYS   = 7   # เก็บ backup ย้อนหลัง 7 วัน

# ──────────────────────────────────────────
#  ไฟล์ที่ต้อง backup
# ──────────────────────────────────────────
BACKUP_FILES = [
    # ข้อมูลสำคัญ
    "appointments.xlsx",
    "drx_data.json",
    # config & credentials
    ".env",
    "google_creds.json",
    "ngrok.yml",
    # Q&A database
    "qa_database.py",
    "bot_replies.py",
    "fb_replies.json",
    "fb_templates.json",
    # chatbot replies & DB
    "fb_merged.json",
]

# ──────────────────────────────────────────
#  ไดเรกทอรีที่ต้อง backup ทั้งโฟลเดอร์
# ──────────────────────────────────────────
BACKUP_DIRS = [
    "static/images",
]


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def do_backup() -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    dest  = BACKUP_ROOT / today
    dest.mkdir(parents=True, exist_ok=True)

    log = {
        "timestamp": now_str(),
        "date": today,
        "files": [],
        "dirs": [],
        "skipped": [],
        "errors": [],
    }

    print("=" * 52)
    print(f"  🗄️  Dog & Cat Lovely — Backup")
    print(f"  📅 {now_str()}")
    print(f"  📁 {dest}")
    print("=" * 52)
    print()

    # ── backup files ──
    print("[ FILES ]")
    for fname in BACKUP_FILES:
        src = BASE / fname
        if src.exists():
            try:
                shutil.copy2(src, dest / fname)
                size_kb = round(src.stat().st_size / 1024, 1)
                print(f"  ✅ {fname:<40} ({size_kb} KB)")
                log["files"].append({"file": fname, "size_kb": size_kb})
            except Exception as e:
                print(f"  ❌ {fname}: {e}")
                log["errors"].append({"file": fname, "error": str(e)})
        else:
            print(f"  ⏭  {fname:<40} (not found)")
            log["skipped"].append(fname)

    # ── backup dirs ──
    print()
    print("[ DIRECTORIES ]")
    for dname in BACKUP_DIRS:
        src_dir = BASE / dname
        dst_dir = dest / dname
        if src_dir.exists():
            try:
                if dst_dir.exists():
                    shutil.rmtree(dst_dir)
                shutil.copytree(src_dir, dst_dir)
                n_files = len(list(dst_dir.rglob("*.*")))
                total_kb = round(sum(f.stat().st_size for f in dst_dir.rglob("*.*")) / 1024, 1)
                print(f"  ✅ {dname:<40} ({n_files} files, {total_kb} KB)")
                log["dirs"].append({"dir": dname, "files": n_files, "size_kb": total_kb})
            except Exception as e:
                print(f"  ❌ {dname}: {e}")
                log["errors"].append({"dir": dname, "error": str(e)})
        else:
            print(f"  ⏭  {dname:<40} (not found)")

    # ── save backup log ──
    log_file = dest / "backup_log.json"
    log_file.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── cleanup เก่า ──
    print()
    print("[ CLEANUP OLD BACKUPS ]")
    cutoff = datetime.now() - timedelta(days=KEEP_DAYS)
    removed = 0
    for d in sorted(BACKUP_ROOT.iterdir()):
        if d.is_dir() and d.name != today:
            try:
                date_d = datetime.strptime(d.name, "%Y-%m-%d")
                if date_d < cutoff:
                    shutil.rmtree(d)
                    print(f"  🗑️  Removed: {d.name}")
                    removed += 1
                else:
                    print(f"  📁 Keep: {d.name}")
            except ValueError:
                pass  # ไม่ใช่ date folder — ข้าม

    if removed == 0:
        print("  (nothing to remove)")

    # ── summary ──
    print()
    print("=" * 52)
    n_ok  = len(log["files"]) + len(log["dirs"])
    n_err = len(log["errors"])
    print(f"  ✅ Backed up : {n_ok} items")
    if log["skipped"]:
        print(f"  ⏭  Skipped  : {len(log['skipped'])} (not found)")
    if n_err:
        print(f"  ❌ Errors   : {n_err}")
    print(f"  📁 Location : {dest}")
    print("=" * 52)

    return log


def do_list():
    if not BACKUP_ROOT.exists():
        print("ยังไม่มี backup ครับ")
        return

    print("=" * 52)
    print("  📋 รายการ Backup ที่มี")
    print("=" * 52)
    backups = sorted([d for d in BACKUP_ROOT.iterdir() if d.is_dir()], reverse=True)
    if not backups:
        print("  (ยังไม่มี backup)")
        return

    for d in backups:
        log_file = d / "backup_log.json"
        if log_file.exists():
            log = json.loads(log_file.read_text(encoding="utf-8"))
            n_files = len(log.get("files", []))
            n_dirs  = len(log.get("dirs", []))
            ts      = log.get("timestamp", "")
            size_total = sum(f.get("size_kb", 0) for f in log.get("files", []))
            size_total += sum(f.get("size_kb", 0) for f in log.get("dirs", []))
            print(f"  📁 {d.name}  |  {ts}  |  {n_files} files + {n_dirs} dirs  |  {round(size_total,1)} KB")
        else:
            total_size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
            print(f"  📁 {d.name}  |  {round(total_size/1024,1)} KB")
    print()


def do_restore(date_str: str):
    src = BACKUP_ROOT / date_str
    if not src.exists():
        print(f"❌ ไม่พบ backup วันที่ {date_str}")
        do_list()
        return

    print("=" * 52)
    print(f"  ♻️  Restore จาก {date_str}")
    print("=" * 52)

    # backup ปัจจุบันก่อน restore
    print("  (สำรองข้อมูลปัจจุบันก่อน...)")
    do_backup()

    print()
    print("[ RESTORE FILES ]")
    log_file = src / "backup_log.json"
    if log_file.exists():
        log = json.loads(log_file.read_text(encoding="utf-8"))
        for item in log.get("files", []):
            fname = item["file"]
            bak   = src / fname
            if bak.exists():
                shutil.copy2(bak, BASE / fname)
                print(f"  ✅ {fname}")
            else:
                print(f"  ⏭  {fname} (not in backup)")

        for item in log.get("dirs", []):
            dname  = item["dir"]
            bak_d  = src / dname
            dst_d  = BASE / dname
            if bak_d.exists():
                if dst_d.exists():
                    shutil.rmtree(dst_d)
                shutil.copytree(bak_d, dst_d)
                print(f"  ✅ {dname}/")
    else:
        # ถ้าไม่มี log ให้ restore ทุกไฟล์ที่เจอ
        for f in src.rglob("*"):
            if f.is_file() and f.name != "backup_log.json":
                rel = f.relative_to(src)
                dst = BASE / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst)
                print(f"  ✅ {rel}")

    print()
    print(f"  ♻️  Restore จาก {date_str} เสร็จแล้ว!")
    print("=" * 52)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dog & Cat Lovely — Backup Tool")
    parser.add_argument("--list",    action="store_true", help="ดูรายการ backup")
    parser.add_argument("--restore", metavar="DATE",      help="restore จาก backup (YYYY-MM-DD)")
    args = parser.parse_args()

    if args.list:
        do_list()
    elif args.restore:
        do_restore(args.restore)
    else:
        do_backup()
